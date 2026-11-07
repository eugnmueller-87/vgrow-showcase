"""Hank's HTTP side (spec appendix B, frozen with WP8): the handlers, register(app, hank) and journal_wrap.

Every path sits behind the cockpit's key cookie: register() adds the routes to the app make_app() built, whose guard
middleware covers them. The page's language comes in X-Lang (the language middleware puts it into
i18n.REQUEST_LANG); a question's answer is in the question's own language (answer.lang).

Hank is a guest (spec 0.3 rule 2, 10.1 rule 7): a failing handler answers {error} with status 503 and never touches
any other route. A request the handler cannot read answers 400.

journal_wrap(handler, hank) wraps the cockpit's own POST /api/cmd (WP9 registers it): the command runs exactly as
before, with every guard; only afterwards the journal gets a line with the origin (a person, or a Hank card when the
body carries the id of a card Hank handed out). A failure in that bookkeeping is logged and never changes the
command's response. A body with a Hank card id is checked again before the command runs (Hank.precheck_command: the
card still handed out and unchanged, fresh readings, the card rules and the 24 h light rule on the moment of the
tap); a card that may not run now answers 409 with the reason and the command is not called. A command without a
card id is never held back.

| Method and path                  | Answer                                                                  |
|----------------------------------|-------------------------------------------------------------------------|
| GET /api/hank/state              | {mode, light, scene, ollama, chat_model, embed, index, inbox, menu}     |
| POST /api/hank/ask {q, client_id, lang, fix?} | {done: true, answer} or {id, done: false, draft, stage}    |
| GET /api/hank/job/{id}           | {stage, elapsed_s, found, cold, draft, done, answer}                    |
| DELETE /api/hank/job/{id}        | {ok}                                                                    |
| POST /api/hank/clarify {id, answer} | as ask                                                               |
| POST /api/hank/feedback {answer_id, vote} | {ok}                                                           |
| POST /api/hank/proposal {id, outcome} | {ok, message?}                                                     |
| POST /api/hank/mode {mode}       | {mode}                                                                  |
| POST /api/hank/ack {upto}        | {ok}                                                                    |
| GET /api/hank/gaps               | {gaps: [...]}                                                           |
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from aiohttp import web

from vgrow import i18n

from .core import MODES, Hank

_LOGGER = logging.getLogger(__name__)

Handler = Callable[[web.Request], Awaitable[web.StreamResponse]]
OUTCOMES = ("declined",)  # appendix B: the card is dismissed ("Nicht jetzt")
VOTES = ("up", "down")


class BadRequest(Exception):
    """The request cannot be read: 400 with the reason."""


def _error(status: int, message: str) -> web.Response:
    return web.json_response({"error": message}, status=status)


def guarded(call: Callable[[web.Request], Awaitable[Any]]) -> Handler:
    """A handler that answers JSON, 400 for an unreadable request and 503 for anything that breaks inside Hank."""

    async def handle(request: web.Request) -> web.StreamResponse:
        try:
            result = await call(request)
        except web.HTTPException:
            raise
        except BadRequest as err:
            return _error(400, str(err))
        except Exception as err:  # noqa: BLE001 (appendix B: an error in a handler is a 503, never a broken page)
            _LOGGER.exception("Hank handler %s failed", request.path)
            return _error(503, str(err) or type(err).__name__)
        if isinstance(result, web.StreamResponse):
            return result
        return web.json_response(result)

    return handle


async def _body(request: web.Request) -> dict[str, Any]:
    try:
        data = await request.json()
    except Exception as err:  # noqa: BLE001
        raise BadRequest(f"the body is no JSON object: {err}") from err
    if not isinstance(data, dict):
        raise BadRequest("the body is no JSON object")
    return data


def _str(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


FIX_KEYS_MAX = 20  # the problem keys a tapped line or note can carry (a handful in practice)


def _fix_keys(value: Any) -> list[str] | None:
    """The problem keys of a tapped traffic-light line or note ("probe_humidity:high", "stage"), or None."""
    if value is None:
        return None
    if not isinstance(value, list) or len(value) > FIX_KEYS_MAX or not all(
            isinstance(k, str) and 0 < len(k) <= 80 for k in value):
        raise BadRequest(f"fix must be a list of at most {FIX_KEYS_MAX} problem keys")
    return list(value)


def _lang(value: Any) -> str:
    return i18n.normalize(value) or i18n.current_lang()


def register(app: web.Application, hank: Hank) -> None:
    """Add Hank's routes to the cockpit's app (spec appendix B)."""

    async def state(request: web.Request) -> Any:
        return hank.state(i18n.current_lang())

    async def ask(request: web.Request) -> Any:
        body = await _body(request)
        q = body.get("q")
        if q is not None and not isinstance(q, str):
            raise BadRequest("q must be a text")
        return await hank.ask(q or "", client_id=_str(body.get("client_id")), lang=_lang(body.get("lang")),
                              fix=_fix_keys(body.get("fix")))

    async def job(request: web.Request) -> Any:
        found = hank.job(request.match_info["id"])
        if found is None:
            return _error(404, "unknown job")
        return found

    async def cancel(request: web.Request) -> Any:
        if not await hank.cancel(request.match_info["id"]):
            return _error(404, "unknown job")
        return {"ok": True}

    async def clarify(request: web.Request) -> Any:
        body = await _body(request)
        answer = body.get("answer")
        if not isinstance(answer, str | bool):
            raise BadRequest("answer must be yes or no")
        try:
            return await hank.clarify(_str(body.get("id")), answer, client_id=_str(body.get("client_id")),
                                      lang=_lang(body.get("lang")))
        except ValueError as err:
            raise BadRequest(str(err)) from err

    async def feedback(request: web.Request) -> Any:
        body = await _body(request)
        answer_id, vote = _str(body.get("answer_id")), body.get("vote")
        if answer_id is None or vote not in VOTES:
            raise BadRequest("feedback needs answer_id and a vote up or down")
        return hank.feedback(answer_id, str(vote))

    async def proposal(request: web.Request) -> Any:
        body = await _body(request)
        card_id, outcome = _str(body.get("id")), body.get("outcome")
        if card_id is None or outcome not in OUTCOMES:
            raise BadRequest("proposal needs id and the outcome declined")
        return hank.decline(card_id, i18n.current_lang())

    async def mode(request: web.Request) -> Any:
        body = await _body(request)
        if body.get("mode") not in MODES:
            raise BadRequest(f"mode must be one of {', '.join(MODES)}")
        return await hank.set_mode(str(body["mode"]))

    async def ack(request: web.Request) -> Any:
        body = await _body(request)
        upto = body.get("upto")
        if isinstance(upto, bool) or not isinstance(upto, int | float):
            raise BadRequest("upto must be a message id")
        return hank.ack(int(upto))

    async def gaps(request: web.Request) -> Any:
        return hank.gaps()

    app.router.add_get("/api/hank/state", guarded(state))
    app.router.add_post("/api/hank/ask", guarded(ask))
    app.router.add_get("/api/hank/job/{id}", guarded(job))
    app.router.add_delete("/api/hank/job/{id}", guarded(cancel))
    app.router.add_post("/api/hank/clarify", guarded(clarify))
    app.router.add_post("/api/hank/feedback", guarded(feedback))
    app.router.add_post("/api/hank/proposal", guarded(proposal))
    app.router.add_post("/api/hank/mode", guarded(mode))
    app.router.add_post("/api/hank/ack", guarded(ack))
    app.router.add_get("/api/hank/gaps", guarded(gaps))


def journal_wrap(handler: Handler, hank: Hank | None) -> Handler:
    """POST /api/cmd with a journal line afterwards (spec 5.5, 8). The command itself is untouched: same body, same
    guards, same response; an exception of the command goes out unchanged too. aiohttp keeps the read body, so the
    command reads the same bytes."""

    async def wrapped(request: web.Request) -> web.StreamResponse:
        body: Any = None
        if hank is not None:
            try:
                body = await request.json()
            except Exception:  # noqa: BLE001 (the command answers an unreadable body itself)
                body = None
        if hank is not None and isinstance(body, dict) and isinstance(body.get("proposal"), str):
            # a Hank card is checked again at the tap, on the moment of now (core.precheck_command, review 2, S2);
            # the owner's own command (no card id) is never held back here
            try:
                refusal = hank.precheck_command(body)
            except Exception:  # noqa: BLE001 (a card Hank cannot check does not run; the slider still does)
                _LOGGER.exception("Hank card check before /api/cmd failed")
                refusal = i18n.t("hank.cardcheck.gone", i18n.current_lang())
            if refusal:
                return web.json_response({"ok": False, "error": refusal}, status=409)
        response = await handler(request)
        if hank is not None and isinstance(body, dict):
            try:
                hank.record_command(body, response.status)
            except Exception:  # noqa: BLE001 (spec 10.1 rule 7: the journal never breaks a command)
                _LOGGER.exception("Hank journal line for /api/cmd failed")
        return response

    return wrapped

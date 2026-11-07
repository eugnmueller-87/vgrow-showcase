"""Login rules (compliance finding of 22.10.2026): network and server failures back off, a rejected login waits
half an hour, never more than six logins in any rolling hour (restarts included), and every retry carries the same
identifiers, restarts included. An MQTT link the cloud drops at once backs off too, and connections have their own
hourly cap. A fake client and a fake clock: no network, no waiting."""

from __future__ import annotations

import asyncio
import json
import random
import socket
from datetime import UTC, datetime, timedelta

import pytest

from vgrow import box as box_mod
from vgrow import i18n
from vgrow.box import (
    AUTH_RETRY_SECONDS,
    BACKOFF_MAX_SECONDS,
    CONNECT_MAX_PER_HOUR,
    LOGIN_MAX_PER_HOUR,
    LOGIN_WINDOW_SECONDS,
    STABLE_SECONDS,
    SUPERVISE_SECONDS,
    ApiClient,
    Box,
    LoginGate,
    backoff_seconds,
)
from vgrow.demo import DemoBox
from vgrow.i18n import fmt_time
from vgrow.server import serve
from vgrow.vivosun.aws_auth import AwsCredentials
from vgrow.vivosun.const import API_LOGIN_PATH
from vgrow.vivosun.exceptions import VivosunAuthError, VivosunConnectionError, VivosunResponseError
from vgrow.vivosun.models import AuthTokens, AwsIdentity, DeviceInfo

VGROW = DeviceInfo("dev1", "vivosun-VSCTLY18-acct-dev1", "vivosun/VS_COMMON/VSCTLY18/acct/dev1", "VGrow", True, 1, "controller")
START = 1_800_000_000.0  # a fixed fake epoch


class Clock:
    def __init__(self, t: float = START) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


class Cloud:
    """The fake client: VIVOSUN's REST calls and the AWS part. `fail` is what the login raises (None: it works)."""

    def __init__(self, clock: Clock, fail: Exception | None = None, devices: list | None = None,  # type: ignore[type-arg]
                 cognito: Exception | None = None) -> None:
        self.clock, self.fail, self.cognito = clock, fail, cognito
        self.devices = [VGROW] if devices is None else devices
        self.logins: list[float] = []

    async def login(self, email: str, password: str) -> AuthTokens:
        self.logins.append(self.clock())
        if self.fail is not None:
            raise self.fail
        return AuthTokens("access", "login", "refresh", "user")

    async def get_devices(self, tokens: AuthTokens) -> list:  # type: ignore[type-arg]
        return self.devices

    async def get_aws_identity(self, tokens: AuthTokens) -> AwsIdentity:
        return AwsIdentity("iot.example", "us-east-2", "identity", "open-id-token", 443)

    async def get_credentials_for_identity(self, identity: AwsIdentity) -> AwsCredentials:
        if self.cognito is not None:
            raise self.cognito
        return AwsCredentials("AK", "SK", "ST", datetime.now(tz=UTC) + timedelta(hours=1))

    def credentials_need_refresh(self, creds: AwsCredentials) -> bool:
        return False

    def sigv4_sign_mqtt_url(self, **kwargs: object) -> str:
        return "wss://iot.example/mqtt"


class FakeMQTT:
    """Stands in for MQTTClient: notes the client id of every connection; connects unless `down`."""

    ids: list[str] = []
    down = False

    def __init__(self, *, websocket_url: str, thing: str, topic_prefix: str, client_id: str, label: str) -> None:
        FakeMQTT.ids.append(client_id)
        self.is_connected = False

    async def connect(self) -> None:
        if FakeMQTT.down:
            raise OSError("network is unreachable")
        self.is_connected = True

    def add_message_callback(self, callback: object) -> None: ...

    async def subscribe(self, topics: object) -> None: ...

    async def publish(self, topic: str, payload: bytes, qos: int = 0) -> None: ...

    async def disconnect(self) -> None:
        self.is_connected = False


@pytest.fixture
def mqtt(monkeypatch):  # type: ignore[no-untyped-def]
    FakeMQTT.ids, FakeMQTT.down = [], False
    monkeypatch.setattr(box_mod, "MQTTClient", FakeMQTT)
    return FakeMQTT


def make(clock: Clock, cloud: Cloud, seed: int = 1) -> Box:
    box = Box("x@example.test", "pw", clock=clock, rng=random.Random(seed))
    box._api = box._aws = cloud  # type: ignore[assignment]
    return box


def drive(box: Box, clock: Clock, seconds: float) -> None:
    """The supervisor's looks over `seconds` of fake time, each after the wait the previous one asked for."""
    async def run() -> None:
        end, wait = clock.t + seconds, 0.0
        while clock.t + wait <= end:
            clock.t += wait
            wait = await box._supervise_once()

    asyncio.run(run())


def most_in_an_hour(stamps: list[float]) -> int:
    return max((sum(1 for s in stamps if t <= s < t + LOGIN_WINDOW_SECONDS) for t in stamps), default=0)


class Fixed:
    def __init__(self, value: float) -> None:
        self.value = value

    def random(self) -> float:
        return self.value


# ---------- the backoff ----------

def test_backoff_doubles_from_10_seconds_up_to_5_minutes_with_jitter() -> None:
    steps = [10, 20, 40, 80, 160, 300, 300, 300]
    assert [backoff_seconds(n, Fixed(0.0)) for n in range(1, 9)] == steps  # no jitter drawn: the plain steps
    assert [backoff_seconds(n, Fixed(1.0)) for n in range(1, 9)] == pytest.approx([s * 0.8 for s in steps])
    rng = random.Random(7)
    waits = [backoff_seconds(n, rng) for n in range(1, 9)]
    assert all(0.8 * step <= wait <= step for wait, step in zip(waits, steps, strict=True))
    assert len({round(w / s, 6) for w, s in zip(waits, steps, strict=True)}) > 1  # jitter, not a fixed fraction
    assert backoff_seconds(10_000, rng) <= BACKOFF_MAX_SECONDS  # never past the cap, however long it fails


def test_network_failures_back_off_and_stay_under_six_logins_an_hour(mqtt) -> None:  # type: ignore[no-untyped-def]
    clock = Clock()
    cloud = Cloud(clock, VivosunConnectionError("Vivosun API request failed for /user/login"))
    box = make(clock, cloud)
    drive(box, clock, 3 * 3600)
    gaps = [b - a for a, b in zip(cloud.logins, cloud.logins[1:], strict=False)]
    for gap, step in zip(gaps[:5], (10, 20, 40, 80, 160), strict=True):  # the first burst follows the backoff
        assert 0.8 * step <= gap <= step, gaps
    assert most_in_an_hour(cloud.logins) <= LOGIN_MAX_PER_HOUR  # the old loop: about 90
    assert len(cloud.logins) >= 3 * LOGIN_MAX_PER_HOUR - 1  # and it keeps trying, hour after hour
    assert all(gap <= LOGIN_WINDOW_SECONDS for gap in gaps)
    saved = json.loads(LoginGate.path().read_text(encoding="utf-8"))["attempts"]
    assert len(saved) <= LOGIN_MAX_PER_HOUR and max(saved) == cloud.logins[-1]  # only the last hour is kept
    assert FakeMQTT.ids == []  # no login, no connection attempt


def test_while_the_cap_holds_the_page_says_why_and_until_when(mqtt) -> None:  # type: ignore[no-untyped-def]
    clock = Clock()
    cloud = Cloud(clock, VivosunConnectionError("Vivosun API request failed for /user/login"))
    box = make(clock, cloud)
    drive(box, clock, 600)  # the burst: six attempts within about five minutes, then the cap
    assert len(cloud.logins) == LOGIN_MAX_PER_HOUR
    at = cloud.logins[0] + LOGIN_WINDOW_SECONDS
    assert box._retry_at == at
    assert box.last_error == ("Anmeldung: Vivosun API request failed for /user/login. Höchstens 6 Anmeldungen pro "
                              f"Stunde, nächster Versuch um {fmt_time(at, 'de')}")
    token = i18n.REQUEST_LANG.set("en")
    try:
        assert box.last_error == ("Login: Vivosun API request failed for /user/login. At most 6 logins per hour, "
                                  f"next try at {fmt_time(at, 'en')}")
    finally:
        i18n.REQUEST_LANG.reset(token)


def test_server_errors_and_a_failing_aws_step_back_off_like_network_errors(mqtt) -> None:  # type: ignore[no-untyped-def]
    for cloud_error in ({"fail": VivosunResponseError("Vivosun API error: busy")},
                        # The credentials were accepted; an AWS token problem is no reason for a half-hour pause.
                        {"cognito": VivosunAuthError("Cognito authentication failed")}):
        clock = Clock()
        cloud = Cloud(clock, **cloud_error)  # type: ignore[arg-type]
        box = make(clock, cloud)
        wait = asyncio.run(box._supervise_once())
        assert 8 <= wait <= 10 and box._failures == 1, cloud_error
        assert "Nächster Versuch um" in (box.last_error or "") and "30 Minuten" not in (box.last_error or "")


# ---------- a rejected login ----------

def test_a_rejected_login_retries_at_most_every_30_minutes_and_says_what_to_do(mqtt) -> None:  # type: ignore[no-untyped-def]
    clock = Clock()
    cloud = Cloud(clock, VivosunAuthError("Authentication failed: invalid password."))
    box = make(clock, cloud)
    drive(box, clock, 2 * 3600)
    assert cloud.logins == [START + n * AUTH_RETRY_SECONDS for n in range(5)]  # no fast loop in between
    at = START + 5 * AUTH_RETRY_SECONDS
    assert box.last_error == (
        "Anmeldung: VIVOSUN lehnt die Anmeldung ab (Authentication failed: invalid password). Bitte VIVOSUN_EMAIL "
        "und VIVOSUN_PASSWORD in ~/.vgrow.env prüfen und den Server neu starten. Nächster Versuch um "
        f"{fmt_time(at, 'de')}; nach einer Ablehnung höchstens alle 30 Minuten")
    token = i18n.REQUEST_LANG.set("en")
    try:
        assert box.last_error == (
            "Login: VIVOSUN rejects the login (Authentication failed: invalid password). Please check VIVOSUN_EMAIL "
            "and VIVOSUN_PASSWORD in ~/.vgrow.env and restart the server. Next try at "
            f"{fmt_time(at, 'en')}; after a rejection at most every 30 minutes")
    finally:
        i18n.REQUEST_LANG.reset(token)
    assert FakeMQTT.ids == []


def test_an_account_without_a_vgrow_counts_as_rejected(mqtt) -> None:  # type: ignore[no-untyped-def]
    clock = Clock()
    cloud = Cloud(clock, devices=[])
    box = make(clock, cloud)
    drive(box, clock, 3600)
    assert cloud.logins == [START, START + AUTH_RETRY_SECONDS, START + 2 * AUTH_RETRY_SECONDS]
    assert (box.last_error or "").startswith("Anmeldung: Keine VGrow (VSCTLY18) in diesem Vivosun-Konto. "
                                             "Gefunden: nichts. Nächster Versuch um ")


def test_the_supervisor_loop_sleeps_what_each_look_asks_for(mqtt) -> None:  # type: ignore[no-untyped-def]
    clock, waits = Clock(), []

    class Done(Exception):
        pass

    async def sleep(seconds: float) -> None:
        waits.append(seconds)
        clock.t += seconds
        if len(waits) > 3:
            raise Done

    cloud = Cloud(clock, VivosunAuthError("Authentication failed"))
    box = Box("x@example.test", "pw", clock=clock, sleep=sleep, rng=random.Random(1))
    box._api = box._aws = cloud  # type: ignore[assignment]
    with pytest.raises(Done):
        asyncio.run(box._supervise(0))
    assert waits == [0, AUTH_RETRY_SECONDS, AUTH_RETRY_SECONDS, AUTH_RETRY_SECONDS] and len(cloud.logins) == 3


# ---------- the cap across restarts, the recovery ----------

def test_the_hourly_cap_counts_across_restarts(mqtt) -> None:  # type: ignore[no-untyped-def]
    clock = Clock()
    first = Cloud(clock, VivosunConnectionError("down"))
    drive(make(clock, first), clock, 600)
    assert len(first.logins) == LOGIN_MAX_PER_HOUR
    clock.t += 30  # a restart: launchd after a crash, a deploy, a --check next to the server
    second = Cloud(clock)  # this one would work
    box = make(clock, second)
    wait = asyncio.run(box._supervise_once())
    assert second.logins == []  # a new process, the same state folder: the cap still holds
    allowed = first.logins[0] + LOGIN_WINDOW_SECONDS
    assert wait == pytest.approx(allowed - clock.t)
    assert box.last_error == f"Anmeldung: Höchstens 6 Anmeldungen pro Stunde, nächster Versuch um {fmt_time(allowed, 'de')}"
    clock.t += wait
    assert asyncio.run(box._supervise_once()) == SUPERVISE_SECONDS
    assert second.logins == [allowed] and box.last_error is None and len(FakeMQTT.ids) == 1  # in, and connected


def test_the_gate_counts_a_clock_set_back_and_keeps_counting_without_its_file(isolated_state) -> None:  # type: ignore[no-untyped-def]
    clock = Clock()
    gate = LoginGate(clock)
    for n in range(LOGIN_MAX_PER_HOUR):
        gate.record(START + n)
    assert gate.next_allowed(START + 60) == START + LOGIN_WINDOW_SECONDS
    assert gate.next_allowed(START - 600) > START - 600  # the clock went back ten minutes: still capped
    assert gate.next_allowed(START + LOGIN_WINDOW_SECONDS + 1) == START + LOGIN_WINDOW_SECONDS + 1  # aged out
    LoginGate.path().write_text("not json", encoding="utf-8")  # a broken file: this process still counts its own
    assert gate.next_allowed(START + 60) == START + LOGIN_WINDOW_SECONDS


def test_a_working_connection_resets_the_backoff(mqtt) -> None:  # type: ignore[no-untyped-def]
    clock = Clock()
    cloud = Cloud(clock, VivosunConnectionError("down"))
    box = make(clock, cloud)
    drive(box, clock, 100)
    assert box._failures == len(cloud.logins) == 4  # at about 0, 10, 30 and 70 s
    cloud.fail = None  # the network is back
    clock.t = box._retry_at
    assert asyncio.run(box._supervise_once()) == SUPERVISE_SECONDS
    assert box.last_error is None and box._mqtt is not None and box._mqtt.is_connected
    assert box._failures == 4  # in, but only a link that holds counts as recovered
    clock.t += STABLE_SECONDS
    assert asyncio.run(box._supervise_once()) == SUPERVISE_SECONDS and box._failures == 0  # it held
    box._mqtt.is_connected = False  # the connection drops, and the network with it
    FakeMQTT.down = True
    clock.t += SUPERVISE_SECONDS
    wait = asyncio.run(box._supervise_once())
    assert 8 <= wait <= 10  # back to the first step, not where the old streak ended
    assert (box.last_error or "").startswith("Verbindung: network is unreachable. Nächster Versuch um ")


def test_a_link_the_cloud_drops_at_once_backs_off_instead_of_reopening_every_10_seconds(mqtt) -> None:  # type: ignore[no-untyped-def]
    """The review's probe: CONNACK and SUBACK fine, then AWS IoT closes the link (a policy denial on the shadow/get
    publish, say). The old loop reconnected every 10 s: 361 connections in an hour, and nothing on the page."""
    clock = Clock()
    box = make(clock, Cloud(clock))
    times: list[float] = []
    real = box._connect

    async def connect() -> None:
        times.append(clock.t)
        await real()

    box._connect = connect  # type: ignore[method-assign]

    async def hour() -> None:
        end, wait = clock.t + 3500, 0.0
        while clock.t + wait <= end:
            clock.t += wait
            if box._mqtt is not None:
                box._mqtt.is_connected = False  # the cloud closes the link before the next look
            wait = await box._supervise_once()

    asyncio.run(hour())
    assert len(times) <= LOGIN_MAX_PER_HOUR  # each new try goes through the hourly login gate
    gaps = [b - a for a, b in zip(times, times[1:], strict=False)]
    assert len(gaps) >= 4 and all(b > a for a, b in zip(gaps, gaps[1:], strict=False)), gaps  # a growing backoff
    assert box._failures >= 5 and box._retry_at > clock.t
    assert "Die Cloud trennt die Verbindung kurz nach dem Aufbau" in (box.last_error or "")
    token = i18n.REQUEST_LANG.set("en")
    try:
        assert "The cloud closes the connection right after it opens" in (box.last_error or "")
    finally:
        i18n.REQUEST_LANG.reset(token)


def test_never_more_than_12_connections_an_hour(mqtt) -> None:  # type: ignore[no-untyped-def]
    """A link that just outlives the stable time each round would still reconnect at once: the hourly cap holds it."""
    clock = Clock()
    box = make(clock, Cloud(clock))
    asyncio.run(box._supervise_once())
    for _ in range(40):
        clock.t += STABLE_SECONDS  # up long enough to count as stable
        asyncio.run(box._supervise_once())
        box._mqtt.is_connected = False  # type: ignore[union-attr]
        clock.t += 1
        asyncio.run(box._supervise_once())
    assert len(FakeMQTT.ids) == CONNECT_MAX_PER_HOUR
    assert box._retry_at > clock.t and (box.last_error or "").startswith(
        "Verbindung: Höchstens 12 Verbindungen pro Stunde, nächster Versuch um ")


# ---------- the same identifiers on every retry ----------

class Response:
    status = 200

    def __init__(self, payload: dict) -> None:  # type: ignore[type-arg]
        self.payload = payload

    async def __aenter__(self) -> Response:
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False

    async def json(self, content_type: str | None = None) -> dict:  # type: ignore[type-arg]
        return self.payload


class Session:
    """What upstream's login sends, read off the wire."""

    def __init__(self) -> None:
        self.sent: list[tuple[str, dict]] = []  # type: ignore[type-arg]

    def request(self, method: str, url: str, **kwargs: object) -> Response:
        self.sent.append((url, json.loads(kwargs["data"])))  # type: ignore[arg-type]
        return Response({"success": True, "message": "ok", "data": {
            "accessToken": "a", "loginToken": "l", "refreshToken": "r", "userId": "u"}})


def test_every_login_of_a_client_carries_the_same_identifiers() -> None:
    session = Session()
    api = ApiClient(session, sp_client_id="client-1", sp_session_id="session-1")  # type: ignore[arg-type]
    for _ in range(3):
        asyncio.run(api.login("x@example.test", "pw"))  # upstream mints two fresh uuids on every call
    bodies = [body for _, body in session.sent]
    assert all(url.endswith(API_LOGIN_PATH) for url, _ in session.sent) and len(bodies) == 3
    assert {(b["spClientId"], b["spSessionId"]) for b in bodies} == {("client-1", "session-1")}
    assert bodies[0]["email"] == "x@example.test" and bodies[0]["spAppId"]  # upstream's other fields stay as sent
    plain = ApiClient(Session())  # type: ignore[arg-type]
    asyncio.run(plain.login("x@example.test", "pw"))
    asyncio.run(plain.login("x@example.test", "pw"))
    sent = [body for _, body in plain._session.sent]  # type: ignore[attr-defined]
    assert sent[0]["spClientId"] == sent[1]["spClientId"] != "client-1"  # its own pair, also kept


def test_reconnects_keep_one_mqtt_client_id(mqtt) -> None:  # type: ignore[no-untyped-def]
    clock = Clock()
    box = make(clock, Cloud(clock))
    asyncio.run(box._supervise_once())
    clock.t += STABLE_SECONDS  # a link that held, then dropped: reconnected at once
    box._mqtt.is_connected = False  # type: ignore[union-attr]
    asyncio.run(box._supervise_once())
    assert len(FakeMQTT.ids) == 2 and len(set(FakeMQTT.ids)) == 1 and FakeMQTT.ids[0].startswith("vgrow-control-")
    restarted = make(clock, Cloud(clock))  # the server again, after a restart: the same identifiers
    assert (restarted._sp_client_id, restarted._sp_session_id, restarted._mqtt_client_id) == (
        box._sp_client_id, box._sp_session_id, box._mqtt_client_id)
    check = Box("y@example.test", "pw", remember=False)  # --check next to the server: its own, never stored
    assert check._mqtt_client_id != box._mqtt_client_id and check._sp_client_id != box._sp_client_id
    saved = json.loads(Box.login_state_path().read_text(encoding="utf-8"))
    assert saved["mqttClientId"] == box._mqtt_client_id and saved["spSessionId"] == box._sp_session_id


def test_a_restart_keeps_the_pause_after_a_rejected_login(mqtt) -> None:  # type: ignore[no-untyped-def]
    """The review's probe: a wrong password in a launchd crash loop gave 6 rejected logins within about 3 minutes,
    each with new identifiers, instead of one per 30 minutes."""
    clock = Clock()
    cloud = Cloud(clock, VivosunAuthError("Authentication failed: invalid password."))
    first = make(clock, cloud)
    asyncio.run(first._supervise_once())  # rejected: the next try in 30 minutes
    for _ in range(6):  # restarts every 30 s (KeepAlive with ThrottleInterval 30)
        clock.t += 30
        again = make(clock, cloud)
        assert (again._sp_client_id, again._sp_session_id) == (first._sp_client_id, first._sp_session_id)
        assert asyncio.run(again._supervise_once()) > 25 * 60
    assert cloud.logins == [START]  # one rejected login, not one per restart
    assert (again.last_error or "").startswith(
        "Anmeldung: VIVOSUN lehnt die Anmeldung ab (Authentication failed: invalid password). Bitte VIVOSUN_EMAIL")
    token = i18n.REQUEST_LANG.set("en")
    try:  # read back after the restart, still said in the reader's language
        assert (again.last_error or "").startswith("Login: VIVOSUN rejects the login (Authentication failed")
    finally:
        i18n.REQUEST_LANG.reset(token)
    clock.t = START + AUTH_RETRY_SECONDS
    cloud.fail = None  # the password is fixed
    assert asyncio.run(again._supervise_once()) == SUPERVISE_SECONDS and cloud.logins[-1] == clock.t
    assert json.loads(Box.login_state_path().read_text(encoding="utf-8"))["auth_retry_at"] is None
    assert make(clock, cloud)._retry_at == 0.0  # the next start does not wait


def test_a_start_that_cannot_bind_its_port_never_logs_in() -> None:
    """Under launchd a taken port meant a crash loop, and each start logged in first; now the port comes first."""
    class Counting(DemoBox):
        demo = False  # the real start path, without the demo's own state folder
        starts = 0

        async def start(self) -> None:
            Counting.starts += 1
            await super().start()

    async def run() -> None:
        with socket.socket() as taken:
            taken.bind(("127.0.0.1", 0))
            taken.listen()
            with pytest.raises(OSError):
                await serve(Counting(), port=taken.getsockname()[1], host="127.0.0.1")

    asyncio.run(run())
    assert Counting.starts == 0


def test_a_failed_first_login_does_not_stop_the_server(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    async def down(self: ApiClient, email: str, password: str) -> AuthTokens:
        raise VivosunConnectionError("Vivosun API request failed for /user/login")

    monkeypatch.setattr(ApiClient, "login", down)
    box = Box("x@example.test", "pw")
    seen: list[dict] = []  # type: ignore[type-arg]

    async def listener(b: Box) -> None:
        seen.append(b.snapshot())

    box.listeners.append(listener)

    async def run() -> None:
        await box.start()  # raised before: serve() never served, and launchd restarted it every 30 s
        try:
            assert [task.get_name() for task in box._tasks] == ["vgrow_supervise", "vgrow_poll", "vgrow_shadow_refresh"]
            assert box._api is not None and box._api.login_ids == {"spClientId": box._sp_client_id,
                                                                    "spSessionId": box._sp_session_id}
        finally:
            await box.stop()
        assert all(task.done() for task in box._tasks)

    asyncio.run(run())
    assert len(seen) == 1 and seen[0]["online"] is False and seen[0]["sensors_age"] is None
    assert seen[0]["error"].startswith("Anmeldung: Vivosun API request failed for /user/login. Nächster Versuch um ")
    assert box._errors["sensors"].text("de") == "Noch nicht bei VIVOSUN angemeldet"  # no AssertionError on the page
    assert len(json.loads(LoginGate.path().read_text(encoding="utf-8"))["attempts"]) == 1  # counted

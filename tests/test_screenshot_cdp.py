"""An iframe interception may reply to the outer command before its nested fulfill command."""
import json
from collections import deque

import pytest

from scripts.screenshots import Cdp


class Socket:
    def __init__(self, outer):
        self.messages = deque([
            {"method": "Fetch.requestPaused", "params": {"requestId": "frame"}},
            outer,
            {"id": 2, "result": {"fulfilled": True}},
        ])
        self.sent = []

    def send(self, value):
        self.sent.append(json.loads(value))

    def recv(self):
        # A broken client would block forever here after discarding the outer reply.
        assert self.messages, "Client lost a command response during nested event handling"
        return json.dumps(self.messages.popleft())


def test_nested_iframe_interception_preserves_the_outer_reply():
    socket = Socket({"id": 1, "result": {"navigated": True}})
    client = Cdp(socket)
    nested = []
    client.handlers["Fetch.requestPaused"] = lambda event: nested.append(client.cmd("Fetch.fulfillRequest", **event))
    assert client.cmd("Page.navigate", url="http://localhost/") == {"navigated": True}
    assert nested == [{"fulfilled": True}]
    assert [m["method"] for m in socket.sent] == ["Page.navigate", "Fetch.fulfillRequest"]
    assert not client.responses


def test_outer_errors_are_also_preserved_during_interception():
    client = Cdp(Socket({"id": 1, "error": {"message": "navigation failed"}}))
    client.handlers["Fetch.requestPaused"] = lambda event: client.cmd("Fetch.fulfillRequest", **event)
    with pytest.raises(RuntimeError, match="navigation failed"):
        client.cmd("Page.navigate", url="http://localhost/")

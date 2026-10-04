"""The session-bus notification client: wire format, and a whole conversation with a fake bus."""
import os
import socket
import struct
import threading

import pytest

from lapbar import notify


def test_signatures_split_into_complete_types():
    assert notify.split_signature("susssasa{sv}i") == ["s", "u", "s", "s", "s", "as", "a{sv}", "i"]
    assert notify.split_signature("a(yv)") == ["a(yv)"]


def test_a_message_survives_a_round_trip_through_the_wire_format():
    fields = {notify.PATH: ("o", "/org/freedesktop/Notifications"), notify.MEMBER: ("s", "Notify")}
    body = ("lapbar", 0, "", "Title ✓", "Body <b>x</b>", ["default", "View"], {"urgency": ("y", 1)}, 20000)
    message = notify.decode_message(notify.encode_message(7, notify.METHOD_CALL, fields, "susssasa{sv}i", body))
    assert message["type"] == notify.METHOD_CALL and message["serial"] == 7
    assert message["fields"][notify.MEMBER] == "Notify" and message["fields"][notify.SIGNATURE] == "susssasa{sv}i"
    assert message["body"] == body


def test_a_big_endian_message_is_read_too():
    # body "u" = 5, header: endian B, type 4 (signal), flags 0, version 1, body length 4, serial 1, fields: signature "u"
    fields = b"\x08\x01g\x00\x01u\x00"                       # (y=8, v=(g "u")): code, sig "g", then the signature value
    header = b"B\x04\x00\x01" + struct.pack(">II", 4, 1) + struct.pack(">I", len(fields)) + fields
    header += b"\0" * (-len(header) % 8)
    message = notify.decode_message(header + struct.pack(">I", 5))
    assert message["type"] == notify.SIGNAL and message["body"] == (5,)


def test_addresses_are_parsed_for_paths_and_abstract_sockets(monkeypatch, tmp_path):
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    path = tmp_path / "bus"
    server.bind(str(path))
    server.listen(1)
    try:
        sock = notify.open_socket(f"tcp:host=x,port=1;unix:path={str(path).replace('/', '%2f')},guid=abc")
        sock.close()
        with pytest.raises(notify.DBusError):
            notify.open_socket(f"unix:path={tmp_path / 'missing'}")
    finally:
        server.close()
    abstract = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    name = f"lapbar-test-{os.getpid()}"
    abstract.bind("\0" + name)
    abstract.listen(1)
    try:
        notify.open_socket(f"unix:abstract={name},guid=abc").close()
    finally:
        abstract.close()


class FakeBus:
    """The bus side of the conversation, on the other end of a socket pair, run in a thread."""

    def __init__(self, sock, outcome):
        self.sock = sock
        self.outcome = outcome            # ("action", key) / ("closed",) / ("error",)
        self.auth_line = b""
        self.calls = []
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()

    def read_line(self):
        line = b""
        while not line.endswith(b"\r\n"):
            line += self.sock.recv(1)
        return line

    def read_message(self):
        start = self._recv(16)
        body_length, fields_length = struct.unpack("<I", start[4:8])[0], struct.unpack("<I", start[12:16])[0]
        total = 16 + fields_length + (-(16 + fields_length) % 8) + body_length
        return notify.decode_message(start + self._recv(total - 16))

    def _recv(self, n):
        data = b""
        while len(data) < n:
            data += self.sock.recv(n - len(data))
        return data

    def reply(self, to, sig="", body=()):
        self.sock.sendall(notify.encode_message(100 + to["serial"], notify.METHOD_RETURN,
                                                {notify.REPLY_SERIAL: ("u", to["serial"])}, sig, body))

    def signal(self, member, sig, body):
        self.sock.sendall(notify.encode_message(900, notify.SIGNAL, {
            notify.PATH: ("o", notify.NOTIFY_PATH), notify.INTERFACE: ("s", notify.NOTIFY_NAME),
            notify.MEMBER: ("s", member)}, sig, body))

    def run(self):
        self.auth_line = self.read_line()
        self.sock.sendall(b"OK 1234567890abcdef\r\n")
        assert self.read_line() == b"BEGIN\r\n"
        while True:
            message = self.read_message()
            member = message["fields"][notify.MEMBER]
            self.calls.append((member, message["body"]))
            if member == "Hello":
                self.reply(message, "s", (":1.42",))
                self.signal("NameAcquired", "s", (":1.42",))     # unrelated signal: must be ignored
            elif member == "AddMatch":
                self.reply(message)
            elif member == "Notify":
                if self.outcome[0] == "error":
                    self.sock.sendall(notify.encode_message(
                        500, notify.ERROR, {notify.REPLY_SERIAL: ("u", message["serial"]),
                                            notify.ERROR_NAME: ("s", "org.freedesktop.DBus.Error.ServiceUnknown")},
                        "s", ("no notification daemon",)))
                    return
                self.reply(message, "u", (31,))
                self.signal("ActionInvoked", "us", (30, "someone-elses"))   # another app's popup: ignored
                if self.outcome[0] == "action":
                    self.signal("ActionInvoked", "us", (31, self.outcome[1]))
                else:
                    self.signal("NotificationClosed", "uu", (31, 2))
                return


def converse(outcome, **kw):
    ours, theirs = socket.socketpair()
    bus = FakeBus(theirs, outcome)
    try:
        result = notify.show("Kudos from Private Person", "a private remark", [("default", "View on Strava")],
                             timeout_ms=5000, sock=ours, **kw)
    finally:
        bus.thread.join(timeout=5)
        theirs.close()
    return result, bus


def test_a_click_returns_the_action_key_and_the_text_goes_in_the_notify_call():
    result, bus = converse(("action", "default"))
    assert result == "default"
    assert bus.auth_line == b"\0AUTH EXTERNAL " + str(os.getuid()).encode().hex().encode() + b"\r\n"
    assert [member for member, _ in bus.calls] == ["Hello", "AddMatch", "Notify"]   # subscribed before showing
    app, replaces, icon, title, body, actions, hints, timeout = bus.calls[2][1]
    assert (app, title, body, timeout) == ("lapbar", "Kudos from Private Person", "a private remark", 5000)
    assert actions == ["default", "View on Strava"] and hints == {"urgency": ("y", 1)}


def test_closing_the_popup_returns_none():
    assert converse(("closed",))[0] is None


def test_no_notification_daemon_is_a_dbus_error():
    with pytest.raises(notify.DBusError, match="ServiceUnknown"):
        converse(("error",))


def test_a_refused_login_or_a_vanished_bus_is_a_dbus_error():
    ours, theirs = socket.socketpair()
    theirs.sendall(b"REJECTED EXTERNAL\r\n")
    with pytest.raises(notify.DBusError, match="refused"):
        notify.show("t", "b", sock=ours)
    ours, theirs = socket.socketpair()
    theirs.close()
    with pytest.raises(notify.DBusError):
        notify.show("t", "b", sock=ours)


def test_an_absurdly_large_message_from_the_bus_is_refused_before_it_is_read():
    ours, theirs = socket.socketpair()
    theirs.sendall(b"OK 1234\r\n")
    header = b"l\x02\x00\x01" + struct.pack("<II", notify.MAX_MESSAGE, 1) + struct.pack("<I", 0)
    theirs.sendall(header)
    with pytest.raises(notify.DBusError, match="oversized"):
        notify.show("t", "b", sock=ours)
    theirs.close()


def test_the_client_starts_no_process_at_all():
    source = (notify.__file__ and open(notify.__file__).read())
    assert "subprocess" not in source and "os.system" not in source and "exec" not in source.replace("execute", "")

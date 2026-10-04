"""Desktop notifications sent straight to the session bus, not through `notify-send`.

`notify-send` takes the title and body as command-line arguments, and a command line sits in the process table
(`ps`, /proc/<pid>/cmdline) for anyone on the machine to read for as long as the process runs -- which, for a
popup waiting on a click, is until it is dismissed. These popups carry other people's names, the text of their
comments, activity titles and fitness reasons, so they go to org.freedesktop.Notifications over the user's own
session bus socket instead: the text only ever travels through this process's memory and that private socket.

This is a deliberately small D-Bus client using only the standard library (LapBar has no dependencies): SASL
EXTERNAL authentication on the socket, the wire format for the handful of types the Notifications interface
uses, one Notify call, and waiting for that notification's ActionInvoked or NotificationClosed signal. It is the
same conversation `notify-send --action ... --wait` has with the notification daemon, minus the argv.

Reference: https://dbus.freedesktop.org/doc/dbus-specification.html and
https://specifications.freedesktop.org/notification-spec/latest/protocol.html
"""
import os
import socket
import struct
import time
from urllib.parse import unquote

BUS_NAME = "org.freedesktop.DBus"
BUS_PATH = "/org/freedesktop/DBus"
NOTIFY_NAME = "org.freedesktop.Notifications"
NOTIFY_PATH = "/org/freedesktop/Notifications"
URGENCY_NORMAL = 1

MAX_MESSAGE = 1 << 20          # a reply or signal from the bus is a few hundred bytes; refuse anything absurd
METHOD_CALL, METHOD_RETURN, ERROR, SIGNAL = 1, 2, 3, 4
# header field codes
PATH, INTERFACE, MEMBER, ERROR_NAME, REPLY_SERIAL, DESTINATION, SENDER, SIGNATURE = 1, 2, 3, 4, 5, 6, 7, 8

_FIXED = {"y": ("B", 1), "b": ("I", 4), "n": ("h", 2), "q": ("H", 2), "i": ("i", 4), "u": ("I", 4),
          "x": ("q", 8), "t": ("Q", 8), "d": ("d", 8), "h": ("I", 4)}
_ALIGN = {**{c: size for c, (_, size) in _FIXED.items()}, "s": 4, "o": 4, "g": 1, "a": 4, "(": 8, "{": 8, "v": 1}


class DBusError(Exception):
    """The bus or the notification daemon refused, or did not answer in time."""


# ------------------------------------------------------------------ wire format

def _type_end(sig: str, i: int) -> int:
    c = sig[i]
    if c == "a":
        return _type_end(sig, i + 1)
    if c in "({":
        close = ")" if c == "(" else "}"
        i += 1
        while sig[i] != close:
            i = _type_end(sig, i)
        return i + 1
    return i + 1


def split_signature(sig: str) -> list[str]:
    """`susa{sv}` -> ["s", "u", "s", "a{sv}"]: one entry per complete type."""
    out, i = [], 0
    while i < len(sig):
        end = _type_end(sig, i)
        out.append(sig[i:end])
        i = end
    return out


class _Writer:
    """Little-endian marshalling; offsets are relative to where the buffer starts, which is always 8-aligned."""

    def __init__(self):
        self.buf = bytearray()

    def pad(self, n: int):
        self.buf += b"\0" * (-len(self.buf) % n)

    def write(self, sig: str, value):
        c = sig[0]
        if c in _FIXED:
            fmt, size = _FIXED[c]
            self.pad(size)
            self.buf += struct.pack("<" + fmt, value)
        elif c in "so":
            data = value.encode()
            self.pad(4)
            self.buf += struct.pack("<I", len(data)) + data + b"\0"
        elif c == "g":
            data = value.encode()
            self.buf += bytes([len(data)]) + data + b"\0"
        elif c == "v":
            inner_sig, inner = value
            self.write("g", inner_sig)
            self.write(inner_sig, inner)
        elif c == "a":
            element = sig[1:]
            self.pad(4)
            length_at = len(self.buf)
            self.buf += b"\0\0\0\0"
            self.pad(_ALIGN[element[0]])
            start = len(self.buf)
            for item in (value.items() if element[0] == "{" else value):
                self.write(element, item)
            struct.pack_into("<I", self.buf, length_at, len(self.buf) - start)
        elif c in "({":
            self.pad(8)
            for item_sig, item in zip(split_signature(sig[1:-1]), value):
                self.write(item_sig, item)
        else:
            raise ValueError(f"unsupported D-Bus type {c!r}")


class _Reader:
    """Unmarshalling of one whole message; alignment is relative to the message's first byte, as the spec says."""

    def __init__(self, data: bytes, pos: int = 0):
        self.data = data
        self.pos = pos
        self.order = "<" if data[0:1] == b"l" else ">"

    def _take(self, n: int) -> bytes:
        if self.pos + n > len(self.data):
            raise DBusError("truncated D-Bus message")
        chunk = self.data[self.pos:self.pos + n]
        self.pos += n
        return chunk

    def pad(self, n: int):
        self.pos += -self.pos % n

    def read(self, sig: str):
        c = sig[0]
        if c in _FIXED:
            fmt, size = _FIXED[c]
            self.pad(size)
            return struct.unpack(self.order + fmt, self._take(size))[0]
        if c in "so":
            length = self.read("u")
            value = self._take(length + 1)[:-1]
            return value.decode("utf-8", "replace")
        if c == "g":
            length = self._take(1)[0]
            return self._take(length + 1)[:-1].decode("ascii", "replace")
        if c == "v":
            inner_sig = self.read("g")
            return inner_sig, self.read(inner_sig)
        if c == "a":
            length = self.read("u")
            element = sig[1:]
            self.pad(_ALIGN[element[0]])
            end = self.pos + length
            if end > len(self.data):
                raise DBusError("truncated D-Bus message")
            items = []
            while self.pos < end:
                items.append(self.read(element))
            return dict(items) if element[0] == "{" else items
        if c in "({":
            self.pad(8)
            return tuple(self.read(item_sig) for item_sig in split_signature(sig[1:-1]))
        raise DBusError(f"unsupported D-Bus type {c!r}")


def encode_message(serial: int, kind: int, fields: dict[int, tuple[str, object]], body_sig: str = "",
                   body: tuple = ()) -> bytes:
    body_writer = _Writer()
    for item_sig, item in zip(split_signature(body_sig), body):
        body_writer.write(item_sig, item)
    fields = dict(fields)
    if body_sig:
        fields[SIGNATURE] = ("g", body_sig)
    header = _Writer()
    header.buf += b"l" + bytes([kind, 0, 1])
    header.write("u", len(body_writer.buf))
    header.write("u", serial)
    header.write("a(yv)", [(code, value) for code, value in fields.items()])
    header.pad(8)
    return bytes(header.buf + body_writer.buf)


def decode_message(data: bytes) -> dict:
    """{"type", "serial", "fields": {code: value}, "body": tuple}."""
    reader = _Reader(data)
    kind = data[1]
    reader.pos = 8
    serial = reader.read("u")
    fields = {code: value for code, (_, value) in reader.read("a(yv)")}
    reader.pad(8)
    body = tuple(reader.read(item_sig) for item_sig in split_signature(fields.get(SIGNATURE, "")))
    return {"type": kind, "serial": serial, "fields": fields, "body": body}


# ------------------------------------------------------------------ the connection

def session_bus_address() -> str:
    return os.environ.get("DBUS_SESSION_BUS_ADDRESS") or f"unix:path={os.environ.get('XDG_RUNTIME_DIR', f'/run/user/{os.getuid()}')}/bus"


def open_socket(address: str | None = None) -> socket.socket:
    """Connect to the first usable unix-socket address in a D-Bus address list."""
    problems = []
    for entry in (address or session_bus_address()).split(";"):
        transport, _, params = entry.partition(":")
        if transport != "unix":
            continue
        options = dict(p.split("=", 1) for p in params.split(",") if "=" in p)
        if "path" in options:
            target = unquote(options["path"])
        elif "abstract" in options:
            target = "\0" + unquote(options["abstract"])
        else:
            continue
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM | socket.SOCK_CLOEXEC)
        try:
            sock.connect(target)
            return sock
        except OSError as e:
            sock.close()
            problems.append(str(e))
    raise DBusError("no reachable session bus" + (f" ({'; '.join(problems)})" if problems else ""))


class Connection:
    def __init__(self, sock: socket.socket, timeout: float = 10.0):
        self.sock = sock
        self.serial = 0
        self.buffer = b""
        self.pending: list[dict] = []    # signals that arrived while waiting for a reply
        sock.settimeout(timeout)
        self._authenticate()
        self.call(BUS_NAME, BUS_PATH, BUS_NAME, "Hello")

    def close(self):
        self.sock.close()

    def _sendall(self, data: bytes):
        try:
            self.sock.sendall(data)
        except OSError as e:
            raise DBusError(f"the session bus connection failed ({e})") from e

    def _recv(self, n: int) -> bytes:
        while len(self.buffer) < n:
            try:
                chunk = self.sock.recv(65536)
            except TimeoutError as e:
                raise DBusError("the session bus did not answer in time") from e
            except OSError as e:
                raise DBusError(f"the session bus connection failed ({e})") from e
            if not chunk:
                raise DBusError("the session bus closed the connection")
            self.buffer += chunk
        data, self.buffer = self.buffer[:n], self.buffer[n:]
        return data

    def _authenticate(self):
        uid = str(os.getuid()).encode().hex().encode()
        self._sendall(b"\0AUTH EXTERNAL " + uid + b"\r\n")
        line = b""
        while not line.endswith(b"\r\n"):
            line += self._recv(1)
            if len(line) > 512:
                raise DBusError("unexpected answer from the session bus")
        if not line.startswith(b"OK "):
            raise DBusError("the session bus refused the connection")
        self._sendall(b"BEGIN\r\n")

    def send(self, kind: int, fields: dict, body_sig: str = "", body: tuple = ()) -> int:
        self.serial += 1
        self._sendall(encode_message(self.serial, kind, fields, body_sig, body))
        return self.serial

    def receive(self) -> dict:
        start = self._recv(16)
        if start[0:1] not in (b"l", b"B"):
            raise DBusError("malformed D-Bus message")
        order = "<" if start[0:1] == b"l" else ">"
        body_length, fields_length = struct.unpack(order + "I", start[4:8])[0], struct.unpack(order + "I", start[12:16])[0]
        header_length = 16 + fields_length + (-(16 + fields_length) % 8)
        total = header_length + body_length
        if total > MAX_MESSAGE:
            raise DBusError("oversized D-Bus message")
        return decode_message(start + self._recv(total - 16))

    def call(self, destination: str, path: str, interface: str, member: str, body_sig: str = "", body: tuple = ()):
        serial = self.send(METHOD_CALL, {PATH: ("o", path), INTERFACE: ("s", interface), MEMBER: ("s", member),
                                         DESTINATION: ("s", destination)}, body_sig, body)
        while True:
            message = self.receive()
            if message["type"] == SIGNAL:
                self.pending.append(message)
            elif message["fields"].get(REPLY_SERIAL) == serial:
                if message["type"] == ERROR:
                    detail = message["body"][0] if message["body"] else ""
                    raise DBusError(f"{message['fields'].get(ERROR_NAME, 'error')}: {detail}")
                return message["body"]

    def next_signal(self) -> dict:
        if self.pending:
            return self.pending.pop(0)
        while True:
            message = self.receive()
            if message["type"] == SIGNAL:
                return message


# ------------------------------------------------------------------ the one thing LapBar needs

def show(title: str, body: str, actions: list[tuple[str, str]] = (), timeout_ms: int = 20000,
         app_name: str = "lapbar", sock: socket.socket | None = None) -> str | None:
    """Show a popup and wait for it: returns the key of the action clicked, or None once it is closed or times out.

    The same result `notify-send -a <app> -u normal -t <ms> -A key=label ... <title> <body>` prints, without
    the title and body ever being a command-line argument. Raises DBusError if there is no bus or no
    notification daemon."""
    deadline = time.monotonic() + timeout_ms / 1000 + 30
    conn = Connection(sock or open_socket())
    try:
        # Subscribe before showing it, so a very quick click can never be missed.
        conn.call(BUS_NAME, BUS_PATH, BUS_NAME, "AddMatch", "s",
                  (f"type='signal',sender='{NOTIFY_NAME}',interface='{NOTIFY_NAME}',path='{NOTIFY_PATH}'",))
        flat_actions = [part for key, label in actions for part in (key, label)]
        (notification_id,) = conn.call(NOTIFY_NAME, NOTIFY_PATH, NOTIFY_NAME, "Notify", "susssasa{sv}i",
                                       (app_name, 0, "", title, body, flat_actions,
                                        {"urgency": ("y", URGENCY_NORMAL)}, timeout_ms))
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            conn.sock.settimeout(remaining)
            try:
                signal = conn.next_signal()
            except DBusError:
                if time.monotonic() >= deadline:
                    return None
                raise
            member = signal["fields"].get(MEMBER)
            if not signal["body"] or signal["body"][0] != notification_id:
                continue
            if member == "ActionInvoked":
                return signal["body"][1]
            if member == "NotificationClosed":
                return None
    finally:
        conn.close()

"""Minimal Minecraft RCON client with per-call timeouts and fragmented-response support."""

from __future__ import annotations

import socket
import struct
import threading
import time

AUTH = 3
EXEC = 2


class RconError(Exception):
    pass


def encode_packet(request_id: int, packet_type: int, payload: str) -> bytes:
    body = struct.pack("<ii", request_id, packet_type) + payload.encode("utf-8") + b"\0\0"
    return struct.pack("<i", len(body)) + body


MAX_PAYLOAD = 4096
MAX_RESPONSE = 1024 * 1024


class RconClient:
    """One authenticated connection. ``command`` returns the full response text.

    The vanilla server reads exactly one packet per socket read and drops the connection if a
    read holds more, so requests are strictly one at a time with no end-marker packet. A
    response longer than 4096 bytes arrives split into several packets with the same ID.
    """

    def __init__(self, host: str, port: int, password: str, timeout_s: float = 10.0):
        self._lock = threading.Lock()
        self._next_id = 10
        self.timeout_s = timeout_s
        try:
            self._socket = socket.create_connection((host, port), timeout=timeout_s)
        except OSError as error:
            raise RconError(f"cannot connect to RCON {host}:{port}: {error}") from error
        self._socket.settimeout(timeout_s)
        self._send(1, AUTH, password)
        request_id, _type, _payload = self._receive()
        if request_id != 1:
            self.close()
            raise RconError("RCON authentication failed")

    def _send(self, request_id: int, packet_type: int, payload: str) -> None:
        try:
            self._socket.sendall(encode_packet(request_id, packet_type, payload))
        except OSError as error:
            raise RconError(f"RCON send failed: {error}") from error

    def _read_exact(self, count: int) -> bytes:
        data = bytearray()
        while len(data) < count:
            try:
                chunk = self._socket.recv(count - len(data))
            except socket.timeout as error:
                raise RconError(f"RCON timed out after {self.timeout_s}s") from error
            except OSError as error:
                raise RconError(f"RCON receive failed: {error}") from error
            if not chunk:
                raise RconError("RCON connection closed")
            data.extend(chunk)
        return bytes(data)

    def _receive(self) -> tuple[int, int, bytes]:
        length = struct.unpack("<i", self._read_exact(4))[0]
        if length < 10 or length > 1 << 20:
            raise RconError(f"RCON packet length {length} is invalid")
        data = self._read_exact(length)
        request_id, packet_type = struct.unpack("<ii", data[:8])
        return request_id, packet_type, data[8:-2]

    def command(self, text: str) -> str:
        """Send one command; ``timeout_s`` bounds the whole exchange, not just each read."""
        with self._lock:
            deadline = time.monotonic() + self.timeout_s
            command_id = self._next_id
            self._next_id += 1
            self._send(command_id, EXEC, text)
            parts: list[bytes] = []
            total = 0
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise RconError(f"RCON response exceeded {self.timeout_s}s")
                self._socket.settimeout(remaining)
                request_id, _type, payload = self._receive()
                if request_id == -1:
                    raise RconError("RCON session is no longer authenticated")
                if request_id != command_id:
                    continue
                total += len(payload)
                if total > MAX_RESPONSE:
                    raise RconError(f"RCON response exceeded {MAX_RESPONSE} bytes")
                parts.append(payload)
                if len(payload) < MAX_PAYLOAD:
                    return b"".join(parts).decode("utf-8", "replace")

    def close(self) -> None:
        """Safe from another thread: shutting the socket down unblocks a pending read."""
        for action in (lambda: self._socket.shutdown(socket.SHUT_RDWR), self._socket.close):
            try:
                action()
            except OSError:
                pass

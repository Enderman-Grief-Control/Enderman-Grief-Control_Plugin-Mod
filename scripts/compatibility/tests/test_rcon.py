import socket
import struct
import threading
import time
import unittest

from egc_compat.rcon import MAX_PAYLOAD, MAX_RESPONSE, RconClient, RconError


class FakeVanillaRcon:
    """Mimics the vanilla server: one packet per read, connection dropped if a read holds more."""

    def __init__(self, password: str, responses: dict[str, str]):
        self.password = password
        self.responses = responses
        self.listener = socket.socket()
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen(1)
        self.port = self.listener.getsockname()[1]
        self.thread = threading.Thread(target=self.serve, daemon=True)
        self.thread.start()

    def send(self, connection: socket.socket, request_id: int, payload: bytes) -> None:
        body = struct.pack("<ii", request_id, 0) + payload + b"\0\0"
        connection.sendall(struct.pack("<i", len(body)) + body)

    def serve(self) -> None:
        try:
            connection, _ = self.listener.accept()
        except OSError:
            return
        with connection, self.listener:
            try:
                self.handle(connection)
            except OSError:
                pass  # the client hung up, for example after hitting its own limit

    def handle(self, connection: socket.socket) -> None:
        authed = False
        while True:
            data = connection.recv(1460)
            if len(data) < 10 or struct.unpack("<i", data[:4])[0] != len(data) - 4:
                return
            request_id, packet_type = struct.unpack("<ii", data[4:12])
            payload = data[12:-2].decode()
            if packet_type == 3:
                authed = payload == self.password
                self.send(connection, request_id if authed else -1, b"")
            elif packet_type == 2 and authed:
                text = self.responses.get(payload, "").encode()
                for start in range(0, max(len(text), 1), MAX_PAYLOAD):
                    self.send(connection, request_id, text[start:start + MAX_PAYLOAD])
            else:
                self.send(connection, request_id, b"Unknown request")


class RconTest(unittest.TestCase):
    def test_command_round_trip(self):
        server = FakeVanillaRcon("secret", {"time query gametime": "The time is 42"})
        client = RconClient("127.0.0.1", server.port, "secret", timeout_s=2)
        self.assertEqual(client.command("time query gametime"), "The time is 42")
        client.close()

    def test_split_response_is_reassembled(self):
        long_text = "x" * (MAX_PAYLOAD + 100)
        server = FakeVanillaRcon("secret", {"long": long_text})
        client = RconClient("127.0.0.1", server.port, "secret", timeout_s=2)
        self.assertEqual(client.command("long"), long_text)
        client.close()

    def test_bad_password_is_rejected(self):
        server = FakeVanillaRcon("secret", {})
        with self.assertRaises(RconError):
            RconClient("127.0.0.1", server.port, "wrong", timeout_s=2)

    def test_endless_response_hits_the_size_cap_or_deadline(self):
        server = FakeVanillaRcon("secret", {"flood": "x" * (MAX_RESPONSE + MAX_PAYLOAD * 4)})
        client = RconClient("127.0.0.1", server.port, "secret", timeout_s=5)
        with self.assertRaises(RconError):
            client.command("flood")
        client.close()

    def test_silent_server_times_out_within_the_command_limit(self):
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        self.addCleanup(listener.close)

        def accept_and_ignore():
            connection, _ = listener.accept()
            with connection:
                data = connection.recv(1460)
                request_id = struct.unpack("<i", data[4:8])[0]
                body = struct.pack("<ii", request_id, 2) + b"\0\0"
                connection.sendall(struct.pack("<i", len(body)) + body)  # authenticate, then go silent
                time.sleep(2)

        threading.Thread(target=accept_and_ignore, daemon=True).start()
        client = RconClient("127.0.0.1", listener.getsockname()[1], "secret", timeout_s=0.5)
        started = time.monotonic()
        with self.assertRaises(RconError):
            client.command("list")
        self.assertLess(time.monotonic() - started, 3)
        client.close()

    def test_closed_port_is_an_error(self):
        probe = socket.socket()
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
        probe.close()
        with self.assertRaises(RconError):
            RconClient("127.0.0.1", port, "secret", timeout_s=1)


if __name__ == "__main__":
    unittest.main()

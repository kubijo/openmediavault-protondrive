"""Bounded, length-prefixed Protobuf IPC over a private Unix socket."""

import socket
import struct
from pathlib import Path

from .v1 import control_pb2 as wire

LIMIT = 4 * 1024 * 1024
SOCKET = Path('/run/omv-protondrive-controller/control.sock')


def receive(connection: socket.socket, count: int) -> bytes:
    chunks = bytearray()
    while len(chunks) < count:
        chunk = connection.recv(count - len(chunks))
        if not chunk:
            raise ConnectionError('Controller disconnected before completing a message')
        chunks.extend(chunk)
    return bytes(chunks)


def read_frame(connection: socket.socket) -> bytes:
    length = int.from_bytes(receive(connection, 4), 'big')
    if not 0 < length <= LIMIT:
        raise ValueError('Invalid controller message size')
    return receive(connection, length)


def write_frame(connection: socket.socket, value: bytes) -> None:
    if not 0 < len(value) <= LIMIT:
        raise ValueError('Invalid controller message size')
    connection.sendall(struct.pack('!I', len(value)) + value)


class BrokerClient:
    def __init__(self, path: Path = SOCKET) -> None:
        self.path = path

    def call(self, request: wire.BrokerRequest) -> wire.BrokerResponse:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(35)
            connection.connect(str(self.path))
            write_frame(connection, request.SerializeToString())
            return wire.BrokerResponse.FromString(read_frame(connection))

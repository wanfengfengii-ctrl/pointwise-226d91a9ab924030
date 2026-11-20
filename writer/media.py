import os
from pathlib import Path
from typing import Protocol


class AppendMedia(Protocol):
    def append(self, payload: bytes) -> int:
        ...

    def read_all(self) -> bytes:
        ...


class FileMedia:
    def __init__(self, path):
        self.path = Path(path)

    def append(self, payload: bytes) -> int:
        view = memoryview(payload)
        written = 0
        with self.path.open('ab', buffering=0) as stream:
            while written < len(view):
                count = stream.write(view[written:])
                if not count:
                    raise OSError('media append made no progress')
                written += count
            os.fsync(stream.fileno())
        return written

    def read_all(self) -> bytes:
        try:
            return self.path.read_bytes()
        except FileNotFoundError:
            return b''

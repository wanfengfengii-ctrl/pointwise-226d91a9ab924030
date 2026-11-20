from dataclasses import dataclass
from hashlib import sha256
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from order.selection import SelectedReading


@dataclass(frozen=True)
class DirectoryEntry:
    ordinal: int
    origin: str
    start: int
    end: int
    sha256: str


def build_directory(readings: 'tuple[SelectedReading, ...]',
                    bodies: tuple[bytes, ...]) -> tuple[DirectoryEntry, ...]:
    directory = []
    position = 0
    for reading, body in zip(readings, bodies, strict=True):
        end = position + len(body)
        directory.append(DirectoryEntry(reading.ordinal, reading.origin, position, end,
                                        sha256(body).hexdigest()))
        position = end
    return tuple(directory)

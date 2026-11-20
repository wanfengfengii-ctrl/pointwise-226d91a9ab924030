from dataclasses import dataclass
from typing import Iterable

from storage import ValidationError, require_reading_value, require_text
from study.imports import OriginalReading


@dataclass(frozen=True)
class Correction:
    origin: str
    value: int | None


def apply_corrections(readings: tuple[OriginalReading, ...],
                      corrections: Iterable[Correction]) -> tuple[int | None, ...]:
    known = {reading.origin for reading in readings}
    updates = {}
    for correction in corrections:
        if not isinstance(correction, Correction):
            raise ValidationError('expected a correction')
        origin = require_text(correction.origin, 'correction origin')
        if origin not in known:
            raise ValidationError(f'unknown correction origin {origin!r}')
        if origin in updates:
            raise ValidationError(f'duplicate correction origin {origin!r}')
        updates[origin] = require_reading_value(correction.value)
    return tuple(updates.get(reading.origin, reading.value) for reading in readings)

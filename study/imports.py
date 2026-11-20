from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, TYPE_CHECKING

from storage import ValidationError, require_reading_value, require_text
from study.calibration import CalibrationTable

if TYPE_CHECKING:
    from study.repository import StudyRepository


@dataclass(frozen=True)
class OriginalReading:
    origin: str
    value: int | None
    unit: str


@dataclass(frozen=True)
class DraftBatch:
    batch_id: str
    calibration: CalibrationTable
    readings: tuple[OriginalReading, ...]


def import_batch(repository: StudyRepository, batch_id: str, calibration: CalibrationTable,
                 readings: Iterable[OriginalReading]) -> DraftBatch:
    require_text(batch_id, 'batch id')
    if not isinstance(calibration, CalibrationTable):
        raise ValidationError('expected a calibration table')
    ordered = tuple(readings)
    seen = set()
    for reading in ordered:
        if not isinstance(reading, OriginalReading):
            raise ValidationError('expected an original reading')
        require_text(reading.origin, 'origin')
        if reading.origin in seen:
            raise ValidationError(f'duplicate origin {reading.origin!r}')
        calibration.rule_for(reading.unit)
        require_reading_value(reading.value)
        seen.add(reading.origin)
    draft = DraftBatch(batch_id, calibration, ordered)
    repository.insert_draft(draft)
    return draft

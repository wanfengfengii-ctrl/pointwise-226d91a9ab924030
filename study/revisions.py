from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, TYPE_CHECKING

from storage import ValidationError, require_text
from study.calibration import CalibrationTable, calibrate_reading
from study.corrections import Correction, apply_corrections

if TYPE_CHECKING:
    from study.repository import StudyRepository


@dataclass(frozen=True)
class FrozenReading:
    origin: str
    original_unit: str
    original_value: int | None
    working_value: int | None
    value: int | None


@dataclass(frozen=True)
class FrozenRevision:
    batch_id: str
    revision_id: str
    calibration: CalibrationTable
    readings: tuple[FrozenReading, ...]


def freeze_revision(repository: StudyRepository, batch_id: str, revision_id: str,
                    corrections: Iterable[Correction] = ()) -> FrozenRevision:
    require_text(revision_id, 'revision id')
    draft = repository.get_draft(batch_id)
    values = apply_corrections(draft.readings, corrections)
    frozen = []
    for original, working in zip(draft.readings, values, strict=True):
        try:
            calibrated = calibrate_reading(draft.calibration, original.unit, working)
        except ValidationError as exc:
            raise ValidationError(f'origin {original.origin!r}: {exc}') from exc
        frozen.append(FrozenReading(original.origin, original.unit, original.value,
                                    working, calibrated))
    revision = FrozenRevision(draft.batch_id, revision_id, draft.calibration, tuple(frozen))
    repository.insert_revision(revision)
    return revision


def load_revision(repository: StudyRepository, batch_id: str, revision_id: str) -> FrozenRevision:
    return repository.get_revision(batch_id, revision_id)

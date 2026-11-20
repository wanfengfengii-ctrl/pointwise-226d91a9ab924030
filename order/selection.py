from dataclasses import dataclass

from study.revisions import FrozenRevision


@dataclass(frozen=True)
class SelectedReading:
    ordinal: int
    origin: str
    original_unit: str
    target_unit: str
    original_value: int | None
    working_value: int | None
    value: int | None


def select_readings(revision: FrozenRevision, origins: tuple[str, ...]) -> tuple[SelectedReading, ...]:
    by_origin = {reading.origin: reading for reading in revision.readings}
    selected = []
    for ordinal, origin in enumerate(origins):
        reading = by_origin[origin]
        selected.append(SelectedReading(ordinal, origin, reading.original_unit,
                                        revision.calibration.target_unit, reading.original_value,
                                        reading.working_value, reading.value))
    return tuple(selected)

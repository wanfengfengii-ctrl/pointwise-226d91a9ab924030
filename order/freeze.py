from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, TYPE_CHECKING

from order.checks import validate_selection
from order.selection import SelectedReading, select_readings
from storage import require_text
from study.revisions import load_revision

if TYPE_CHECKING:
    from order.repository import OrderRepository
    from study.repository import StudyRepository


@dataclass(frozen=True)
class FixedOrder:
    order_id: str
    batch_id: str
    revision_id: str
    calibration_version: str
    target_unit: str
    items: tuple[SelectedReading, ...]


def fix_order(repository: OrderRepository, study: StudyRepository, batch_id: str,
              revision_id: str, order_id: str, origins: Iterable[str]) -> FixedOrder:
    require_text(order_id, 'order id')
    revision = load_revision(study, batch_id, revision_id)
    requested = validate_selection({reading.origin for reading in revision.readings}, origins)
    order = FixedOrder(order_id, revision.batch_id, revision.revision_id, revision.calibration.version,
                       revision.calibration.target_unit, select_readings(revision, requested))
    repository.insert_order(order)
    return order

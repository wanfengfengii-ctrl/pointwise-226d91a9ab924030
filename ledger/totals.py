from dataclasses import dataclass
from typing import Iterable

from intake.receipts import ReceiptItem
from storage import ValidationError


@dataclass(frozen=True)
class LedgerTotals:
    valid_count: int
    total: int


def compute_totals(items: Iterable[ReceiptItem]) -> LedgerTotals:
    seen = {}
    count = 0
    total = 0
    for item in items:
        key = (item.seal_id, item.ordinal)
        if key in seen:
            if seen[key] != item:
                raise ValidationError(f'contradictory receipt item {key!r}')
            continue
        seen[key] = item
        if item.value is not None:
            count += 1
            total += item.value
    return LedgerTotals(count, total)

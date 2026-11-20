from dataclasses import dataclass
from typing import Iterable

from intake.receipts import AcceptedVolume, ReceiptItem
from intake.repository import IntakeRepository


@dataclass(frozen=True)
class LedgerSourceRef:
    receipt_id: int
    ordinal: int


def make_source_refs(receipt: AcceptedVolume) -> tuple[LedgerSourceRef, ...]:
    return tuple(LedgerSourceRef(receipt.first_receipt.receipt_id, item.ordinal) for item in receipt.items)


def resolve_source_refs(repository: IntakeRepository,
                        refs: Iterable[LedgerSourceRef]) -> tuple[ReceiptItem, ...]:
    return tuple(repository.get_receipt_item(ref.receipt_id, ref.ordinal) for ref in refs)

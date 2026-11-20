from dataclasses import dataclass

from intake.receipts import ReceiptItem
from intake.repository import IntakeRepository
from ledger.provenance import resolve_source_refs
from ledger.repository import LedgerRepository, StoredLedger


@dataclass(frozen=True)
class LedgerReport:
    stored: StoredLedger
    items: tuple[ReceiptItem, ...]


def read_ledger(repository: LedgerRepository, account: str) -> LedgerReport:
    stored = repository.get_ledger(account)
    items = resolve_source_refs(IntakeRepository(repository.db), stored.source_refs)
    return LedgerReport(stored, items)

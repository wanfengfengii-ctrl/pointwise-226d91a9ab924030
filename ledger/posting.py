from intake.repository import IntakeRepository
from ledger.history import LedgerReport
from ledger.provenance import make_source_refs
from ledger.repository import LedgerRepository, StoredLedger
from ledger.totals import compute_totals
from storage import require_text


def post_ledger(repository: LedgerRepository, shift: str, receipt_id: int, account: str) -> LedgerReport:
    require_text(shift, 'shift')
    require_text(account, 'account')
    receipt = IntakeRepository(repository.db).get_receipt(receipt_id)
    totals = compute_totals(receipt.items)
    stored = StoredLedger(account, shift, receipt_id, receipt.target_unit, totals, make_source_refs(receipt))
    repository.insert_ledger(stored)
    return LedgerReport(stored, receipt.items)

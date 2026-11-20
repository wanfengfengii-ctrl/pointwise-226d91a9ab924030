from dataclasses import dataclass

from ledger.provenance import LedgerSourceRef
from ledger.totals import LedgerTotals
from storage import ConflictError, Database, MissingError, decimal_text, decimal_value, require_text


@dataclass(frozen=True)
class StoredLedger:
    account: str
    shift: str
    receipt_id: int
    target_unit: str
    totals: LedgerTotals
    source_refs: tuple[LedgerSourceRef, ...]


class LedgerRepository:
    def __init__(self, db: Database):
        self.db = db

    def initialize(self):
        self.db.create_schema(
            '''CREATE TABLE IF NOT EXISTS ledger_accounts (
                account TEXT NOT NULL PRIMARY KEY,
                shift TEXT NOT NULL,
                receipt_id INTEGER NOT NULL REFERENCES intake_volumes(receipt_id),
                target_unit TEXT NOT NULL,
                valid_count INTEGER NOT NULL,
                total TEXT NOT NULL,
                UNIQUE (account, receipt_id)
            )''',
            '''CREATE TABLE IF NOT EXISTS ledger_sources (
                account TEXT NOT NULL,
                position INTEGER NOT NULL,
                receipt_id INTEGER NOT NULL,
                ordinal INTEGER NOT NULL,
                PRIMARY KEY (account, position),
                UNIQUE (account, receipt_id, ordinal),
                FOREIGN KEY (account, receipt_id) REFERENCES ledger_accounts(account, receipt_id),
                FOREIGN KEY (receipt_id, ordinal) REFERENCES intake_items(receipt_id, ordinal)
            )''',
        )

    def insert_ledger(self, ledger: StoredLedger):
        with self.db.transaction() as connection:
            if connection.execute('SELECT 1 FROM ledger_accounts WHERE account = ?',
                                  (ledger.account,)).fetchone() is not None:
                raise ConflictError(f'account already exists: {ledger.account!r}')
            connection.execute(
                '''INSERT INTO ledger_accounts (account, shift, receipt_id, target_unit, valid_count, total)
                   VALUES (?, ?, ?, ?, ?, ?)''',
                (ledger.account, ledger.shift, ledger.receipt_id, ledger.target_unit,
                 ledger.totals.valid_count, decimal_text(ledger.totals.total)),
            )
            connection.executemany(
                'INSERT INTO ledger_sources (account, position, receipt_id, ordinal) VALUES (?, ?, ?, ?)',
                ((ledger.account, position, ref.receipt_id, ref.ordinal)
                 for position, ref in enumerate(ledger.source_refs)),
            )

    def get_ledger(self, account: str) -> StoredLedger:
        require_text(account, 'account')
        connection = self.db.connection
        header = connection.execute('SELECT * FROM ledger_accounts WHERE account = ?', (account,)).fetchone()
        if header is None:
            raise MissingError(f'unknown account {account!r}')
        rows = connection.execute(
            'SELECT receipt_id, ordinal FROM ledger_sources WHERE account = ? ORDER BY position', (account,))
        refs = tuple(LedgerSourceRef(row['receipt_id'], row['ordinal']) for row in rows)
        totals = LedgerTotals(header['valid_count'], decimal_value(header['total']))
        return StoredLedger(account, header['shift'], header['receipt_id'], header['target_unit'], totals, refs)

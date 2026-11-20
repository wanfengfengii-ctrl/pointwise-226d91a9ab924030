from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import uuid4

from intake.receipts import AcceptedVolume, FirstReceipt, ReceiptItem
from storage import ConflictError, Database, MissingError, decimal_text, decimal_value

if TYPE_CHECKING:
    from intake.decoding import ParsedEnvelope


_ITEM_SELECT = '''SELECT v.seal_id, v.batch_id, v.revision_id, v.target_unit, i.*
                  FROM intake_items AS i JOIN intake_volumes AS v USING (receipt_id)'''


def _read_item(row):
    return ReceiptItem(row['seal_id'], row['batch_id'], row['revision_id'], row['ordinal'], row['origin'],
                       row['range_start'], row['range_end'], row['original_unit'], row['target_unit'],
                       decimal_value(row['original_value']), decimal_value(row['working_value']),
                       decimal_value(row['value']))


class IntakeRepository:
    def __init__(self, db: Database):
        self.db = db

    def initialize(self):
        self.db.create_schema(
            '''CREATE TABLE IF NOT EXISTS intake_volumes (
                receipt_id INTEGER PRIMARY KEY,
                seal_id TEXT NOT NULL UNIQUE,
                order_id TEXT NOT NULL,
                batch_id TEXT NOT NULL,
                revision_id TEXT NOT NULL,
                calibration_version TEXT NOT NULL,
                target_unit TEXT NOT NULL,
                envelope BLOB NOT NULL,
                payload BLOB NOT NULL
            )''',
            '''CREATE TABLE IF NOT EXISTS intake_items (
                receipt_id INTEGER NOT NULL REFERENCES intake_volumes(receipt_id),
                ordinal INTEGER NOT NULL,
                origin TEXT NOT NULL,
                range_start INTEGER NOT NULL,
                range_end INTEGER NOT NULL,
                original_unit TEXT NOT NULL,
                original_value TEXT,
                working_value TEXT,
                value TEXT,
                PRIMARY KEY (receipt_id, ordinal),
                UNIQUE (receipt_id, origin)
            )''',
            '''CREATE TABLE IF NOT EXISTS intake_first_receipts (
                receipt_id INTEGER PRIMARY KEY REFERENCES intake_volumes(receipt_id),
                token TEXT NOT NULL UNIQUE
            )''',
        )

    def accept_or_replay(self, parsed: ParsedEnvelope, envelope: bytes, payload: bytes,
                         items: tuple[ReceiptItem, ...]) -> AcceptedVolume:
        with self.db.transaction() as connection:
            prior = connection.execute(
                'SELECT receipt_id, envelope, payload FROM intake_volumes WHERE seal_id = ?',
                (parsed.seal_id,)).fetchone()
            if prior is not None:
                if (prior['envelope'], prior['payload']) != (envelope, payload):
                    raise ConflictError(f'seal already received with other bytes: {parsed.seal_id!r}')
                return self.get_receipt(prior['receipt_id'])

            inserted = connection.execute(
                '''INSERT INTO intake_volumes
                   (seal_id, order_id, batch_id, revision_id, calibration_version, target_unit, envelope, payload)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
                (parsed.seal_id, parsed.order_id, parsed.batch_id, parsed.revision_id,
                 parsed.calibration_version, parsed.target_unit, envelope, payload),
            )
            receipt_id = inserted.lastrowid
            connection.executemany(
                '''INSERT INTO intake_items
                   (receipt_id, ordinal, origin, range_start, range_end,
                    original_unit, original_value, working_value, value) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                ((receipt_id, item.ordinal, item.origin, item.start, item.end, item.original_unit,
                  decimal_text(item.original_value), decimal_text(item.working_value), decimal_text(item.value))
                 for item in items),
            )
            token = uuid4().hex
            connection.execute('INSERT INTO intake_first_receipts (receipt_id, token) VALUES (?, ?)',
                               (receipt_id, token))
            first = FirstReceipt(receipt_id, parsed.seal_id, token)
        return AcceptedVolume(first, parsed.order_id, parsed.batch_id, parsed.revision_id,
                              parsed.calibration_version, parsed.target_unit, items)

    def get_receipt(self, receipt_id: int) -> AcceptedVolume:
        if type(receipt_id) is not int or not 0 < receipt_id <= (1 << 63) - 1:
            raise MissingError(f'unknown receipt {receipt_id!r}')
        connection = self.db.connection
        header = connection.execute(
            '''SELECT v.*, f.token FROM intake_volumes AS v JOIN intake_first_receipts AS f USING (receipt_id)
               WHERE v.receipt_id = ?''', (receipt_id,)).fetchone()
        if header is None:
            raise MissingError(f'unknown receipt {receipt_id!r}')
        rows = connection.execute(_ITEM_SELECT + ' WHERE i.receipt_id = ? ORDER BY i.ordinal', (receipt_id,))
        first = FirstReceipt(receipt_id, header['seal_id'], header['token'])
        return AcceptedVolume(first, header['order_id'], header['batch_id'], header['revision_id'],
                              header['calibration_version'], header['target_unit'],
                              tuple(_read_item(row) for row in rows))

    def get_receipt_item(self, receipt_id: int, ordinal: int) -> ReceiptItem:
        if (type(receipt_id) is not int or not 0 < receipt_id <= (1 << 63) - 1
                or type(ordinal) is not int or not 0 <= ordinal <= (1 << 63) - 1):
            raise MissingError(f'unknown receipt item {(receipt_id, ordinal)!r}')
        row = self.db.connection.execute(_ITEM_SELECT + ' WHERE i.receipt_id = ? AND i.ordinal = ?',
                                         (receipt_id, ordinal)).fetchone()
        if row is None:
            raise MissingError(f'unknown receipt item {(receipt_id, ordinal)!r}')
        return _read_item(row)

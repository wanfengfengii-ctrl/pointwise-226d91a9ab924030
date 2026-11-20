from order.freeze import FixedOrder
from order.selection import SelectedReading
from storage import ConflictError, Database, MissingError, decimal_text, decimal_value, require_text


class OrderRepository:
    def __init__(self, db: Database):
        self.db = db

    def initialize(self):
        self.db.create_schema(
            '''CREATE TABLE IF NOT EXISTS orders_fixed (
                order_id TEXT NOT NULL PRIMARY KEY,
                batch_id TEXT NOT NULL,
                revision_id TEXT NOT NULL,
                calibration_version TEXT NOT NULL,
                target_unit TEXT NOT NULL
            )''',
            '''CREATE TABLE IF NOT EXISTS orders_items (
                order_id TEXT NOT NULL REFERENCES orders_fixed(order_id),
                ordinal INTEGER NOT NULL,
                origin TEXT NOT NULL,
                original_unit TEXT NOT NULL,
                original_value TEXT,
                working_value TEXT,
                value TEXT,
                PRIMARY KEY (order_id, ordinal),
                UNIQUE (order_id, origin)
            )''',
        )

    def insert_order(self, order: FixedOrder):
        with self.db.transaction() as connection:
            if connection.execute('SELECT 1 FROM orders_fixed WHERE order_id = ?',
                                  (order.order_id,)).fetchone() is not None:
                raise ConflictError(f'order already exists: {order.order_id!r}')
            connection.execute('INSERT INTO orders_fixed VALUES (?, ?, ?, ?, ?)',
                               (order.order_id, order.batch_id, order.revision_id,
                                order.calibration_version, order.target_unit))
            connection.executemany(
                '''INSERT INTO orders_items
                   (order_id, ordinal, origin, original_unit, original_value, working_value, value)
                   VALUES (?, ?, ?, ?, ?, ?, ?)''',
                ((order.order_id, item.ordinal, item.origin, item.original_unit,
                  decimal_text(item.original_value), decimal_text(item.working_value), decimal_text(item.value))
                 for item in order.items),
            )

    def get_order(self, order_id: str) -> FixedOrder:
        require_text(order_id, 'order id')
        connection = self.db.connection
        header = connection.execute('SELECT * FROM orders_fixed WHERE order_id = ?', (order_id,)).fetchone()
        if header is None:
            raise MissingError(f'unknown order {order_id!r}')
        rows = connection.execute('SELECT * FROM orders_items WHERE order_id = ? ORDER BY ordinal', (order_id,))
        items = tuple(SelectedReading(row['ordinal'], row['origin'], row['original_unit'], header['target_unit'],
                                      decimal_value(row['original_value']), decimal_value(row['working_value']),
                                      decimal_value(row['value'])) for row in rows)
        return FixedOrder(order_id, header['batch_id'], header['revision_id'],
                          header['calibration_version'], header['target_unit'], items)

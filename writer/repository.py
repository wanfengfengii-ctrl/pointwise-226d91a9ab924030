from dataclasses import dataclass
from hashlib import sha256

from order.freeze import FixedOrder
from storage import Database, MissingError, require_text
from writer.directory import DirectoryEntry, V2SelectionEntry
from writer.encoding import encode_v1_envelope, encode_v2_envelope


@dataclass(frozen=True)
class SealedVolume:
    seal_id: str
    envelope: bytes
    payload: bytes


@dataclass(frozen=True)
class V2Volume:
    volume_id: str
    order_id: str
    seal_id: str | None


@dataclass(frozen=True)
class V2Request:
    request_id: str
    ordinal: int
    origin: str
    start: int | None
    end: int | None


@dataclass(frozen=True)
class V2Fragment:
    request_id: str
    start: int
    end: int


class VolumeRepository:
    def __init__(self, db: Database):
        self.db = db

    def initialize(self):
        self.db.create_schema(
            '''CREATE TABLE IF NOT EXISTS writer_volumes (
                serial INTEGER PRIMARY KEY,
                seal_id TEXT NOT NULL UNIQUE,
                envelope BLOB NOT NULL,
                payload BLOB NOT NULL
            )''',
            '''CREATE TABLE IF NOT EXISTS writer_v2_volumes (
                volume_id TEXT NOT NULL PRIMARY KEY,
                order_id TEXT NOT NULL REFERENCES orders_fixed(order_id),
                seal_id TEXT
            )''',
            '''CREATE TABLE IF NOT EXISTS writer_v2_requests (
                volume_id TEXT NOT NULL REFERENCES writer_v2_volumes(volume_id),
                request_id TEXT NOT NULL,
                ordinal INTEGER NOT NULL,
                origin TEXT NOT NULL,
                range_start INTEGER,
                range_end INTEGER,
                PRIMARY KEY (volume_id, request_id),
                UNIQUE (volume_id, origin)
            )''',
            '''CREATE TABLE IF NOT EXISTS writer_v2_fragments (
                volume_id TEXT NOT NULL REFERENCES writer_v2_volumes(volume_id),
                request_id TEXT NOT NULL,
                range_start INTEGER NOT NULL,
                range_end INTEGER NOT NULL,
                PRIMARY KEY (volume_id, range_start)
            )''',
        )

    def publish_volume(self, order: FixedOrder, directory: tuple[DirectoryEntry, ...], payload: bytes) -> str:
        fingerprint = sha256(encode_v1_envelope('V1', order, directory)).hexdigest()
        with self.db.transaction() as connection:
            serial = connection.execute('SELECT COALESCE(MAX(serial), 0) + 1 FROM writer_volumes').fetchone()[0]
            seal_id = f'V1-{serial}-{fingerprint}'
            envelope = encode_v1_envelope(seal_id, order, directory)
            connection.execute('INSERT INTO writer_volumes VALUES (?, ?, ?, ?)',
                               (serial, seal_id, envelope, payload))
        return seal_id

    def get_volume(self, seal_id: str) -> SealedVolume:
        require_text(seal_id, 'seal id')
        row = self.db.connection.execute('SELECT envelope, payload FROM writer_volumes WHERE seal_id = ?',
                                         (seal_id,)).fetchone()
        if row is None:
            raise MissingError(f'unknown seal {seal_id!r}')
        return SealedVolume(seal_id, row['envelope'], row['payload'])

    def register_v2_volume(self, volume_id: str, order_id: str):
        with self.db.transaction() as connection:
            connection.execute('INSERT INTO writer_v2_volumes (volume_id, order_id) VALUES (?, ?)',
                               (volume_id, order_id))

    def get_v2_volume(self, volume_id: str) -> V2Volume | None:
        require_text(volume_id, 'volume id')
        row = self.db.connection.execute(
            'SELECT volume_id, order_id, seal_id FROM writer_v2_volumes WHERE volume_id = ?',
            (volume_id,)).fetchone()
        if row is None:
            return None
        return V2Volume(row['volume_id'], row['order_id'], row['seal_id'])

    def insert_v2_request(self, volume_id: str, request_id: str, ordinal: int, origin: str):
        with self.db.transaction() as connection:
            connection.execute(
                '''INSERT INTO writer_v2_requests (volume_id, request_id, ordinal, origin)
                   VALUES (?, ?, ?, ?)''',
                (volume_id, request_id, ordinal, origin))

    def list_v2_requests(self, volume_id: str) -> tuple[V2Request, ...]:
        rows = self.db.connection.execute(
            '''SELECT request_id, ordinal, origin, range_start, range_end
               FROM writer_v2_requests WHERE volume_id = ? ORDER BY ordinal''',
            (volume_id,))
        return tuple(V2Request(row['request_id'], row['ordinal'], row['origin'],
                               row['range_start'], row['range_end']) for row in rows)

    def complete_v2_request(self, volume_id: str, request_id: str, start: int, end: int):
        with self.db.transaction() as connection:
            connection.execute(
                '''UPDATE writer_v2_requests SET range_start = ?, range_end = ?
                   WHERE volume_id = ? AND request_id = ? AND range_start IS NULL''',
                (start, end, volume_id, request_id))

    def insert_v2_fragment(self, volume_id: str, request_id: str, start: int, end: int):
        with self.db.transaction() as connection:
            connection.execute(
                '''INSERT INTO writer_v2_fragments (volume_id, request_id, range_start, range_end)
                   VALUES (?, ?, ?, ?)''',
                (volume_id, request_id, start, end))

    def list_v2_fragments(self, volume_id: str) -> tuple[V2Fragment, ...]:
        rows = self.db.connection.execute(
            '''SELECT request_id, range_start, range_end
               FROM writer_v2_fragments WHERE volume_id = ? ORDER BY range_start''',
            (volume_id,))
        return tuple(V2Fragment(row['request_id'], row['range_start'], row['range_end']) for row in rows)

    def publish_v2_volume(self, volume_id: str, order: FixedOrder,
                          selections: tuple[V2SelectionEntry, ...],
                          fragments: tuple[V2Fragment, ...], payload: bytes) -> str:
        fingerprint = sha256(encode_v2_envelope('V2', volume_id, order, selections, fragments)).hexdigest()
        with self.db.transaction() as connection:
            row = connection.execute('SELECT seal_id FROM writer_v2_volumes WHERE volume_id = ?',
                                     (volume_id,)).fetchone()
            if row is None:
                raise MissingError(f'unknown volume {volume_id!r}')
            if row['seal_id'] is not None:
                return row['seal_id']
            serial = connection.execute('SELECT COALESCE(MAX(serial), 0) + 1 FROM writer_volumes').fetchone()[0]
            seal_id = f'V2-{serial}-{fingerprint}'
            envelope = encode_v2_envelope(seal_id, volume_id, order, selections, fragments)
            connection.execute('INSERT INTO writer_volumes VALUES (?, ?, ?, ?)',
                               (serial, seal_id, envelope, payload))
            connection.execute('UPDATE writer_v2_volumes SET seal_id = ? WHERE volume_id = ?',
                               (seal_id, volume_id))
        return seal_id

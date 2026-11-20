from dataclasses import dataclass
from hashlib import sha256

from order.freeze import FixedOrder
from storage import Database, MissingError, require_text
from writer.directory import DirectoryEntry
from writer.encoding import encode_v1_envelope


@dataclass(frozen=True)
class SealedVolume:
    seal_id: str
    envelope: bytes
    payload: bytes


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

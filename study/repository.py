import json

from storage import ConflictError, Database, MissingError, decimal_text, decimal_value, json_bytes, require_text
from study.calibration import CalibrationRule, CalibrationTable
from study.imports import DraftBatch, OriginalReading
from study.revisions import FrozenReading, FrozenRevision


def _encode_calibration(table):
    return json_bytes({
        'version': table.version,
        'target_unit': table.target_unit,
        'rules': [[rule.unit, decimal_text(rule.numerator), decimal_text(rule.denominator)]
                  for rule in table.rules],
    }).decode('utf-8')


def _read_calibration(encoded):
    data = json.loads(encoded)
    rules = tuple(CalibrationRule(unit, decimal_value(numerator), decimal_value(denominator))
                  for unit, numerator, denominator in data['rules'])
    return CalibrationTable(data['version'], data['target_unit'], rules)


class StudyRepository:
    def __init__(self, db: Database):
        self.db = db

    def initialize(self):
        self.db.create_schema(
            '''CREATE TABLE IF NOT EXISTS study_drafts (
                batch_id TEXT NOT NULL PRIMARY KEY,
                calibration TEXT NOT NULL
            )''',
            '''CREATE TABLE IF NOT EXISTS study_draft_readings (
                batch_id TEXT NOT NULL REFERENCES study_drafts(batch_id),
                position INTEGER NOT NULL,
                origin TEXT NOT NULL,
                unit TEXT NOT NULL,
                original_value TEXT,
                PRIMARY KEY (batch_id, position),
                UNIQUE (batch_id, origin)
            )''',
            '''CREATE TABLE IF NOT EXISTS study_revisions (
                batch_id TEXT NOT NULL REFERENCES study_drafts(batch_id),
                revision_id TEXT NOT NULL,
                calibration TEXT NOT NULL,
                PRIMARY KEY (batch_id, revision_id)
            )''',
            '''CREATE TABLE IF NOT EXISTS study_revision_readings (
                batch_id TEXT NOT NULL,
                revision_id TEXT NOT NULL,
                position INTEGER NOT NULL,
                origin TEXT NOT NULL,
                original_unit TEXT NOT NULL,
                original_value TEXT,
                working_value TEXT,
                value TEXT,
                PRIMARY KEY (batch_id, revision_id, position),
                UNIQUE (batch_id, revision_id, origin),
                FOREIGN KEY (batch_id, revision_id) REFERENCES study_revisions(batch_id, revision_id)
            )''',
        )

    def insert_draft(self, draft: DraftBatch):
        with self.db.transaction() as connection:
            occupied = connection.execute('SELECT 1 FROM study_drafts WHERE batch_id = ?',
                                          (draft.batch_id,)).fetchone()
            if occupied is not None:
                raise ConflictError(f'batch already exists: {draft.batch_id!r}')
            connection.execute('INSERT INTO study_drafts VALUES (?, ?)',
                               (draft.batch_id, _encode_calibration(draft.calibration)))
            connection.executemany(
                '''INSERT INTO study_draft_readings
                   (batch_id, position, origin, unit, original_value) VALUES (?, ?, ?, ?, ?)''',
                ((draft.batch_id, position, reading.origin, reading.unit, decimal_text(reading.value))
                 for position, reading in enumerate(draft.readings)),
            )

    def get_draft(self, batch_id: str) -> DraftBatch:
        require_text(batch_id, 'batch id')
        connection = self.db.connection
        header = connection.execute('SELECT calibration FROM study_drafts WHERE batch_id = ?',
                                    (batch_id,)).fetchone()
        if header is None:
            raise MissingError(f'unknown batch {batch_id!r}')
        rows = connection.execute(
            '''SELECT origin, unit, original_value FROM study_draft_readings
               WHERE batch_id = ? ORDER BY position''', (batch_id,))
        readings = tuple(OriginalReading(row['origin'], decimal_value(row['original_value']), row['unit'])
                         for row in rows)
        return DraftBatch(batch_id, _read_calibration(header['calibration']), readings)

    def insert_revision(self, revision: FrozenRevision):
        key = (revision.batch_id, revision.revision_id)
        with self.db.transaction() as connection:
            occupied = connection.execute(
                'SELECT 1 FROM study_revisions WHERE batch_id = ? AND revision_id = ?', key).fetchone()
            if occupied is not None:
                raise ConflictError(f'revision already exists: {key!r}')
            connection.execute('INSERT INTO study_revisions VALUES (?, ?, ?)',
                               (*key, _encode_calibration(revision.calibration)))
            connection.executemany(
                '''INSERT INTO study_revision_readings
                   (batch_id, revision_id, position, origin, original_unit,
                    original_value, working_value, value) VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
                ((*key, position, reading.origin, reading.original_unit,
                  decimal_text(reading.original_value), decimal_text(reading.working_value),
                  decimal_text(reading.value)) for position, reading in enumerate(revision.readings)),
            )

    def get_revision(self, batch_id: str, revision_id: str) -> FrozenRevision:
        key = (require_text(batch_id, 'batch id'), require_text(revision_id, 'revision id'))
        connection = self.db.connection
        header = connection.execute(
            'SELECT calibration FROM study_revisions WHERE batch_id = ? AND revision_id = ?', key).fetchone()
        if header is None:
            raise MissingError(f'unknown revision {key!r}')
        rows = connection.execute(
            '''SELECT origin, original_unit, original_value, working_value, value
               FROM study_revision_readings WHERE batch_id = ? AND revision_id = ? ORDER BY position''', key)
        readings = tuple(FrozenReading(row['origin'], row['original_unit'],
                                       decimal_value(row['original_value']), decimal_value(row['working_value']),
                                       decimal_value(row['value'])) for row in rows)
        return FrozenRevision(*key, _read_calibration(header['calibration']), readings)

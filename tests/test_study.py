from storage import ConflictError, MissingError, ValidationError
from study.corrections import Correction
from study.imports import OriginalReading, import_batch
from study.repository import StudyRepository
from study.revisions import freeze_revision, load_revision
from tests.support import ExchangeCase


class StudyTests(ExchangeCase):
    def test_freezes_independent_revisions(self):
        import_batch(self.study, 'batch', self.calibration,
                     (OriginalReading('A', 20, '半格'), OriginalReading('B', 8, '格')))
        old = freeze_revision(self.study, 'batch', 'R1')
        new = freeze_revision(self.study, 'batch', 'R2', (Correction('A', 24),))
        self.study_db.close()
        reopened = StudyRepository(self.open_database(self.study_path))
        self.assertEqual(load_revision(reopened, 'batch', 'R1'), old)
        self.assertEqual(load_revision(reopened, 'batch', 'R2'), new)
        self.assertEqual([(row.origin, row.original_value, row.working_value, row.value) for row in old.readings],
                         [('A', 20, 20, 10), ('B', 8, 8, 8)])
        self.assertEqual([(row.origin, row.original_value, row.working_value, row.value) for row in new.readings],
                         [('A', 20, 24, 12), ('B', 8, 8, 8)])
        self.assertEqual((old.calibration.version, new.calibration.version), ('C1', 'C1'))

    def test_freezes_exact_fraction(self):
        revision = self.prepare_workday()
        self.assertEqual([row.origin for row in revision.readings], ['A', 'B', 'C'])
        self.assertEqual([row.value for row in revision.readings], [None, 0, 11])
        self.assertEqual((revision.readings[2].original_value, revision.readings[2].working_value), (20, 22))
        self.assertEqual(load_revision(self.study, 'workday', 'R1').readings[2].value, 10)

    def test_rejects_fraction_without_creating_revision(self):
        revision = self.prepare_workday()
        with self.assertRaisesRegex(ValidationError, 'converted exactly'):
            freeze_revision(self.study, 'workday', 'R3', (Correction('C', 21),))
        with self.assertRaises(MissingError):
            load_revision(self.study, 'workday', 'R3')
        self.assertEqual(load_revision(self.study, 'workday', 'R2'), revision)

    def test_rejects_unknown_or_duplicate_correction(self):
        revision = self.prepare_workday()
        cases = ((Correction('A', 1), Correction('unknown', 2)),
                 (Correction('C', 22), Correction('C', 24)))
        for corrections in cases:
            with self.subTest(corrections=corrections):
                with self.assertRaises(ValidationError):
                    freeze_revision(self.study, 'workday', 'R3', corrections)
                with self.assertRaises(MissingError):
                    load_revision(self.study, 'workday', 'R3')
        self.assertEqual(load_revision(self.study, 'workday', 'R2'), revision)

    def test_invalid_unit_rejects_whole_batch(self):
        with self.assertRaisesRegex(ValidationError, 'unknown original unit'):
            import_batch(self.study, 'bad', self.calibration,
                         (OriginalReading('A', 1, '格'), OriginalReading('B', 2, '未知')))
        self.assert_batch_missing('bad')

    def test_duplicate_origin_rejects_whole_batch(self):
        with self.assertRaisesRegex(ValidationError, 'duplicate origin'):
            import_batch(self.study, 'bad', self.calibration,
                         (OriginalReading('A', 1, '格'), OriginalReading('A', 2, '格')))
        self.assert_batch_missing('bad')

    def test_initialize_keeps_draft_and_revision(self):
        revision = self.prepare_workday()
        draft = self.study.get_draft('workday')
        self.study.initialize()
        self.study.initialize()
        with self.assertRaises(ValidationError):
            import_batch(self.study, 'bad', self.calibration,
                         (OriginalReading('A', None, 'unknown'),))
        self.assert_batch_missing('bad')
        self.assertEqual(self.study.get_draft('workday'), draft)
        self.assertEqual(load_revision(self.study, 'workday', 'R2'), revision)
        with self.assertRaises(ConflictError):
            freeze_revision(self.study, 'workday', 'R2', (Correction('C', 24),))
        self.assertEqual(load_revision(self.study, 'workday', 'R2'), revision)

    def assert_batch_missing(self, key):
        with self.assertRaises(MissingError):
            self.study.get_draft(key)
        cursor = self.study_db.connection.execute('SELECT COUNT(*) FROM study_draft_readings WHERE batch_id = ?',
                                                  (key,))
        self.assertEqual(cursor.fetchone()[0], 0)

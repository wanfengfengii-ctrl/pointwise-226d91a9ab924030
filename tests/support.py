from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from intake.repository import IntakeRepository
from ledger.repository import LedgerRepository
from order.freeze import fix_order
from order.repository import OrderRepository
from storage import Database
from study.calibration import CalibrationRule, CalibrationTable
from study.corrections import Correction
from study.imports import OriginalReading, import_batch
from study.repository import StudyRepository
from study.revisions import freeze_revision
from writer.media import FileMedia
from writer.repository import VolumeRepository
from writer.seal import read_volume, write_volume


class ExchangeCase(unittest.TestCase):
    def setUp(self):
        workspace = TemporaryDirectory()
        self.addCleanup(workspace.cleanup)
        self.root = Path(workspace.name)
        self.study_path = self.root / 'study.sqlite3'
        self.issue_path = self.root / 'issue.sqlite3'
        self.night_path = self.root / 'night.sqlite3'
        self.study_db = self.open_database(self.study_path)
        self.issue_db = self.open_database(self.issue_path)
        self.night_db = self.open_database(self.night_path)
        self.study = StudyRepository(self.study_db)
        self.orders = OrderRepository(self.issue_db)
        self.volumes = VolumeRepository(self.issue_db)
        self.intake = IntakeRepository(self.night_db)
        self.ledgers = LedgerRepository(self.night_db)
        for repository in (self.study, self.orders, self.volumes, self.intake, self.ledgers):
            repository.initialize()
        self.calibration = CalibrationTable('C1', '格', (CalibrationRule('半格', 1, 2),
                                                        CalibrationRule('格', 1, 1)))

    def open_database(self, path):
        db = Database(path)
        self.addCleanup(db.close)
        return db

    def prepare_workday(self):
        import_batch(self.study, 'workday', self.calibration, (
            OriginalReading('A', None, '格'), OriginalReading('B', 0, '格'), OriginalReading('C', 20, '半格'),
        ))
        freeze_revision(self.study, 'workday', 'R1')
        return freeze_revision(self.study, 'workday', 'R2', (Correction('C', 22),))

    def issue(self, order_id, origins, *, batch_id='workday', revision_id='R2'):
        fix_order(self.orders, self.study, batch_id, revision_id, order_id, origins)
        media = FileMedia(self.root / f'{order_id}.media')
        return read_volume(self.volumes, write_volume(self.volumes, self.orders, order_id, media))

    def reopen_night(self):
        self.night_db.close()
        self.night_db = self.open_database(self.night_path)
        self.intake = IntakeRepository(self.night_db)
        self.ledgers = LedgerRepository(self.night_db)

    def detach_issuer(self):
        self.study_db.close()
        self.issue_db.close()
        self.study_path.rename(self.root / 'closed-study.sqlite3')
        self.issue_path.rename(self.root / 'closed-issue.sqlite3')
        self.assertFalse(self.study_path.exists())
        self.assertFalse(self.issue_path.exists())

    def intake_counts(self):
        connection = self.night_db.connection
        return (
            connection.execute('SELECT COUNT(*) FROM intake_volumes').fetchone()[0],
            connection.execute('SELECT COUNT(*) FROM intake_items').fetchone()[0],
            connection.execute('SELECT COUNT(*) FROM intake_first_receipts').fetchone()[0],
        )

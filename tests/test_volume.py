import hashlib
import json

from intake.admission import accept_volume
from ledger.posting import post_ledger
from order.freeze import fix_order
from storage import ConflictError, MissingError, ValidationError
from study.imports import OriginalReading, import_batch
from study.revisions import freeze_revision
from tests.media_fixture import InterruptedAppend, RecordingMedia
from tests.support import ExchangeCase
from writer.media import FileMedia
from writer.repository import VolumeRepository
from writer.seal import read_volume, write_volume


class VolumeTests(ExchangeCase):
    def test_requested_order_equal_bodies_and_utf8_offsets(self):
        import_batch(self.study, 'same', self.calibration,
                     (OriginalReading('A', 20, '半格'), OriginalReading('C', 20, '半格')))
        freeze_revision(self.study, 'same', 'R1')
        volume = self.issue('same-order', ('C', 'A'), batch_id='same', revision_id='R1')
        order = self.orders.get_order('same-order')
        self.assertEqual([(item.ordinal, item.origin) for item in order.items], [(0, 'C'), (1, 'A')])
        text = '{"original_unit":"半格","original_value":"20","target_unit":"格","value":"10","working_value":"20"}'
        body = text.encode('utf-8')
        self.assertGreater(len(body), len(text))
        self.assertEqual(volume.payload, body * 2)
        header = json.loads(volume.envelope)
        self.assertEqual(header['origins'], ['C', 'A'])
        entries = header['directory']
        self.assertEqual([(e['ordinal'], e['origin'], e['start'], e['end']) for e in entries],
                         [(0, 'C', 0, len(body)), (1, 'A', len(body), 2 * len(body))])
        for entry in entries:
            self.assertEqual(volume.payload[entry['start']:entry['end']], body)
            self.assertEqual(entry['sha256'], hashlib.sha256(body).hexdigest())
        received = accept_volume(self.intake, volume)
        self.assertEqual([(item.origin, item.value) for item in received.items], [('C', 10), ('A', 10)])
        report = post_ledger(self.ledgers, 'night', received.first_receipt.receipt_id, 'same-account')
        self.assertEqual((report.stored.totals.valid_count, report.stored.totals.total), (2, 20))

    def test_reopens_fixed_seal(self):
        self.prepare_workday()
        original = self.issue('original', ('C', 'A', 'B'))
        self.issue_db.close()
        reopened = VolumeRepository(self.open_database(self.issue_path))
        reopened.initialize()
        self.assertEqual(read_volume(reopened, original.seal_id), original)
        with self.assertRaises(MissingError):
            read_volume(reopened, 'missing-seal')

    def test_duplicate_selection_has_no_order_or_volume(self):
        self.prepare_workday()
        with self.assertRaisesRegex(ValidationError, 'duplicate selected origin'):
            fix_order(self.orders, self.study, 'workday', 'R2', 'bad', ('A', 'A'))
        self.assert_unavailable_order('bad')

    def test_unknown_selection_has_no_order_or_volume(self):
        self.prepare_workday()
        with self.assertRaisesRegex(ValidationError, 'unknown selected origin'):
            fix_order(self.orders, self.study, 'workday', 'R2', 'bad', ('A', 'unknown'))
        self.assert_unavailable_order('bad')

    def test_order_conflict_preserves_fixed_selection(self):
        self.prepare_workday()
        old = fix_order(self.orders, self.study, 'workday', 'R1', 'taken', ('B', 'A'))
        with self.assertRaises(ConflictError):
            fix_order(self.orders, self.study, 'workday', 'R2', 'taken', ('C',))
        self.assertEqual(self.orders.get_order('taken'), old)
        self.assertEqual(self.issue_db.connection.execute('SELECT COUNT(*) FROM writer_volumes').fetchone()[0], 0)

    def test_interrupted_write_keeps_prefix_without_new_seal(self):
        self.prepare_workday()
        old = self.issue('old', ('C', 'A', 'B'))
        fix_order(self.orders, self.study, 'workday', 'R2', 'interrupted', ('C', 'A', 'B'))
        path = self.root / 'interrupted.media'
        cut = len(old.payload) // 2
        media = RecordingMedia(path, interrupt_after=cut)
        with self.assertRaises(InterruptedAppend):
            write_volume(self.volumes, self.orders, 'interrupted', media)
        self.assertEqual(media.ranges, [(0, cut)])
        reopened_media = FileMedia(path)
        self.assertEqual(reopened_media.read_all(), old.payload[:cut])
        with self.assertRaisesRegex(ValidationError, 'empty candidate'):
            write_volume(self.volumes, self.orders, 'interrupted', reopened_media)
        self.assertEqual(reopened_media.read_all(), old.payload[:cut])
        self.issue_db.close()
        reopened = VolumeRepository(self.open_database(self.issue_path))
        self.assertEqual(read_volume(reopened, old.seal_id), old)
        self.assertEqual(reopened.db.connection.execute('SELECT seal_id FROM writer_volumes').fetchall()[0][0],
                         old.seal_id)
        self.assertEqual(reopened.db.connection.execute('SELECT COUNT(*) FROM writer_volumes').fetchone()[0], 1)

    def test_full_append_without_reply_has_no_v1_seal(self):
        self.prepare_workday()
        reference = self.issue('reference', ('C',))
        fix_order(self.orders, self.study, 'workday', 'R2', 'lost-reply', ('C',))
        path = self.root / 'lost-reply.media'
        media = RecordingMedia(path, lose_reply=True)
        with self.assertRaises(InterruptedAppend):
            write_volume(self.volumes, self.orders, 'lost-reply', media)
        self.assertEqual(media.ranges, [(0, len(reference.payload))])
        self.assertEqual(FileMedia(path).read_all(), reference.payload)
        self.assertEqual(self.issue_db.connection.execute('SELECT COUNT(*) FROM writer_volumes').fetchone()[0], 1)

    def test_exchanges_empty_order(self):
        self.prepare_workday()
        volume = self.issue('empty', ())
        self.assertEqual(volume.payload, b'')
        self.assertEqual(json.loads(volume.envelope)['directory'], [])
        accepted = accept_volume(self.intake, volume)
        report = post_ledger(self.ledgers, 'night', accepted.first_receipt.receipt_id, 'empty-account')
        self.assertEqual(report.items, ())
        self.assertEqual((report.stored.totals.valid_count, report.stored.totals.total), (0, 0))

    def assert_unavailable_order(self, order_id):
        path = self.root / 'unused.media'
        with self.assertRaises(MissingError):
            write_volume(self.volumes, self.orders, order_id, FileMedia(path))
        self.assertFalse(path.exists())
        self.assertEqual(self.issue_db.connection.execute('SELECT COUNT(*) FROM orders_fixed').fetchone()[0], 0)
        self.assertEqual(self.issue_db.connection.execute('SELECT COUNT(*) FROM writer_volumes').fetchone()[0], 0)

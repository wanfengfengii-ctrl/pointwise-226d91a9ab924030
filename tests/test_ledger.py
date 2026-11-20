import json

from intake.admission import accept_volume
from ledger.history import read_ledger
from ledger.posting import post_ledger
from storage import ConflictError, MissingError
from study.corrections import Correction
from study.imports import OriginalReading, import_batch
from study.revisions import freeze_revision
from tests.support import ExchangeCase


class LedgerTests(ExchangeCase):
    def test_missing_zero_and_nonzero_count_once(self):
        self.prepare_workday()
        volume = self.issue('count', ('C', 'A', 'B'))
        self.detach_issuer()
        first = accept_volume(self.intake, volume)
        self.assertEqual(accept_volume(self.intake, volume), first)
        report = post_ledger(self.ledgers, 'night', first.first_receipt.receipt_id, 'count-account')
        self.assertEqual(self.intake_counts(), (1, 3, 1))
        self.assertEqual((report.stored.totals.valid_count, report.stored.totals.total), (2, 11))
        self.assertEqual(report.stored.target_unit, '格')
        self.assertEqual([(item.origin, item.value) for item in report.items], [('C', 11), ('A', None), ('B', 0)])
        directory = json.loads(volume.envelope)['directory']
        self.assertEqual([(item.start, item.end) for item in report.items],
                         [(entry['start'], entry['end']) for entry in directory])
        self.assertTrue(all(item.seal_id == volume.seal_id for item in report.items))

    def test_large_exact_sum_survives_reopen(self):
        import_batch(self.study, 'large', self.calibration,
                     (OriginalReading('A', 9223372036854775807, '格'), OriginalReading('B', 1, '格')))
        freeze_revision(self.study, 'large', 'R1')
        volume = self.issue('large', ('A', 'B'), batch_id='large', revision_id='R1')
        accepted = accept_volume(self.intake, volume)
        report = post_ledger(self.ledgers, 'night', accepted.first_receipt.receipt_id, 'large-account')
        self.assertEqual((report.stored.totals.valid_count, report.stored.totals.total), (2, 9223372036854775808))
        self.reopen_night()
        reread = read_ledger(self.ledgers, 'large-account')
        self.assertEqual(reread, report)
        self.assertIs(type(reread.stored.totals.total), int)
        self.assertEqual([item.value for item in reread.items], [9223372036854775807, 1])

    def test_unknown_receipt_leaves_no_account(self):
        with self.assertRaises(MissingError):
            post_ledger(self.ledgers, 'night', 1, 'not-posted')
        with self.assertRaises(MissingError):
            read_ledger(self.ledgers, 'not-posted')
        self.assertEqual(self.night_db.connection.execute('SELECT COUNT(*) FROM ledger_sources').fetchone()[0], 0)

    def test_account_conflict_keeps_entire_old_report(self):
        self.prepare_workday()
        first = accept_volume(self.intake, self.issue('first', ('C', 'A', 'B')))
        other = accept_volume(self.intake, self.issue('other', ('A',)))
        report = post_ledger(self.ledgers, 'night', first.first_receipt.receipt_id, 'occupied')
        with self.assertRaises(ConflictError):
            post_ledger(self.ledgers, 'dawn', other.first_receipt.receipt_id, 'occupied')
        self.assertEqual(read_ledger(self.ledgers, 'occupied'), report)
        self.assertEqual(self.night_db.connection.execute('SELECT COUNT(*) FROM ledger_sources').fetchone()[0], 3)

    def test_history_survives_source_revision_and_disconnection(self):
        self.prepare_workday()
        volume = self.issue('history', ('C', 'A', 'B'))
        accepted = accept_volume(self.intake, volume)
        report = post_ledger(self.ledgers, 'night', accepted.first_receipt.receipt_id, 'archive')
        later = freeze_revision(self.study, 'workday', 'R3', (Correction('C', 24),))
        self.assertEqual(later.readings[2].value, 12)
        self.detach_issuer()
        self.reopen_night()
        history = read_ledger(self.ledgers, 'archive')
        self.assertEqual(history, report)
        first = history.items[0]
        self.assertEqual((first.seal_id, first.batch_id, first.revision_id, first.origin),
                         (volume.seal_id, 'workday', 'R2', 'C'))
        self.assertEqual((first.original_value, first.working_value, first.value), (20, 22, 11))
        self.assertEqual(json.loads(volume.payload[first.start:first.end])['value'], '11')

    def test_history_resolves_same_origin_to_its_original_receipt(self):
        self.prepare_workday()
        original_volume = self.issue('old', ('C', 'A', 'B'))
        first = accept_volume(self.intake, original_volume)
        old = post_ledger(self.ledgers, 'night', first.first_receipt.receipt_id, 'old-account')
        freeze_revision(self.study, 'workday', 'R3', (Correction('C', 24),))
        later_volume = self.issue('later', ('A', 'B', 'C'), revision_id='R3')
        later = accept_volume(self.intake, later_volume)
        post_ledger(self.ledgers, 'dawn', later.first_receipt.receipt_id, 'later-account')
        self.assertEqual((later.items[2].origin, later.items[2].value), ('C', 12))
        self.assertGreater(later.items[2].start, 0)
        self.detach_issuer()
        self.reopen_night()
        recovered = read_ledger(self.ledgers, 'old-account')
        self.assertEqual(recovered, old)
        item = recovered.items[0]
        self.assertEqual((item.origin, item.start, item.value, item.seal_id, item.revision_id),
                         ('C', 0, 11, original_volume.seal_id, 'R2'))
        self.assertEqual(read_ledger(self.ledgers, 'later-account').items[2], later.items[2])

    def test_unknown_history_account_reports_missing(self):
        with self.assertRaises(MissingError):
            read_ledger(self.ledgers, 'missing')

    def test_reinitialize_preserves_receipt_book_and_sources(self):
        self.prepare_workday()
        volume = self.issue('persisted', ('C', 'A', 'B'))
        accepted = accept_volume(self.intake, volume)
        posted = post_ledger(self.ledgers, 'night', accepted.first_receipt.receipt_id, 'persisted-account')
        self.reopen_night()
        for _ in range(2):
            self.intake.initialize()
            self.ledgers.initialize()
        self.assertEqual(accept_volume(self.intake, volume), accepted)
        self.assertEqual(read_ledger(self.ledgers, 'persisted-account'), posted)
        self.assertEqual(self.intake_counts(), (1, 3, 1))

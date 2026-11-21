import json
import sqlite3
from dataclasses import replace
from pathlib import Path

from intake.admission import accept_volume
from ledger.history import read_ledger
from ledger.posting import post_ledger
from order.freeze import fix_order
from storage import ConflictError, MissingError, ValidationError
from study.calibration import CalibrationRule, CalibrationTable
from study.corrections import Correction
from study.imports import OriginalReading, import_batch
from study.revisions import freeze_revision
from tests.media_fixture import InterruptedAppend, RecordingMedia
from tests.support import ExchangeCase
from writer.encoding import encode_v2_record
from writer.media import FileMedia
from writer.seal import append_record, begin_volume, read_volume, seal


def _encoded(header):
    return json.dumps(header, ensure_ascii=False).encode('utf-8')


class ShortMedia(FileMedia):
    """Write everything but report one byte less, like a short write."""

    def append(self, payload):
        super().append(payload)
        return len(payload) - 1


class ResumeTests(ExchangeCase):
    def prepare_resume_case(self):
        if getattr(self, 'resume', None) is not None:
            return self.resume
        sample = json.loads((Path(__file__).resolve().parent.parent
                             / 'examples' / 'resume_case.json').read_text(encoding='utf-8'))
        calibration = sample['calibration']
        table = CalibrationTable(calibration['version'], calibration['target_unit'],
                                 tuple(CalibrationRule(*rule) for rule in calibration['rules']))
        import_batch(self.study, sample['batch_id'], table,
                     (OriginalReading(*row) for row in sample['readings']))
        freeze_revision(self.study, sample['batch_id'], sample['revision_id'])
        fix_order(self.orders, self.study, sample['batch_id'], sample['revision_id'],
                  sample['order_id'], sample['origins'])
        self.resume = sample
        return sample

    def reopen_issue(self):
        self.issue_db.close()
        self.issue_db = self.open_database(self.issue_path)
        self.orders = type(self.orders)(self.issue_db)
        self.volumes = type(self.volumes)(self.issue_db)

    def record_lengths(self):
        order = self.orders.get_order(self.resume['order_id'])
        requests = dict(zip(self.resume['origins'], self.resume['requests']))
        return {item.origin: len(encode_v2_record(requests[item.origin], item))
                for item in order.items}

    def burn_resume_volume(self, volume_id='roll-night'):
        """Burn the resume sample: A loses a full reply, B leaves a strict prefix."""
        self.prepare_resume_case()
        lengths = self.record_lengths()
        cut = lengths['B'] // 2
        path = self.root / f'{volume_id}.media'
        begin_volume(self.volumes, self.orders, volume_id, self.resume['order_id'], FileMedia(path))
        lost = RecordingMedia(path, lose_reply=True)
        with self.assertRaises(InterruptedAppend):
            append_record(self.volumes, volume_id, 'qa-A', 'A', lost)
        a_len = lost.ranges[0][1]
        self.reopen_issue()
        a_range = append_record(self.volumes, volume_id, 'qa-A', 'A', FileMedia(path))
        fragmented = RecordingMedia(path, interrupt_after=cut)
        with self.assertRaises(InterruptedAppend):
            append_record(self.volumes, volume_id, 'qa-B', 'B', fragmented)
        self.reopen_issue()
        b_range = append_record(self.volumes, volume_id, 'qa-B', 'B', FileMedia(path))
        c_range = append_record(self.volumes, volume_id, 'qa-C', 'C', FileMedia(path))
        seal_id = seal(self.volumes, volume_id, FileMedia(path))
        return read_volume(self.volumes, seal_id), a_range, b_range, c_range, (a_len, a_len + cut)

    def test_resume_sample_matches_expected_intervals_and_totals(self):
        sample = self.prepare_resume_case()
        lengths = self.record_lengths()
        cut = lengths['B'] // 2
        path = self.root / 'roll-night.media'
        begin_volume(self.volumes, self.orders, 'roll-night', sample['order_id'], FileMedia(path))
        # A lands completely but the reply is lost.
        lost = RecordingMedia(path, lose_reply=True)
        with self.assertRaises(InterruptedAppend):
            append_record(self.volumes, 'roll-night', 'qa-A', 'A', lost)
        a_len = lost.ranges[0][1]
        self.assertEqual(lost.ranges, [(0, lengths['A'])])
        # Reclaiming A after reopening the book and the media appends nothing.
        self.reopen_issue()
        a_range = append_record(self.volumes, 'roll-night', 'qa-A', 'A', FileMedia(path))
        self.assertEqual(a_range, (0, a_len))
        self.assertEqual(len(FileMedia(path).read_all()), a_len)
        # B stops at a nonempty strict prefix; sealing is refused while B is incomplete.
        fragmented = RecordingMedia(path, interrupt_after=cut)
        with self.assertRaises(InterruptedAppend):
            append_record(self.volumes, 'roll-night', 'qa-B', 'B', fragmented)
        self.assertEqual(fragmented.ranges, [(a_len, a_len + cut)])
        with self.assertRaisesRegex(ValidationError, 'completely written'):
            seal(self.volumes, 'roll-night', FileMedia(path))
        # After reopening, B's complete interval starts right behind the fragment.
        self.reopen_issue()
        b_range = append_record(self.volumes, 'roll-night', 'qa-B', 'B', FileMedia(path))
        self.assertEqual(b_range, (a_len + cut, a_len + cut + lengths['B']))
        c_range = append_record(self.volumes, 'roll-night', 'qa-C', 'C', FileMedia(path))
        self.assertEqual(c_range, (b_range[1], b_range[1] + lengths['C']))
        seal_id = seal(self.volumes, 'roll-night', FileMedia(path))
        self.assertTrue(seal_id.startswith('V2-'))
        self.assertEqual(seal(self.volumes, 'roll-night', FileMedia(path)), seal_id)
        # Sealed requests stay reclaimable, even with the media detached.
        self.assertEqual(append_record(self.volumes, 'roll-night', 'qa-B', 'B', FileMedia(path)), b_range)
        volume = read_volume(self.volumes, seal_id)
        header = json.loads(volume.envelope)
        self.assertEqual(header['version'], 2)
        self.assertEqual(header['volume_id'], 'roll-night')
        self.assertEqual([(s['ordinal'], s['origin'], s['request_id'], s['start'], s['end'])
                          for s in header['selections']],
                         [(0, 'A', 'qa-A', *a_range), (1, 'B', 'qa-B', *b_range),
                          (2, 'C', 'qa-C', *c_range)])
        self.assertEqual(header['fragments'], [{'request_id': 'qa-B', 'start': a_len, 'end': a_len + cut}])
        self.assertEqual(len(volume.payload), c_range[1])
        # A and C share the reading body but keep separate physical provenance.
        a_record = json.loads(volume.payload[a_range[0]:a_range[1]])
        c_record = json.loads(volume.payload[c_range[0]:c_range[1]])
        self.assertEqual(a_record['reading'], c_record['reading'])
        self.assertNotEqual(volume.payload[a_range[0]:a_range[1]],
                            volume.payload[c_range[0]:c_range[1]])
        # The source side and the media disappear; only the sealed volume remains.
        self.detach_issuer()
        path.unlink()
        accepted = accept_volume(self.intake, volume)
        self.assertEqual([(item.origin, item.value, item.start, item.end) for item in accepted.items],
                         [('A', 11, *a_range), ('B', 0, *b_range), ('C', 11, *c_range)])
        self.assertEqual(accept_volume(self.intake, volume), accepted)
        report = post_ledger(self.ledgers, 'night', accepted.first_receipt.receipt_id, 'resume-account')
        self.assertEqual((report.stored.totals.valid_count, report.stored.totals.total), (3, 22))
        self.assertEqual(report.stored.target_unit, '格')
        self.reopen_night()
        self.assertEqual(read_ledger(self.ledgers, 'resume-account'), report)

    def test_every_append_breakpoint_recovers(self):
        sample = self.prepare_resume_case()
        fix_order(self.orders, self.study, sample['batch_id'], sample['revision_id'], 'single', ('A',))
        record = encode_v2_record('req-a', self.orders.get_order('single').items[0])
        for cut in range(0, len(record) + 1):
            with self.subTest(cut=cut):
                volume_id = f'vol-cut-{cut}'
                path = self.root / f'{volume_id}.media'
                media = RecordingMedia(path, interrupt_after=cut)
                begin_volume(self.volumes, self.orders, volume_id, 'single', media)
                with self.assertRaises(InterruptedAppend):
                    append_record(self.volumes, volume_id, 'req-a', 'A', media)
                self.assertEqual(media.ranges, [(0, cut)])
                self.reopen_issue()
                result = append_record(self.volumes, volume_id, 'req-a', 'A', FileMedia(path))
                if cut == len(record):
                    self.assertEqual(result, (0, len(record)))
                    self.assertEqual(FileMedia(path).read_all(), record)
                else:
                    self.assertEqual(result, (cut, cut + len(record)))
                    self.assertEqual(FileMedia(path).read_all(), record[:cut] + record)
                seal_id = seal(self.volumes, volume_id, FileMedia(path))
                volume = read_volume(self.volumes, seal_id)
                header = json.loads(volume.envelope)
                expected_fragments = ([] if cut in (0, len(record)) else
                                      [{'request_id': 'req-a', 'start': 0, 'end': cut}])
                self.assertEqual(header['fragments'], expected_fragments)
                accepted = accept_volume(self.intake, volume)
                self.assertEqual([(item.origin, item.start, item.end, item.value)
                                  for item in accepted.items],
                                 [('A', *result, 11)])

    def test_zero_byte_interruption_keeps_binding_across_reopen(self):
        self.prepare_resume_case()
        path = self.root / 'zero.media'
        media = RecordingMedia(path, interrupt_after=0)
        begin_volume(self.volumes, self.orders, 'vol-zero', 'night', media)
        with self.assertRaises(InterruptedAppend):
            append_record(self.volumes, 'vol-zero', 'req-1', 'A', media)
        self.assertEqual(media.read_all(), b'')
        self.reopen_issue()
        result = append_record(self.volumes, 'vol-zero', 'req-1', 'A', FileMedia(path))
        self.assertEqual(result, (0, result[1]))
        self.assertEqual(append_record(self.volumes, 'vol-zero', 'req-1', 'A', FileMedia(path)), result)
        with self.assertRaises(ConflictError):
            append_record(self.volumes, 'vol-zero', 'req-1', 'B', FileMedia(path))
        with self.assertRaises(ConflictError):
            append_record(self.volumes, 'vol-zero', 'req-2', 'A', FileMedia(path))
        # Rejected calls neither appended bytes nor bound new requests.
        self.assertEqual(len(FileMedia(path).read_all()), result[1])
        b_range = append_record(self.volumes, 'vol-zero', 'req-2', 'B', FileMedia(path))
        self.assertEqual(b_range[0], result[1])

    def test_repeated_fragments_of_one_request_chain_in_order(self):
        sample = self.prepare_resume_case()
        lengths = self.record_lengths()
        first_cut, second_cut = lengths['B'] // 3, 2 * lengths['B'] // 3
        path = self.root / 'multi.media'
        begin_volume(self.volumes, self.orders, 'vol-multi', sample['order_id'], FileMedia(path))
        a_range = append_record(self.volumes, 'vol-multi', 'qa-A', 'A', FileMedia(path))
        for cut in (first_cut, second_cut):
            with self.assertRaises(InterruptedAppend):
                append_record(self.volumes, 'vol-multi', 'qa-B', 'B',
                              RecordingMedia(path, interrupt_after=cut))
            self.reopen_issue()
        b_range = append_record(self.volumes, 'vol-multi', 'qa-B', 'B', FileMedia(path))
        c_range = append_record(self.volumes, 'vol-multi', 'qa-C', 'C', FileMedia(path))
        seal_id = seal(self.volumes, 'vol-multi', FileMedia(path))
        volume = read_volume(self.volumes, seal_id)
        header = json.loads(volume.envelope)
        self.assertEqual(header['fragments'], [
            {'request_id': 'qa-B', 'start': a_range[1], 'end': a_range[1] + first_cut},
            {'request_id': 'qa-B', 'start': a_range[1] + first_cut, 'end': a_range[1] + first_cut + second_cut},
        ])
        self.assertEqual(b_range, (a_range[1] + first_cut + second_cut,
                                   a_range[1] + first_cut + second_cut + lengths['B']))
        accepted = accept_volume(self.intake, volume)
        self.assertEqual([(item.origin, item.start, item.end) for item in accepted.items],
                         [('A', *a_range), ('B', *b_range), ('C', *c_range)])
        report = post_ledger(self.ledgers, 'night', accepted.first_receipt.receipt_id, 'multi-account')
        self.assertEqual((report.stored.totals.valid_count, report.stored.totals.total), (3, 22))

    def test_begin_volume_registration_rules(self):
        self.prepare_resume_case()
        with self.assertRaises(MissingError):
            begin_volume(self.volumes, self.orders, 'v1', 'unknown-order', FileMedia(self.root / 'x.media'))
        used = self.root / 'used.media'
        used.write_bytes(b'junk')
        with self.assertRaisesRegex(ValidationError, 'empty candidate'):
            begin_volume(self.volumes, self.orders, 'v2', 'night', FileMedia(used))
        with self.assertRaises(MissingError):
            append_record(self.volumes, 'v2', 'r', 'A', FileMedia(used))
        path = self.root / 'bound.media'
        begin_volume(self.volumes, self.orders, 'v3', 'night', FileMedia(path))
        fix_order(self.orders, self.study, 'resume', 'R1', 'other-order', ('A',))
        with self.assertRaises(ConflictError):
            begin_volume(self.volumes, self.orders, 'v3', 'other-order', FileMedia(path))
        append_record(self.volumes, 'v3', 'r1', 'A', FileMedia(path))
        self.reopen_issue()
        begin_volume(self.volumes, self.orders, 'v3', 'night', FileMedia(path))
        with self.assertRaises(ConflictError):
            begin_volume(self.volumes, self.orders, 'v3', 'other-order', FileMedia(path))

    def test_append_record_validation_leaves_no_trace(self):
        self.prepare_resume_case()
        path = self.root / 'seq.media'
        begin_volume(self.volumes, self.orders, 'vol-seq', 'night', FileMedia(path))
        with self.assertRaises(MissingError):
            append_record(self.volumes, 'unknown-vol', 'r', 'A', FileMedia(path))
        with self.assertRaisesRegex(ValidationError, 'unknown selected origin'):
            append_record(self.volumes, 'vol-seq', 'r1', 'Z', FileMedia(path))
        with self.assertRaisesRegex(ValidationError, 'next incomplete'):
            append_record(self.volumes, 'vol-seq', 'r1', 'B', FileMedia(path))
        with self.assertRaisesRegex(ValidationError, 'next incomplete'):
            append_record(self.volumes, 'vol-seq', 'r1', 'C', FileMedia(path))
        self.assertEqual(FileMedia(path).read_all(), b'')
        count = self.issue_db.connection.execute('SELECT COUNT(*) FROM writer_v2_requests').fetchone()[0]
        self.assertEqual(count, 0)
        with self.assertRaises(MissingError):
            seal(self.volumes, 'unknown-vol', FileMedia(path))

    def test_empty_order_seals_directly(self):
        self.prepare_resume_case()
        fix_order(self.orders, self.study, 'resume', 'R1', 'empty-order', ())
        path = self.root / 'empty.media'
        begin_volume(self.volumes, self.orders, 'vol-empty', 'empty-order', FileMedia(path))
        seal_id = seal(self.volumes, 'vol-empty', FileMedia(path))
        self.assertEqual(seal(self.volumes, 'vol-empty', FileMedia(path)), seal_id)
        volume = read_volume(self.volumes, seal_id)
        self.assertEqual(volume.payload, b'')
        header = json.loads(volume.envelope)
        self.assertEqual((header['selections'], header['fragments']), ([], []))
        accepted = accept_volume(self.intake, volume)
        self.assertEqual(accepted.items, ())
        report = post_ledger(self.ledgers, 'night', accepted.first_receipt.receipt_id, 'empty-v2')
        self.assertEqual((report.stored.totals.valid_count, report.stored.totals.total), (0, 0))

    def test_short_append_reports_oserror_and_recovers_from_media(self):
        self.prepare_resume_case()
        path = self.root / 'short.media'
        begin_volume(self.volumes, self.orders, 'vol-short', 'night', FileMedia(path))
        with self.assertRaisesRegex(OSError, 'incomplete V2 media append'):
            append_record(self.volumes, 'vol-short', 'r-a', 'A', ShortMedia(path))
        # The full record did land; recovery confirms it without appending again.
        self.reopen_issue()
        result = append_record(self.volumes, 'vol-short', 'r-a', 'A', FileMedia(path))
        self.assertEqual(result, (0, result[1]))
        self.assertEqual(len(FileMedia(path).read_all()), result[1])

    def test_foreign_or_truncated_media_is_refused(self):
        self.prepare_resume_case()
        path = self.root / 'foreign.media'
        begin_volume(self.volumes, self.orders, 'vol-f', 'night', FileMedia(path))
        a_range = append_record(self.volumes, 'vol-f', 'r-a', 'A', FileMedia(path))
        with path.open('ab') as stream:
            stream.write(b'garbage')
        with self.assertRaisesRegex(ValidationError, 'beyond the accounted'):
            append_record(self.volumes, 'vol-f', 'r-b', 'B', FileMedia(path))
        path.write_bytes(path.read_bytes()[:a_range[1] - 3])
        with self.assertRaisesRegex(ValidationError, 'shorter'):
            append_record(self.volumes, 'vol-f', 'r-a', 'A', FileMedia(path))

    def test_seal_publish_failure_leaves_no_claimable_volume(self):
        self.prepare_resume_case()
        path = self.root / 'pub.media'
        begin_volume(self.volumes, self.orders, 'vol-pub', 'night', FileMedia(path))
        for request_id, origin in (('r-a', 'A'), ('r-b', 'B'), ('r-c', 'C')):
            append_record(self.volumes, 'vol-pub', request_id, origin, FileMedia(path))
        connection = self.issue_db.connection
        connection.execute('''CREATE TRIGGER stop_publish BEFORE INSERT ON writer_volumes
                              BEGIN SELECT RAISE(ABORT, 'publish stopped'); END''')
        with self.assertRaisesRegex(sqlite3.IntegrityError, 'publish stopped'):
            seal(self.volumes, 'vol-pub', FileMedia(path))
        self.assertEqual(connection.execute('SELECT COUNT(*) FROM writer_volumes').fetchone()[0], 0)
        self.assertIsNone(self.volumes.get_v2_volume('vol-pub').seal_id)
        connection.execute('DROP TRIGGER stop_publish')
        seal_id = seal(self.volumes, 'vol-pub', FileMedia(path))
        volume = read_volume(self.volumes, seal_id)
        self.assertEqual(volume.payload, FileMedia(path).read_all())

    def test_tampered_v2_volume_is_rejected_as_a_whole(self):
        volume, a_range, b_range, c_range, fragment = self.burn_resume_volume()
        self.detach_issuer()
        # Truncating B's complete write breaks the ranges and the coverage.
        with self.assertRaisesRegex(ValidationError, 'range|accounted'):
            accept_volume(self.intake, replace(volume, payload=volume.payload[:b_range[1] - 5]))
        # Swapping A and C provenance contradicts the record identities.
        swapped = json.loads(volume.envelope)
        first, third = swapped['selections'][0], swapped['selections'][2]
        for field in ('start', 'end', 'sha256'):
            first[field], third[field] = third[field], first[field]
        with self.assertRaisesRegex(ValidationError, 'identity|checksum|accounted|order'):
            accept_volume(self.intake, replace(volume, envelope=_encoded(swapped)))
        # Altering a completed body breaks its checksum.
        pos = a_range[0] + 10
        altered = volume.payload[:pos] + bytes([volume.payload[pos] ^ 1]) + volume.payload[pos + 1:]
        with self.assertRaisesRegex(ValidationError, 'checksum'):
            accept_volume(self.intake, replace(volume, payload=altered))
        # Corrupting B's fragment breaks the strict-prefix proof.
        corrupted = (volume.payload[:fragment[0]] + bytes([volume.payload[fragment[0]] ^ 1])
                     + volume.payload[fragment[0] + 1:])
        with self.assertRaisesRegex(ValidationError, 'prefix'):
            accept_volume(self.intake, replace(volume, payload=corrupted))
        # Dropping the fragment entry leaves media bytes without attribution.
        dropped = json.loads(volume.envelope)
        dropped['fragments'] = []
        with self.assertRaisesRegex(ValidationError, 'accounted'):
            accept_volume(self.intake, replace(volume, envelope=_encoded(dropped)))
        # Reassigning the fragment to another request fails the prefix proof.
        reassigned = json.loads(volume.envelope)
        reassigned['fragments'][0]['request_id'] = 'qa-A'
        with self.assertRaisesRegex(ValidationError, 'prefix'):
            accept_volume(self.intake, replace(volume, envelope=_encoded(reassigned)))
        reassigned['fragments'][0]['request_id'] = 'qa-Z'
        with self.assertRaisesRegex(ValidationError, 'unknown request'):
            accept_volume(self.intake, replace(volume, envelope=_encoded(reassigned)))
        self.assertEqual(self.intake_counts(), (0, 0, 0))
        accepted = accept_volume(self.intake, volume)
        self.assertEqual([item.origin for item in accepted.items], ['A', 'B', 'C'])
        self.assertEqual(self.intake_counts(), (1, 3, 1))

    def test_v2_replay_and_same_seal_different_bytes(self):
        volume, *_ = self.burn_resume_volume()
        other, *_ = self.burn_resume_volume('roll-other')
        first = accept_volume(self.intake, volume)
        self.reopen_night()
        self.assertEqual(accept_volume(self.intake, volume), first)
        header = json.loads(other.envelope)
        header['seal_id'] = volume.seal_id
        conflict = replace(other, seal_id=volume.seal_id, envelope=_encoded(header))
        with self.assertRaises(ConflictError):
            accept_volume(self.intake, conflict)
        self.assertEqual(self.intake_counts(), (1, 3, 1))
        self.assertEqual(accept_volume(self.intake, volume), first)

    def test_version_field_alone_does_not_change_the_format(self):
        volume, *_ = self.burn_resume_volume()
        header = json.loads(volume.envelope)
        header['version'] = 1
        with self.assertRaisesRegex(ValidationError, 'unsupported volume version'):
            accept_volume(self.intake, replace(volume, envelope=_encoded(header)))
        self.prepare_workday()
        v1 = self.issue('v1-format', ('C',))
        v1_header = json.loads(v1.envelope)
        v1_header['version'] = 2
        with self.assertRaisesRegex(ValidationError, 'unsupported volume version'):
            accept_volume(self.intake, replace(v1, envelope=_encoded(v1_header)))
        self.assertEqual(self.intake_counts(), (0, 0, 0))

    def test_v2_history_survives_source_disconnection(self):
        volume, a_range, b_range, c_range, fragment = self.burn_resume_volume()
        accepted = accept_volume(self.intake, volume)
        report = post_ledger(self.ledgers, 'night', accepted.first_receipt.receipt_id, 'v2-archive')
        later = freeze_revision(self.study, 'resume', 'R2', (Correction('A', 22),))
        self.assertEqual(later.readings[0].value, 22)
        self.detach_issuer()
        (self.root / 'roll-night.media').unlink()
        self.reopen_night()
        history = read_ledger(self.ledgers, 'v2-archive')
        self.assertEqual(history, report)
        self.assertEqual([(item.origin, item.start, item.end, item.value) for item in history.items],
                         [('A', *a_range, 11), ('B', *b_range, 0), ('C', *c_range, 11)])
        self.assertTrue(all(item.seal_id == volume.seal_id for item in history.items))

    def test_v1_and_v2_volumes_coexist(self):
        self.prepare_workday()
        v1 = self.issue('v1-order', ('C', 'A', 'B'))
        v2, a_range, b_range, c_range, _ = self.burn_resume_volume()
        self.assertTrue(v1.seal_id.startswith('V1-'))
        self.assertTrue(v2.seal_id.startswith('V2-'))
        first = accept_volume(self.intake, v1)
        second = accept_volume(self.intake, v2)
        self.assertEqual([item.value for item in first.items], [11, None, 0])
        self.assertEqual([item.value for item in second.items], [11, 0, 11])
        old = post_ledger(self.ledgers, 'night', first.first_receipt.receipt_id, 'mix-old')
        new = post_ledger(self.ledgers, 'dawn', second.first_receipt.receipt_id, 'mix-new')
        self.assertEqual((old.stored.totals.valid_count, old.stored.totals.total), (2, 11))
        self.assertEqual((new.stored.totals.valid_count, new.stored.totals.total), (3, 22))
        self.reopen_night()
        self.assertEqual(read_ledger(self.ledgers, 'mix-old'), old)
        self.assertEqual(read_ledger(self.ledgers, 'mix-new'), new)

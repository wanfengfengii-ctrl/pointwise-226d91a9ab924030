from dataclasses import replace
from hashlib import sha256
import json
import sqlite3

from intake.admission import accept_volume
from ledger.history import read_ledger
from ledger.posting import post_ledger
from storage import ConflictError, MissingError, ValidationError
from tests.support import ExchangeCase


def _encoded_test_envelope(header):
    return json.dumps(header, ensure_ascii=False).encode('utf-8')


class IntakeTests(ExchangeCase):
    def test_tampered_body_refuses_new_receipt_and_keeps_old_book(self):
        self.prepare_workday()
        old = accept_volume(self.intake, self.issue('old', ('C', 'A', 'B')))
        report = post_ledger(self.ledgers, 'night', old.first_receipt.receipt_id, 'old-account')
        incoming = self.issue('incoming', ('C', 'B'))
        changed = incoming.payload.replace(b'"value":"11"', b'"value":"19"', 1)
        self.assertNotEqual(changed, incoming.payload)
        self.assertEqual(len(changed), len(incoming.payload))
        with self.assertRaisesRegex(ValidationError, 'checksum'):
            accept_volume(self.intake, replace(incoming, payload=changed))
        self.assertEqual(self.intake_counts(), (1, 3, 1))
        self.assertEqual(read_ledger(self.ledgers, 'old-account'), report)
        self.assertEqual(accept_volume(self.intake, incoming).items[0].value, 11)

    def test_rejects_directory_gap_overlap_overrun_and_uncovered_tail(self):
        self.prepare_workday()
        volume = self.issue('spans', ('C', 'B'))
        header = json.loads(volume.envelope)
        second_start = header['directory'][1]['start']
        cases = [('gap', 1, 'start', second_start + 1), ('overlap', 1, 'start', second_start - 1),
                 ('overrun', 1, 'end', len(volume.payload) + 1),
                 ('uncovered', 1, 'end', len(volume.payload) - 1)]
        for label, index, field, value in cases:
            with self.subTest(label=label):
                bad = json.loads(volume.envelope)
                directory = bad['directory']
                directory[index][field] = value
                if label == 'uncovered':
                    body = volume.payload[second_start:value]
                    directory[index]['sha256'] = sha256(body).hexdigest()
                with self.assertRaisesRegex(ValidationError, 'range|tail'):
                    accept_volume(self.intake, replace(volume, envelope=_encoded_test_envelope(bad)))
                self.assertEqual(self.intake_counts(), (0, 0, 0))

    def test_rejects_directory_order_conflicting_with_selection(self):
        self.prepare_workday()
        volume = self.issue('identity', ('C', 'A', 'B'))
        header = json.loads(volume.envelope)
        header['origins'] = ['A', 'C', 'B']
        with self.assertRaisesRegex(ValidationError, 'identity mismatch'):
            accept_volume(self.intake, replace(volume, envelope=_encoded_test_envelope(header)))
        self.assertEqual(self.intake_counts(), (0, 0, 0))

    def test_replays_persisted_first_receipt_after_reopen(self):
        self.prepare_workday()
        volume = self.issue('replay', ('C', 'A', 'B'))
        first = accept_volume(self.intake, volume)
        self.assertTrue(first.first_receipt.token)
        self.reopen_night()
        replay = accept_volume(self.intake, volume)
        self.assertEqual(replay, first)
        self.assertEqual(self.intake.get_receipt(first.first_receipt.receipt_id), first)
        self.assertEqual(self.intake_counts(), (1, 3, 1))

    def test_same_seal_with_different_self_consistent_bytes_conflicts(self):
        self.prepare_workday()
        first_volume = self.issue('first', ('C', 'B'))
        second_volume = self.issue('second', ('A', 'B'))
        first = accept_volume(self.intake, first_volume)
        incoming = json.loads(second_volume.envelope)
        incoming['seal_id'] = first_volume.seal_id
        conflict = replace(second_volume, seal_id=first_volume.seal_id, envelope=_encoded_test_envelope(incoming))
        self.assertNotEqual(conflict.payload, first_volume.payload)
        with self.assertRaises(ConflictError):
            accept_volume(self.intake, conflict)
        self.assertEqual(self.intake_counts(), (1, 2, 1))
        self.assertEqual(accept_volume(self.intake, first_volume), first)
        self.assertEqual([(item.origin, item.value) for item in first.items], [('C', 11), ('B', 0)])

    def test_rolls_back_whole_receipt_on_last_insert_failure(self):
        self.prepare_workday()
        volume = self.issue('rollback', ('C', 'A', 'B'))
        connection = self.night_db.connection
        connection.execute('''CREATE TRIGGER stop_receipt BEFORE INSERT ON intake_first_receipts
                              BEGIN SELECT RAISE(ABORT, 'receipt insert stopped'); END''')
        with self.assertRaisesRegex(sqlite3.IntegrityError, 'receipt insert stopped'):
            accept_volume(self.intake, volume)
        self.assertEqual(self.intake_counts(), (0, 0, 0))
        with self.assertRaises(MissingError):
            self.intake.get_receipt(1)
        connection.execute('DROP TRIGGER stop_receipt')
        accepted = accept_volume(self.intake, volume)
        self.assertEqual([item.origin for item in accepted.items], ['C', 'A', 'B'])
        self.assertEqual(self.intake_counts(), (1, 3, 1))

    def test_rejects_bad_body_fields_even_with_matching_span_and_checksum(self):
        self.prepare_workday()
        volume = self.issue('typed', ('C',))
        for field, value in (('value', True), ('value', '011'), ('working_value', None), ('target_unit', '米')):
            with self.subTest(field=field, value=value):
                header = json.loads(volume.envelope)
                reading = json.loads(volume.payload)
                reading[field] = value
                body = json.dumps(reading, ensure_ascii=False).encode('utf-8')
                header['directory'][0].update(end=len(body), sha256=sha256(body).hexdigest())
                with self.assertRaises(ValidationError):
                    accept_volume(self.intake, replace(volume, envelope=_encoded_test_envelope(header), payload=body))
                self.assertEqual(self.intake_counts(), (0, 0, 0))

    def test_rejects_unsupported_version_and_mismatched_outer_seal(self):
        self.prepare_workday()
        volume = self.issue('format', ('C',))
        header = json.loads(volume.envelope)
        header['version'] = 2
        with self.assertRaisesRegex(ValidationError, 'unsupported volume version'):
            accept_volume(self.intake, replace(volume, envelope=_encoded_test_envelope(header)))
        with self.assertRaisesRegex(ValidationError, 'outer seal id'):
            accept_volume(self.intake, replace(volume, seal_id='another-seal'))
        self.assertEqual(self.intake_counts(), (0, 0, 0))

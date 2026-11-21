import json
from pathlib import Path
from tempfile import TemporaryDirectory

from intake.admission import accept_volume
from intake.repository import IntakeRepository
from ledger.history import read_ledger
from ledger.posting import post_ledger
from ledger.repository import LedgerRepository
from order.freeze import fix_order
from order.repository import OrderRepository
from storage import Database, ValidationError
from study.calibration import CalibrationRule, CalibrationTable
from study.corrections import Correction
from study.imports import OriginalReading, import_batch
from study.repository import StudyRepository
from study.revisions import freeze_revision
from writer.encoding import encode_v2_record
from writer.media import FileMedia
from writer.repository import VolumeRepository
from writer.seal import append_record, begin_volume, read_volume, seal, write_volume


class ReplyLossMedia(FileMedia):
    """Write a chosen prefix (or everything) and then lose the append reply."""

    def __init__(self, path, cut=None):
        super().__init__(path)
        self.cut = cut
        self.writes = []

    def append(self, payload):
        limit = len(payload) if self.cut is None else self.cut
        start = len(self.read_all())
        written = super().append(payload[:limit])
        self.writes.append((start, start + written))
        raise OSError('the append reply was lost')


def _summarize(report):
    return {'count': report.stored.totals.valid_count, 'total': report.stored.totals.total,
            'unit': report.stored.target_unit, 'origins': [item.origin for item in report.items]}


def _scenario(case_id, inputs, expected, actual, downstream_expected, downstream_actual, paths, oracle):
    if actual != expected or downstream_actual != downstream_expected:
        raise RuntimeError(f'{case_id} did not meet the workday expectations')
    result = {'case_id': case_id, 'input': inputs, 'expected': expected, 'actual': actual,
              'downstream_expected': downstream_expected, 'downstream_actual': downstream_actual,
              'code_paths': paths, 'oracle': oracle}
    print('POINTWISE_SCENARIO ' + json.dumps(result, ensure_ascii=False, sort_keys=True))


def _calibration(data):
    table = data['calibration']
    return CalibrationTable(table['version'], table['target_unit'],
                            tuple(CalibrationRule(*rule) for rule in table['rules']))


def _workday_scenarios():
    sample = Path(__file__).resolve().parent / 'examples' / 'workday.json'
    data = json.loads(sample.read_text(encoding='utf-8'))
    table = _calibration(data)
    batch_id = data['batch_id']
    expected_totals = {'count': 2, 'total': 11, 'unit': '格', 'origins': ['C', 'A', 'B']}
    with TemporaryDirectory() as night_dir:
        with TemporaryDirectory() as issuer_dir:
            issuer = Path(issuer_dir)
            with Database(issuer / 'study.sqlite3') as study_db, Database(issuer / 'issue.sqlite3') as issue_db:
                study = StudyRepository(study_db)
                orders = OrderRepository(issue_db)
                volumes = VolumeRepository(issue_db)
                for repository in (study, orders, volumes):
                    repository.initialize()
                import_batch(study, batch_id, table, (OriginalReading(*row) for row in data['readings']))
                old = freeze_revision(study, batch_id, 'R1')
                current = freeze_revision(study, batch_id, 'R2', (Correction(*row) for row in data['corrections']))
                fix_order(orders, study, batch_id, 'R2', data['order_id'], data['origins'])
                seal = write_volume(volumes, orders, data['order_id'], FileMedia(issuer / 'workday.media'))
                volume = read_volume(volumes, seal)
                later = freeze_revision(study, batch_id, 'R3', (Correction(*row) for row in data['later_corrections']))
                calibrated = [old.readings[2].value, current.readings[2].value, later.readings[2].value]
        if Path(issuer_dir).exists():
            raise RuntimeError('workday demonstration requires a disconnected issuer')
        night = Path(night_dir) / 'intake.sqlite3'
        with Database(night) as night_db:
            intake = IntakeRepository(night_db)
            ledgers = LedgerRepository(night_db)
            intake.initialize()
            ledgers.initialize()
            accepted = accept_volume(intake, volume)
            replayed = accept_volume(intake, volume)
            report = post_ledger(ledgers, data['shift'], accepted.first_receipt.receipt_id, data['account'])
            measured = {'values': [item.value for item in accepted.items], 'fixed_first_receipt': replayed == accepted}
            _scenario('N7', {'readings': data['readings'], 'corrections': data['corrections'], 'origins': data['origins']},
                      {'values': [11, None, 0], 'fixed_first_receipt': True}, measured,
                      expected_totals, _summarize(report),
                      ['intake/admission.py', 'intake/repository.py', 'ledger/totals.py', 'ledger/posting.py'],
                      '22 half units make 11 units. Adding zero leaves 11 across two observed records.')
        with Database(night) as reopened:
            historical = read_ledger(LedgerRepository(reopened), data['account'])
            c = historical.items[0]
            source = {'origin': c.origin, 'revision': c.revision_id, 'original': c.original_value,
                      'working': c.working_value, 'value': c.value,
                      'matching_seal': c.seal_id == volume.seal_id,
                      'range_matches': c.start == 0 and volume.payload[c.start:c.end] == (
                          '{"original_unit":"半格","original_value":"20","target_unit":"格",'
                          '"value":"11","working_value":"22"}').encode('utf-8')}
            _scenario('N8', {'account': data['account'], 'later_corrections': data['later_corrections']},
                      [10, 11, 12], calibrated,
                      {'origin': 'C', 'revision': 'R2', 'original': 20, 'working': 22, 'value': 11,
                       'matching_seal': True, 'range_matches': True}, source,
                      ['study/revisions.py', 'ledger/history.py', 'ledger/provenance.py', 'intake/repository.py'],
                      'Divide 20, 22 and 24 by two. C is the first selected record and its R2 body starts at byte zero.')
            if historical != report or _summarize(historical) != expected_totals:
                raise RuntimeError('reopened report differs from the original night report')


def _resume_scenarios():
    sample = Path(__file__).resolve().parent / 'examples' / 'resume_case.json'
    data = json.loads(sample.read_text(encoding='utf-8'))
    table = _calibration(data)
    requests = dict(zip(data['origins'], data['requests']))
    account = 'resume-night'
    with TemporaryDirectory() as night_dir:
        with TemporaryDirectory() as issuer_dir:
            issuer = Path(issuer_dir)
            media_path = issuer / 'roll.media'
            with Database(issuer / 'study.sqlite3') as study_db, Database(issuer / 'issue.sqlite3') as issue_db:
                study = StudyRepository(study_db)
                orders = OrderRepository(issue_db)
                volumes = VolumeRepository(issue_db)
                for repository in (study, orders, volumes):
                    repository.initialize()
                import_batch(study, data['batch_id'], table,
                             (OriginalReading(*row) for row in data['readings']))
                freeze_revision(study, data['batch_id'], data['revision_id'])
                fix_order(orders, study, data['batch_id'], data['revision_id'],
                          data['order_id'], data['origins'])
                order = orders.get_order(data['order_id'])
                lengths = {item.origin: len(encode_v2_record(requests[item.origin], item))
                           for item in order.items}
                cut = lengths['B'] // 2
                expected_ranges = {'A': (0, lengths['A']),
                                   'B': (lengths['A'] + cut, lengths['A'] + cut + lengths['B']),
                                   'C': (lengths['A'] + cut + lengths['B'],
                                         lengths['A'] + cut + lengths['B'] + lengths['C'])}
                expected_fragment = (lengths['A'], lengths['A'] + cut)
                begin_volume(volumes, orders, data['volume_id'], data['order_id'], FileMedia(media_path))
                # A lands completely, but the reply never comes back.
                try:
                    append_record(volumes, data['volume_id'], requests['A'], 'A', ReplyLossMedia(media_path))
                    raise RuntimeError('a lost reply must surface as an error')
                except OSError:
                    pass
                landed = len(FileMedia(media_path).read_all())
                # Reclaiming A after reopening must not append again.
                a_range = append_record(volumes, data['volume_id'], requests['A'], 'A', FileMedia(media_path))
                reclaim_kept_media = len(FileMedia(media_path).read_all()) == landed
                # B stops at a nonempty strict prefix; sealing is refused until B is rewritten.
                fragmented = ReplyLossMedia(media_path, cut)
                try:
                    append_record(volumes, data['volume_id'], requests['B'], 'B', fragmented)
                    raise RuntimeError('a lost reply must surface as an error')
                except OSError:
                    pass
                fragment = fragmented.writes[-1]
                try:
                    seal(volumes, data['volume_id'], FileMedia(media_path))
                    raise RuntimeError('sealing must wait for every selection')
                except ValidationError:
                    seal_refused = True
                # The complete B is appended behind the preserved fragment.
                b_range = append_record(volumes, data['volume_id'], requests['B'], 'B', FileMedia(media_path))
                c_range = append_record(volumes, data['volume_id'], requests['C'], 'C', FileMedia(media_path))
                seal_id = seal(volumes, data['volume_id'], FileMedia(media_path))
                reseal_same = seal(volumes, data['volume_id'], FileMedia(media_path)) == seal_id
                volume = read_volume(volumes, seal_id)
                ranges = {'A': a_range, 'B': b_range, 'C': c_range}
                if len(volume.payload) != c_range[1] or fragment != expected_fragment:
                    raise RuntimeError('actual write intervals differ from the media record')
        if Path(issuer_dir).exists():
            raise RuntimeError('resume demonstration requires a disconnected issuer')
        night = Path(night_dir) / 'intake.sqlite3'
        with Database(night) as night_db:
            intake = IntakeRepository(night_db)
            ledgers = LedgerRepository(night_db)
            intake.initialize()
            ledgers.initialize()
            accepted = accept_volume(intake, volume)
            replayed = accept_volume(intake, volume)
            report = post_ledger(ledgers, 'night', accepted.first_receipt.receipt_id, account)
            burn = {'ranges': ranges, 'b_fragment': fragment, 'reclaim_kept_media': reclaim_kept_media,
                    'seal_refused_until_complete': seal_refused, 'reseal_same': reseal_same}
            receipt = {'values': [item.value for item in accepted.items],
                       'ranges': [(item.start, item.end) for item in accepted.items],
                       'fixed_first_receipt': replayed == accepted}
            _scenario('N9', {'volume_id': data['volume_id'], 'requests': data['requests']},
                      {'ranges': expected_ranges, 'b_fragment': expected_fragment,
                       'reclaim_kept_media': True, 'seal_refused_until_complete': True,
                       'reseal_same': True}, burn,
                      {'values': data['expected']['values'],
                       'ranges': [expected_ranges[origin] for origin in data['origins']],
                       'fixed_first_receipt': True}, receipt,
                      ['writer/seal.py', 'writer/encoding.py', 'writer/repository.py',
                       'intake/admission.py', 'intake/verification.py'],
                      'A landed in full before its reply was lost; B was rewritten behind its '
                      'preserved fragment; the night shift counts three records for 22 units.')
        with Database(night) as reopened:
            historical = read_ledger(LedgerRepository(reopened), account)
            recheck = {'ranges_match_burn': [(item.start, item.end) for item in historical.items]
                                             == [ranges[origin] for origin in data['origins']],
                       'matches_night_report': historical == report}
            _scenario('N10', {'account': account},
                      {'count': data['expected']['valid_count'], 'total': data['expected']['total'],
                       'unit': data['expected']['unit'], 'origins': data['origins']},
                      _summarize(historical),
                      {'ranges_match_burn': True, 'matches_night_report': True}, recheck,
                      ['intake/repository.py', 'ledger/posting.py', 'ledger/history.py',
                       'ledger/provenance.py'],
                      'With the issuer, the source and the media gone, the held volume alone '
                      'rebuilds the same book after reopening.')


def main():
    _workday_scenarios()
    _resume_scenarios()


if __name__ == '__main__':
    main()

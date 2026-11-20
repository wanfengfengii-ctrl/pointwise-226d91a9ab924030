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
from storage import Database
from study.calibration import CalibrationRule, CalibrationTable
from study.corrections import Correction
from study.imports import OriginalReading, import_batch
from study.repository import StudyRepository
from study.revisions import freeze_revision
from writer.media import FileMedia
from writer.repository import VolumeRepository
from writer.seal import read_volume, write_volume


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


def main():
    sample = Path(__file__).resolve().parent / 'examples' / 'workday.json'
    data = json.loads(sample.read_text(encoding='utf-8'))
    calibration = data['calibration']
    table = CalibrationTable(calibration['version'], calibration['target_unit'],
                             tuple(CalibrationRule(*rule) for rule in calibration['rules']))
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


if __name__ == '__main__':
    main()

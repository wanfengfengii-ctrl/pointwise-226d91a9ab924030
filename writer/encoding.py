from order.freeze import FixedOrder
from order.selection import SelectedReading
from storage import decimal_text, json_bytes
from writer.directory import DirectoryEntry


def encode_record(reading: SelectedReading) -> bytes:
    return json_bytes({
        'original_unit': reading.original_unit,
        'original_value': decimal_text(reading.original_value),
        'working_value': decimal_text(reading.working_value),
        'target_unit': reading.target_unit,
        'value': decimal_text(reading.value),
    })


def encode_v1_envelope(seal_id: str, order: FixedOrder,
                       directory: tuple[DirectoryEntry, ...]) -> bytes:
    return json_bytes({
        'version': 1,
        'seal_id': seal_id,
        'order_id': order.order_id,
        'batch_id': order.batch_id,
        'revision_id': order.revision_id,
        'calibration_version': order.calibration_version,
        'target_unit': order.target_unit,
        'origins': [reading.origin for reading in order.items],
        'directory': [
            {'ordinal': entry.ordinal, 'origin': entry.origin, 'start': entry.start,
             'end': entry.end, 'sha256': entry.sha256}
            for entry in directory
        ],
    })

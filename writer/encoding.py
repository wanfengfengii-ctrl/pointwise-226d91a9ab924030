from typing import TYPE_CHECKING

from order.freeze import FixedOrder
from order.selection import SelectedReading
from storage import decimal_text, json_bytes
from writer.directory import DirectoryEntry, V2SelectionEntry

if TYPE_CHECKING:
    from writer.repository import V2Fragment


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


def encode_v2_record(request_id: str, reading: SelectedReading) -> bytes:
    return json_bytes({
        'record': 2,
        'request_id': request_id,
        'ordinal': reading.ordinal,
        'origin': reading.origin,
        'reading': {
            'original_unit': reading.original_unit,
            'original_value': decimal_text(reading.original_value),
            'working_value': decimal_text(reading.working_value),
            'target_unit': reading.target_unit,
            'value': decimal_text(reading.value),
        },
    })


def encode_v2_envelope(seal_id: str, volume_id: str, order: FixedOrder,
                       selections: tuple[V2SelectionEntry, ...],
                       fragments: 'tuple[V2Fragment, ...]') -> bytes:
    return json_bytes({
        'version': 2,
        'seal_id': seal_id,
        'volume_id': volume_id,
        'order_id': order.order_id,
        'batch_id': order.batch_id,
        'revision_id': order.revision_id,
        'calibration_version': order.calibration_version,
        'target_unit': order.target_unit,
        'selections': [
            {'ordinal': selection.ordinal, 'origin': selection.origin,
             'request_id': selection.request_id, 'start': selection.start,
             'end': selection.end, 'sha256': selection.sha256}
            for selection in selections
        ],
        'fragments': [
            {'request_id': fragment.request_id, 'start': fragment.start, 'end': fragment.end}
            for fragment in fragments
        ],
    })

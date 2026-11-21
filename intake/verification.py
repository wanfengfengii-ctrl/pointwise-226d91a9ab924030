from dataclasses import dataclass
from hashlib import sha256

from intake.decoding import DecodedReading, ParsedEnvelope, ParsedV2Envelope, decode_v2_record
from storage import ValidationError


@dataclass(frozen=True)
class VerifiedEntry:
    ordinal: int
    origin: str
    start: int
    end: int
    body: bytes


@dataclass(frozen=True)
class VerifiedV2Selection:
    ordinal: int
    origin: str
    request_id: str
    start: int
    end: int
    reading: DecodedReading


def verify_v1_directory(envelope: ParsedEnvelope, payload: bytes) -> tuple[VerifiedEntry, ...]:
    if not isinstance(payload, bytes):
        raise ValidationError('media payload must be bytes')
    if len(envelope.directory) != len(envelope.origins):
        raise ValidationError('directory count does not match the frozen selection')
    if len(set(envelope.origins)) != len(envelope.origins):
        raise ValidationError('duplicate frozen origin')
    verified = []
    cursor = 0
    for ordinal, (origin, entry) in enumerate(zip(envelope.origins, envelope.directory, strict=True)):
        if entry.ordinal != ordinal or entry.origin != origin:
            raise ValidationError(f'directory identity mismatch at selection {ordinal}')
        if entry.start != cursor or not entry.start < entry.end <= len(payload):
            raise ValidationError(f'invalid directory byte range at selection {ordinal}')
        body = payload[entry.start:entry.end]
        if sha256(body).hexdigest() != entry.sha256:
            raise ValidationError(f'body checksum mismatch at selection {ordinal}')
        verified.append(VerifiedEntry(ordinal, origin, entry.start, entry.end, body))
        cursor = entry.end
    if cursor != len(payload):
        raise ValidationError('directory does not cover the media tail')
    return tuple(verified)


def verify_v2_volume(envelope: ParsedV2Envelope, payload: bytes) -> tuple[VerifiedV2Selection, ...]:
    if not isinstance(payload, bytes):
        raise ValidationError('media payload must be bytes')
    selections = envelope.selections
    for position, selection in enumerate(selections):
        if selection.ordinal != position:
            raise ValidationError('selection ordinal out of sequence')
    if len({selection.origin for selection in selections}) != len(selections):
        raise ValidationError('duplicate frozen origin')
    if len({selection.request_id for selection in selections}) != len(selections):
        raise ValidationError('duplicate request identity')
    by_request = {selection.request_id: selection for selection in selections}
    pieces = []
    for selection in selections:
        if not 0 <= selection.start < selection.end <= len(payload):
            raise ValidationError('invalid selection byte range')
        pieces.append((selection.start, selection.end, selection.ordinal, True))
    for fragment in envelope.fragments:
        if fragment.request_id not in by_request:
            raise ValidationError('fragment references an unknown request')
        if not 0 <= fragment.start < fragment.end <= len(payload):
            raise ValidationError('invalid fragment byte range')
        pieces.append((fragment.start, fragment.end, by_request[fragment.request_id].ordinal, False))
    verified = []
    for selection in selections:
        body = payload[selection.start:selection.end]
        if sha256(body).hexdigest() != selection.sha256:
            raise ValidationError('record checksum mismatch')
        record = decode_v2_record(body)
        identity = (record.request_id, record.ordinal, record.origin)
        if identity != (selection.request_id, selection.ordinal, selection.origin):
            raise ValidationError('record identity mismatch')
        if record.reading.target_unit != envelope.target_unit:
            raise ValidationError('target unit mismatch')
        verified.append(VerifiedV2Selection(selection.ordinal, selection.origin, selection.request_id,
                                            selection.start, selection.end, record.reading))
    for fragment in envelope.fragments:
        selection = by_request[fragment.request_id]
        complete = payload[selection.start:selection.end]
        piece = payload[fragment.start:fragment.end]
        if len(piece) >= len(complete) or not complete.startswith(piece):
            raise ValidationError('fragment is not a strict prefix of the request record')
    cursor = 0
    expected_ordinal = 0
    for start, end, ordinal, is_complete in sorted(pieces):
        if start != cursor:
            raise ValidationError('media bytes are not fully accounted')
        cursor = end
        if ordinal != expected_ordinal:
            raise ValidationError('media pieces are out of selection order')
        if is_complete:
            expected_ordinal += 1
    if cursor != len(payload):
        raise ValidationError('media bytes are not fully accounted')
    return tuple(verified)

from dataclasses import dataclass
import json
import re

from storage import ValidationError, decimal_value, require_text


@dataclass(frozen=True)
class EnvelopeEntry:
    ordinal: int
    origin: str
    start: int
    end: int
    sha256: str


@dataclass(frozen=True)
class ParsedEnvelope:
    seal_id: str
    order_id: str
    batch_id: str
    revision_id: str
    calibration_version: str
    target_unit: str
    origins: tuple[str, ...]
    directory: tuple[EnvelopeEntry, ...]


@dataclass(frozen=True)
class V2SelectionEntry:
    ordinal: int
    origin: str
    request_id: str
    start: int
    end: int
    sha256: str


@dataclass(frozen=True)
class V2FragmentEntry:
    request_id: str
    start: int
    end: int


@dataclass(frozen=True)
class ParsedV2Envelope:
    seal_id: str
    volume_id: str
    order_id: str
    batch_id: str
    revision_id: str
    calibration_version: str
    target_unit: str
    selections: tuple[V2SelectionEntry, ...]
    fragments: tuple[V2FragmentEntry, ...]


@dataclass(frozen=True)
class DecodedReading:
    original_unit: str
    target_unit: str
    original_value: int | None
    working_value: int | None
    value: int | None


@dataclass(frozen=True)
class DecodedV2Record:
    request_id: str
    ordinal: int
    origin: str
    reading: DecodedReading


_IDENTITY_FIELDS = ('seal_id', 'order_id', 'batch_id', 'revision_id', 'calibration_version', 'target_unit')
_ENVELOPE_FIELDS = {*_IDENTITY_FIELDS, 'version', 'origins', 'directory'}
_ENTRY_FIELDS = {'ordinal', 'origin', 'start', 'end', 'sha256'}
_RECORD_FIELDS = {'original_unit', 'target_unit', 'original_value', 'working_value', 'value'}
_V2_IDENTITY_FIELDS = ('seal_id', 'volume_id', 'order_id', 'batch_id', 'revision_id',
                       'calibration_version', 'target_unit')
_V2_ENVELOPE_FIELDS = {*_V2_IDENTITY_FIELDS, 'version', 'selections', 'fragments'}
_V2_SELECTION_FIELDS = {'ordinal', 'origin', 'request_id', 'start', 'end', 'sha256'}
_V2_FRAGMENT_FIELDS = {'request_id', 'start', 'end'}
_V2_RECORD_FIELDS = {'record', 'request_id', 'ordinal', 'origin', 'reading'}
_SHA256 = re.compile(r'[0-9a-f]{64}')


def _object_from_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValidationError(f'duplicate JSON field {key!r}')
        result[key] = value
    return result


def _reject_constant(value):
    raise ValidationError(f'invalid JSON constant {value!r}')


def _require_fields(value, fields, label):
    if not isinstance(value, dict) or value.keys() != fields:
        raise ValidationError(f'invalid fields for {label}')
    return value


def _decode_object(encoded, fields, label):
    if not isinstance(encoded, bytes):
        raise ValidationError(f'{label} must be UTF-8 bytes')
    try:
        data = json.loads(encoded.decode('utf-8'), object_pairs_hook=_object_from_pairs,
                          parse_constant=_reject_constant)
    except ValidationError:
        raise
    except (ValueError, RecursionError) as exc:
        raise ValidationError(f'invalid JSON for {label}') from exc
    if fields is None:
        if not isinstance(data, dict):
            raise ValidationError(f'invalid fields for {label}')
        return data
    return _require_fields(data, fields, label)


def _require_sha256(value, label):
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise ValidationError(f'invalid {label} SHA-256')
    return value


def _decode_entry(value):
    entry = _require_fields(value, _ENTRY_FIELDS, 'directory entry')
    for field in ('ordinal', 'start', 'end'):
        if type(entry[field]) is not int:
            raise ValidationError(f'directory {field} must be an integer')
    origin = require_text(entry['origin'], 'directory origin')
    digest = _require_sha256(entry['sha256'], 'directory')
    return EnvelopeEntry(entry['ordinal'], origin, entry['start'], entry['end'], digest)


def _parse_v1_envelope(data) -> ParsedEnvelope:
    if type(data['version']) is not int or data['version'] != 1:
        raise ValidationError('unsupported volume version')
    if not isinstance(data['origins'], list) or not isinstance(data['directory'], list):
        raise ValidationError('origins and directory must be ordered arrays')
    identities = [require_text(data[field], field) for field in _IDENTITY_FIELDS]
    origins = tuple(require_text(origin, 'selected origin') for origin in data['origins'])
    directory = tuple(_decode_entry(entry) for entry in data['directory'])
    return ParsedEnvelope(*identities, origins, directory)


def _decode_v2_selection(value):
    entry = _require_fields(value, _V2_SELECTION_FIELDS, 'selection entry')
    for field in ('ordinal', 'start', 'end'):
        if type(entry[field]) is not int:
            raise ValidationError(f'selection {field} must be an integer')
    origin = require_text(entry['origin'], 'selection origin')
    request_id = require_text(entry['request_id'], 'request id')
    digest = _require_sha256(entry['sha256'], 'selection')
    return V2SelectionEntry(entry['ordinal'], origin, request_id, entry['start'], entry['end'], digest)


def _decode_v2_fragment(value):
    entry = _require_fields(value, _V2_FRAGMENT_FIELDS, 'fragment entry')
    for field in ('start', 'end'):
        if type(entry[field]) is not int:
            raise ValidationError(f'fragment {field} must be an integer')
    request_id = require_text(entry['request_id'], 'request id')
    return V2FragmentEntry(request_id, entry['start'], entry['end'])


def _parse_v2_envelope(data) -> ParsedV2Envelope:
    if type(data['version']) is not int or data['version'] != 2:
        raise ValidationError('unsupported volume version')
    if not isinstance(data['selections'], list) or not isinstance(data['fragments'], list):
        raise ValidationError('selections and fragments must be ordered arrays')
    identities = [require_text(data[field], field) for field in _V2_IDENTITY_FIELDS]
    selections = tuple(_decode_v2_selection(entry) for entry in data['selections'])
    fragments = tuple(_decode_v2_fragment(entry) for entry in data['fragments'])
    return ParsedV2Envelope(*identities, selections, fragments)


def parse_envelope(encoded: bytes):
    data = _decode_object(encoded, None, 'volume envelope')
    if data.keys() == _ENVELOPE_FIELDS:
        return _parse_v1_envelope(data)
    if data.keys() == _V2_ENVELOPE_FIELDS:
        return _parse_v2_envelope(data)
    raise ValidationError('invalid fields for volume envelope')


def parse_v1_envelope(encoded: bytes) -> ParsedEnvelope:
    return _parse_v1_envelope(_decode_object(encoded, _ENVELOPE_FIELDS, 'volume envelope'))


def _decode_reading(data) -> DecodedReading:
    data = _require_fields(data, _RECORD_FIELDS, 'record body')
    original_unit = require_text(data['original_unit'], 'original unit')
    target_unit = require_text(data['target_unit'], 'target unit')
    original = decimal_value(data['original_value'])
    working = decimal_value(data['working_value'])
    value = decimal_value(data['value'])
    if (working is None) != (value is None):
        raise ValidationError('working and calibrated values disagree about missingness')
    return DecodedReading(original_unit, target_unit, original, working, value)


def decode_record(body: bytes) -> DecodedReading:
    return _decode_reading(_decode_object(body, _RECORD_FIELDS, 'record body'))


def decode_v2_record(encoded: bytes) -> DecodedV2Record:
    data = _decode_object(encoded, _V2_RECORD_FIELDS, 'record body')
    if type(data['record']) is not int or data['record'] != 2:
        raise ValidationError('unsupported record marker')
    request_id = require_text(data['request_id'], 'request id')
    origin = require_text(data['origin'], 'record origin')
    if type(data['ordinal']) is not int or data['ordinal'] < 0:
        raise ValidationError('record ordinal must be a nonnegative integer')
    reading = _decode_reading(data['reading'])
    return DecodedV2Record(request_id, data['ordinal'], origin, reading)

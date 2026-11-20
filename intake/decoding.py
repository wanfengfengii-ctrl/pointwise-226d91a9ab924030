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
class DecodedReading:
    original_unit: str
    target_unit: str
    original_value: int | None
    working_value: int | None
    value: int | None


_IDENTITY_FIELDS = ('seal_id', 'order_id', 'batch_id', 'revision_id', 'calibration_version', 'target_unit')
_ENVELOPE_FIELDS = {*_IDENTITY_FIELDS, 'version', 'origins', 'directory'}
_ENTRY_FIELDS = {'ordinal', 'origin', 'start', 'end', 'sha256'}
_RECORD_FIELDS = {'original_unit', 'target_unit', 'original_value', 'working_value', 'value'}
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
    return _require_fields(data, fields, label)


def _decode_entry(value):
    entry = _require_fields(value, _ENTRY_FIELDS, 'directory entry')
    for field in ('ordinal', 'start', 'end'):
        if type(entry[field]) is not int:
            raise ValidationError(f'directory {field} must be an integer')
    origin = require_text(entry['origin'], 'directory origin')
    digest = entry['sha256']
    if not isinstance(digest, str) or _SHA256.fullmatch(digest) is None:
        raise ValidationError('invalid directory SHA-256')
    return EnvelopeEntry(entry['ordinal'], origin, entry['start'], entry['end'], digest)


def parse_v1_envelope(encoded: bytes) -> ParsedEnvelope:
    data = _decode_object(encoded, _ENVELOPE_FIELDS, 'volume envelope')
    if type(data['version']) is not int or data['version'] != 1:
        raise ValidationError('unsupported volume version')
    if not isinstance(data['origins'], list) or not isinstance(data['directory'], list):
        raise ValidationError('origins and directory must be ordered arrays')
    identities = [require_text(data[field], field) for field in _IDENTITY_FIELDS]
    origins = tuple(require_text(origin, 'selected origin') for origin in data['origins'])
    directory = tuple(_decode_entry(entry) for entry in data['directory'])
    return ParsedEnvelope(*identities, origins, directory)


def decode_record(body: bytes) -> DecodedReading:
    data = _decode_object(body, _RECORD_FIELDS, 'record body')
    original_unit = require_text(data['original_unit'], 'original unit')
    target_unit = require_text(data['target_unit'], 'target unit')
    original = decimal_value(data['original_value'])
    working = decimal_value(data['working_value'])
    value = decimal_value(data['value'])
    if (working is None) != (value is None):
        raise ValidationError('working and calibrated values disagree about missingness')
    return DecodedReading(original_unit, target_unit, original, working, value)

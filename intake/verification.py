from dataclasses import dataclass
from hashlib import sha256

from intake.decoding import ParsedEnvelope
from storage import ValidationError


@dataclass(frozen=True)
class VerifiedEntry:
    ordinal: int
    origin: str
    start: int
    end: int
    body: bytes


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

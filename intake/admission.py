from __future__ import annotations

from typing import TYPE_CHECKING

from intake.decoding import ParsedV2Envelope, parse_envelope
from intake.receipts import AcceptedVolume, make_receipt_items, make_v2_receipt_items
from intake.repository import IntakeRepository
from intake.verification import verify_v1_directory, verify_v2_volume
from storage import ValidationError

if TYPE_CHECKING:
    from writer.repository import SealedVolume


def accept_volume(repository: IntakeRepository, volume: SealedVolume) -> AcceptedVolume:
    parsed = parse_envelope(volume.envelope)
    if volume.seal_id != parsed.seal_id:
        raise ValidationError('outer seal id differs from the envelope')
    if isinstance(parsed, ParsedV2Envelope):
        items = make_v2_receipt_items(parsed, verify_v2_volume(parsed, volume.payload))
    else:
        items = make_receipt_items(parsed, verify_v1_directory(parsed, volume.payload))
    return repository.accept_or_replay(parsed, volume.envelope, volume.payload, items)

from __future__ import annotations

from typing import TYPE_CHECKING

from intake.decoding import parse_v1_envelope
from intake.receipts import AcceptedVolume, make_receipt_items
from intake.repository import IntakeRepository
from intake.verification import verify_v1_directory
from storage import ValidationError

if TYPE_CHECKING:
    from writer.repository import SealedVolume


def accept_volume(repository: IntakeRepository, volume: SealedVolume) -> AcceptedVolume:
    parsed = parse_v1_envelope(volume.envelope)
    if volume.seal_id != parsed.seal_id:
        raise ValidationError('outer seal id differs from the envelope')
    verified = verify_v1_directory(parsed, volume.payload)
    items = make_receipt_items(parsed, verified)
    return repository.accept_or_replay(parsed, volume.envelope, volume.payload, items)

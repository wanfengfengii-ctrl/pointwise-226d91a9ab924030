from dataclasses import dataclass

from intake.decoding import ParsedEnvelope, ParsedV2Envelope, decode_record
from intake.verification import VerifiedEntry, VerifiedV2Selection
from storage import ValidationError


@dataclass(frozen=True)
class ReceiptItem:
    seal_id: str
    batch_id: str
    revision_id: str
    ordinal: int
    origin: str
    start: int
    end: int
    original_unit: str
    target_unit: str
    original_value: int | None
    working_value: int | None
    value: int | None


@dataclass(frozen=True)
class FirstReceipt:
    receipt_id: int
    seal_id: str
    token: str


@dataclass(frozen=True)
class AcceptedVolume:
    first_receipt: FirstReceipt
    order_id: str
    batch_id: str
    revision_id: str
    calibration_version: str
    target_unit: str
    items: tuple[ReceiptItem, ...]


def make_receipt_items(envelope: ParsedEnvelope,
                       entries: tuple[VerifiedEntry, ...]) -> tuple[ReceiptItem, ...]:
    items = []
    for entry in entries:
        reading = decode_record(entry.body)
        if reading.target_unit != envelope.target_unit:
            raise ValidationError(f'target unit mismatch at selection {entry.ordinal}')
        items.append(ReceiptItem(envelope.seal_id, envelope.batch_id, envelope.revision_id,
                                 entry.ordinal, entry.origin, entry.start, entry.end,
                                 reading.original_unit, reading.target_unit, reading.original_value,
                                 reading.working_value, reading.value))
    return tuple(items)


def make_v2_receipt_items(envelope: ParsedV2Envelope,
                          selections: tuple[VerifiedV2Selection, ...]) -> tuple[ReceiptItem, ...]:
    return tuple(
        ReceiptItem(envelope.seal_id, envelope.batch_id, envelope.revision_id,
                    selection.ordinal, selection.origin, selection.start, selection.end,
                    selection.reading.original_unit, selection.reading.target_unit,
                    selection.reading.original_value, selection.reading.working_value,
                    selection.reading.value)
        for selection in selections)

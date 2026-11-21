from hashlib import sha256

from order.repository import OrderRepository
from storage import ConflictError, MissingError, ValidationError, require_text
from writer.directory import V2SelectionEntry, build_directory
from writer.encoding import encode_record, encode_v2_record
from writer.media import AppendMedia
from writer.repository import SealedVolume, VolumeRepository, V2Volume


def write_volume(repository: VolumeRepository, orders: OrderRepository, order_id: str,
                 media: AppendMedia) -> str:
    order = orders.get_order(order_id)
    if media.read_all():
        raise ValidationError('a V1 volume requires empty candidate media')
    bodies = tuple(encode_record(reading) for reading in order.items)
    directory = build_directory(order.items, bodies)
    payload = b''.join(bodies)
    written = media.append(payload)
    if type(written) is not int or written != len(payload):
        raise OSError('incomplete V1 media append')
    recorded = media.read_all()
    if recorded != payload:
        raise OSError('V1 media readback differs from the fixed order')
    return repository.publish_volume(order, directory, recorded)


def read_volume(repository: VolumeRepository, seal_id: str) -> SealedVolume:
    return repository.get_volume(seal_id)


def begin_volume(repository: VolumeRepository, orders: OrderRepository, volume_id: str,
                 order_id: str, media: AppendMedia) -> None:
    require_text(volume_id, 'volume id')
    require_text(order_id, 'order id')
    existing = repository.get_v2_volume(volume_id)
    if existing is not None:
        if existing.order_id != order_id:
            raise ConflictError(f'volume {volume_id!r} is bound to order {existing.order_id!r}')
        return
    order = orders.get_order(order_id)
    if media.read_all():
        raise ValidationError('a new V2 volume requires empty candidate media')
    repository.register_v2_volume(volume_id, order.order_id)


def _accounted_length(requests: tuple, fragments: tuple) -> int:
    ends = [request.end for request in requests if request.end is not None]
    ends.extend(fragment.end for fragment in fragments)
    return max(ends, default=0)


def _recover_media(repository: VolumeRepository, volume: V2Volume, order, media: AppendMedia) -> bytes:
    """Fold the last unconfirmed append, if any, into the working book.

    Every append is preceded by this recovery, so at most one attempt can be
    unconfirmed: the bytes the last append left on the media. They are landing
    evidence even when the reply and the working book never confirmed them.
    """
    media_bytes = media.read_all()
    requests = repository.list_v2_requests(volume.volume_id)
    fragments = repository.list_v2_fragments(volume.volume_id)
    accounted = _accounted_length(requests, fragments)
    if len(media_bytes) < accounted:
        raise ValidationError('media is shorter than the recorded writes')
    if len(media_bytes) == accounted:
        return media_bytes
    pending = next((request for request in requests if request.end is None), None)
    if pending is None:
        raise ValidationError('media holds bytes beyond the accounted records')
    record = encode_v2_record(pending.request_id, order.items[pending.ordinal])
    tail = media_bytes[accounted:]
    if tail == record:
        repository.complete_v2_request(volume.volume_id, pending.request_id,
                                       accounted, accounted + len(record))
    elif len(tail) < len(record) and record.startswith(tail):
        repository.insert_v2_fragment(volume.volume_id, pending.request_id,
                                      accounted, len(media_bytes))
    else:
        raise ValidationError('media tail does not match the pending record')
    return media_bytes


def append_record(repository: VolumeRepository, volume_id: str, request_id: str,
                  origin: str, media: AppendMedia) -> tuple[int, int]:
    require_text(volume_id, 'volume id')
    require_text(request_id, 'request id')
    require_text(origin, 'selected origin')
    volume = repository.get_v2_volume(volume_id)
    if volume is None:
        raise MissingError(f'unknown volume {volume_id!r}')
    order = OrderRepository(repository.db).get_order(volume.order_id)
    if volume.seal_id is None:
        _recover_media(repository, volume, order, media)
    requests = repository.list_v2_requests(volume_id)
    by_request = {request.request_id: request for request in requests}
    by_origin = {request.origin: request for request in requests}
    bound = by_request.get(request_id)
    if bound is not None and bound.origin != origin:
        raise ConflictError(f'request {request_id!r} is bound to origin {bound.origin!r}')
    occupant = by_origin.get(origin)
    if occupant is not None and occupant.request_id != request_id:
        raise ConflictError(f'origin {origin!r} is bound to request {occupant.request_id!r}')
    if bound is not None and bound.end is not None:
        return (bound.start, bound.end)
    if bound is not None:
        target = order.items[bound.ordinal]
    else:
        completed = {request.origin for request in requests if request.end is not None}
        pending_item = next((item for item in order.items if item.origin not in completed), None)
        target = next((item for item in order.items if item.origin == origin), None)
        if target is None:
            raise ValidationError(f'unknown selected origin {origin!r}')
        if target is not pending_item:
            raise ValidationError(f'selection {origin!r} is not the next incomplete origin')
        repository.insert_v2_request(volume_id, request_id, target.ordinal, origin)
    record = encode_v2_record(request_id, target)
    start = _accounted_length(repository.list_v2_requests(volume_id),
                              repository.list_v2_fragments(volume_id))
    written = media.append(record)
    if type(written) is not int or written != len(record):
        raise OSError('incomplete V2 media append')
    end = start + written
    repository.complete_v2_request(volume_id, request_id, start, end)
    return (start, end)


def seal(repository: VolumeRepository, volume_id: str, media: AppendMedia) -> str:
    require_text(volume_id, 'volume id')
    volume = repository.get_v2_volume(volume_id)
    if volume is None:
        raise MissingError(f'unknown volume {volume_id!r}')
    if volume.seal_id is not None:
        return volume.seal_id
    order = OrderRepository(repository.db).get_order(volume.order_id)
    media_bytes = _recover_media(repository, volume, order, media)
    requests = repository.list_v2_requests(volume_id)
    fragments = repository.list_v2_fragments(volume_id)
    by_origin = {request.origin: request for request in requests}
    selections = []
    for item in order.items:
        request = by_origin.get(item.origin)
        if request is None or request.end is None:
            raise ValidationError('not every selection is completely written')
        body = media_bytes[request.start:request.end]
        selections.append(V2SelectionEntry(item.ordinal, item.origin, request.request_id,
                                           request.start, request.end, sha256(body).hexdigest()))
    if _accounted_length(requests, fragments) != len(media_bytes):
        raise ValidationError('media holds bytes beyond the accounted records')
    return repository.publish_v2_volume(volume_id, order, tuple(selections), fragments, media_bytes)

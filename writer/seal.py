from order.repository import OrderRepository
from storage import ValidationError
from writer.directory import build_directory
from writer.encoding import encode_record
from writer.media import AppendMedia
from writer.repository import SealedVolume, VolumeRepository


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

from typing import Collection, Iterable

from storage import ValidationError, require_text


def validate_selection(available_origins: Collection[str], origins: Iterable[str]) -> tuple[str, ...]:
    if isinstance(origins, (str, bytes)):
        raise ValidationError('expected an ordered collection of origins')
    requested = tuple(origins)
    seen = set()
    for origin in requested:
        require_text(origin, 'selected origin')
        if origin not in available_origins:
            raise ValidationError(f'unknown selected origin {origin!r}')
        if origin in seen:
            raise ValidationError(f'duplicate selected origin {origin!r}')
        seen.add(origin)
    return requested

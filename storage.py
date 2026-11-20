from contextlib import contextmanager
import json
import re
import sqlite3


class ValidationError(ValueError):
    pass


class ConflictError(ValueError):
    pass


class MissingError(KeyError):
    pass


class Database:
    def __init__(self, path):
        self.connection = sqlite3.connect(path, isolation_level=None)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute('PRAGMA foreign_keys = ON')

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()

    def close(self):
        self.connection.close()

    @contextmanager
    def transaction(self):
        connection = self.connection
        connection.execute('BEGIN IMMEDIATE')
        try:
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise

    def create_schema(self, *statements):
        with self.transaction() as connection:
            for statement in statements:
                connection.execute(statement)


def require_text(value, name):
    if not isinstance(value, str) or not value:
        raise ValidationError(f'{name} must be nonempty text')
    try:
        value.encode('utf-8')
    except UnicodeEncodeError as exc:
        raise ValidationError(f'{name} must be UTF-8 text') from exc
    return value


def require_reading_value(value):
    if value is not None and type(value) is not int:
        raise ValidationError('a measurement must be an integer or missing')
    return value


_DECIMAL = re.compile(r'0|-?[1-9][0-9]*')
_CHUNK_BASE = 10 ** 9


def decimal_text(value):
    if value is None:
        return None
    require_reading_value(value)
    sign = '-' if value < 0 else ''
    remaining = abs(value)
    chunks = []
    while remaining >= _CHUNK_BASE:
        remaining, chunk = divmod(remaining, _CHUNK_BASE)
        chunks.append(f'{chunk:09d}')
    return sign + str(remaining) + ''.join(reversed(chunks))


def decimal_value(value):
    if value is None:
        return None
    if not isinstance(value, str) or _DECIMAL.fullmatch(value) is None:
        raise ValidationError('invalid canonical decimal measurement')
    negative = value.startswith('-')
    digits = value[1:] if negative else value
    number = 0
    for start in range(0, len(digits), 9):
        chunk = digits[start:start + 9]
        number = number * 10 ** len(chunk) + int(chunk)
    return -number if negative else number


def json_bytes(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':'), allow_nan=False).encode('utf-8')

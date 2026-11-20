from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Mapping

from storage import ValidationError, require_reading_value, require_text


@dataclass(frozen=True)
class CalibrationRule:
    unit: str
    numerator: int
    denominator: int

    def __post_init__(self):
        require_text(self.unit, 'original unit')
        if type(self.numerator) is not int:
            raise ValidationError('calibration numerator must be an integer')
        if type(self.denominator) is not int or self.denominator <= 0:
            raise ValidationError('calibration denominator must be a positive integer')


@dataclass(frozen=True)
class CalibrationTable:
    version: str
    target_unit: str
    rules: tuple[CalibrationRule, ...]
    _by_unit: Mapping[str, CalibrationRule] = field(init=False, repr=False, compare=False)

    def __post_init__(self):
        require_text(self.version, 'calibration version')
        require_text(self.target_unit, 'target unit')
        rules = tuple(self.rules)
        by_unit = {}
        for rule in rules:
            if not isinstance(rule, CalibrationRule):
                raise ValidationError('expected a calibration rule')
            if rule.unit in by_unit:
                raise ValidationError(f'duplicate calibration unit {rule.unit!r}')
            by_unit[rule.unit] = rule
        object.__setattr__(self, 'rules', rules)
        object.__setattr__(self, '_by_unit', MappingProxyType(by_unit))

    def rule_for(self, unit: str) -> CalibrationRule:
        require_text(unit, 'original unit')
        try:
            return self._by_unit[unit]
        except KeyError as exc:
            raise ValidationError(f'unknown original unit {unit!r}') from exc


def calibrate_reading(table: CalibrationTable, unit: str, value: int | None) -> int | None:
    rule = table.rule_for(unit)
    require_reading_value(value)
    if value is None:
        return None
    quotient, remainder = divmod(value * rule.numerator, rule.denominator)
    if remainder:
        raise ValidationError(f'measurement in {unit!r} cannot be converted exactly')
    return quotient

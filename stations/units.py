from dataclasses import dataclass
from fractions import Fraction


@dataclass(frozen=True)
class MeterScale:
    numerator: int
    denominator: int

    def __post_init__(self):
        if any(type(value) is not int or value <= 0
               for value in (self.numerator, self.denominator)):
            raise ValueError('meter scale requires two positive integers')
        ratio = Fraction(self.numerator, self.denominator)
        object.__setattr__(self, 'numerator', ratio.numerator)
        object.__setattr__(self, 'denominator', ratio.denominator)


def to_integer_liters(raw, scale: MeterScale) -> int:
    if type(raw) not in (int, str):
        raise ValueError('reading must be an integer or a numeric string')
    try:
        amount = Fraction(raw)
    except (ValueError, ZeroDivisionError) as error:
        raise ValueError('invalid reading') from error
    liters = amount * Fraction(scale.numerator, scale.denominator)
    if liters.denominator != 1:
        raise ValueError('reading does not convert to integer liters')
    return liters.numerator

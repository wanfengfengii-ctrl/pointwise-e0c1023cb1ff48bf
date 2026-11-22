from dataclasses import dataclass
from datetime import date

from stations.store import station_transaction
from stations.units import MeterScale


STATIONS = ('east', 'west')
MAX_SEQUENCE = (1 << 63) - 1


def validate_day(day):
    if type(day) is not str:
        raise ValueError('day must use YYYY-MM-DD')
    try:
        canonical = date.fromisoformat(day).isoformat()
    except ValueError as error:
        raise ValueError('day must use YYYY-MM-DD') from error
    if canonical != day:
        raise ValueError('day must use YYYY-MM-DD')


def validate_station(station):
    if type(station) is not str or station not in STATIONS:
        raise ValueError('station must be east or west')


def validate_identifier(value, name):
    if type(value) is not str or not value or value.strip() != value or not value.isprintable():
        raise ValueError(f'invalid {name}')


def validate_sequence(sequence, minimum=0):
    if type(sequence) is not int or not minimum <= sequence <= MAX_SEQUENCE:
        raise ValueError('invalid station sequence')


def validate_window(first, last):
    validate_sequence(first, minimum=1)
    validate_sequence(last, minimum=first)


@dataclass(frozen=True)
class StationDay:
    day: str
    station: str
    meter_id: str
    scale: MeterScale


def load_station_day(connection, day, station) -> StationDay:
    validate_day(day)
    validate_station(station)
    row = connection.execute(
        '''SELECT meter_id, scale_numerator, scale_denominator
           FROM station_days WHERE day = ? AND station = ?''',
        (day, station),
    ).fetchone()
    if row is None:
        raise KeyError((day, station))
    meter_id, numerator, denominator = row
    return StationDay(day, station, meter_id, MeterScale(int(numerator), int(denominator)))


def register_station_day(connection, day, station, meter_id, scale) -> StationDay:
    validate_day(day)
    validate_station(station)
    validate_identifier(meter_id, 'meter identifier')
    if not isinstance(scale, MeterScale):
        raise ValueError('invalid meter scale')
    requested = StationDay(day, station, meter_id, scale)
    with station_transaction(connection):
        connection.execute(
            '''INSERT INTO station_days
               (day, station, meter_id, scale_numerator, scale_denominator)
               VALUES (?, ?, ?, ?, ?) ON CONFLICT (day, station) DO NOTHING''',
            (day, station, meter_id, str(scale.numerator), str(scale.denominator)),
        )
        registered = load_station_day(connection, day, station)
        if registered != requested:
            raise ValueError('station day already has a different configuration')
    return registered

from dataclasses import dataclass

from stations.journal import append_events
from stations.register import load_station_day, validate_identifier
from stations.store import station_transaction
from stations.units import to_integer_liters


@dataclass(frozen=True)
class Reading:
    meter_id: str
    source_id: str
    raw: int | str


def import_rows(connection, day, station, rows):
    with station_transaction(connection):
        config = load_station_day(connection, day, station)
        try:
            observations = iter(rows)
        except TypeError as error:
            raise ValueError('observations must be an iterable of readings') from error
        converted = []
        for reading in observations:
            if not isinstance(reading, Reading) or reading.meter_id != config.meter_id:
                raise ValueError('reading meter does not match station day')
            validate_identifier(reading.source_id, 'source identifier')
            liters = to_integer_liters(reading.raw, config.scale)
            converted.append((reading.source_id, str(reading.raw), liters))
        return append_events(connection, config, converted)

from dataclasses import dataclass

from stations.journal import latest_sequence
from stations.register import validate_day, validate_identifier, validate_station
from stations.store import station_transaction


@dataclass(frozen=True)
class StationCut:
    cut_id: str
    day: str
    station: str
    sequence: int


def load_station_cut(connection, cut_id) -> StationCut | None:
    validate_identifier(cut_id, 'cut identifier')
    row = connection.execute(
        'SELECT cut_id, day, station, seq FROM station_cuts WHERE cut_id = ?',
        (cut_id,),
    ).fetchone()
    return None if row is None else StationCut(*row)


def freeze_station_cut(connection, day, station, cut_id) -> StationCut:
    validate_day(day)
    validate_station(station)
    validate_identifier(cut_id, 'cut identifier')
    with station_transaction(connection):
        previous = load_station_cut(connection, cut_id)
        if previous is not None:
            if (previous.day, previous.station) != (day, station):
                raise ValueError('cut identifier already belongs to a different station day')
            return previous
        try:
            sequence = latest_sequence(connection, day, station)
        except KeyError as error:
            raise ValueError('cannot freeze an unregistered station day') from error
        connection.execute(
            'INSERT INTO station_cuts (cut_id, day, station, seq) VALUES (?, ?, ?, ?)',
            (cut_id, day, station, sequence),
        )
        return StationCut(cut_id, day, station, sequence)

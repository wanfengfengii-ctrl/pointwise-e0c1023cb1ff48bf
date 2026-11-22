from dataclasses import dataclass

from stations.register import (
    StationDay, validate_day, validate_sequence, validate_station, validate_window,
)
from stations.store import require_station_transaction


@dataclass(frozen=True)
class StationEvent:
    sequence: int
    meter_id: str
    source_id: str
    raw: str | None
    liters: int


def latest_sequence(connection, day, station) -> int:
    validate_day(day)
    validate_station(station)
    row = connection.execute(
        'SELECT last_sequence FROM station_days WHERE day = ? AND station = ?',
        (day, station),
    ).fetchone()
    if row is None:
        raise KeyError((day, station))
    return row['last_sequence']


def append_events(connection, config: StationDay, converted):
    """Stage an already validated batch of (source_id, raw, liters) rows."""
    require_station_transaction(connection)
    start = latest_sequence(connection, config.day, config.station)
    if not converted:
        raise ValueError('empty observation batch')
    end = start + len(converted)
    validate_sequence(end, minimum=1)
    events = tuple(
        StationEvent(sequence, config.meter_id, source, raw, liters)
        for sequence, (source, raw, liters) in enumerate(converted, start=start + 1)
    )
    connection.executemany(
        '''INSERT INTO station_events (day, station, seq, source_id, raw, liters)
           VALUES (?, ?, ?, ?, ?, ?)''',
        ((config.day, config.station, row.sequence, row.source_id, row.raw, str(row.liters))
         for row in events),
    )
    connection.execute(
        'UPDATE station_days SET last_sequence = ? WHERE day = ? AND station = ?',
        (end, config.day, config.station),
    )
    return events


def read_event_window(connection, day, station, first, last):
    validate_day(day)
    validate_station(station)
    validate_window(first, last)
    rows = connection.execute(
        '''SELECT e.seq, d.meter_id, e.source_id, e.raw, e.liters
           FROM station_events AS e
           JOIN station_days AS d ON d.day = e.day AND d.station = e.station
           WHERE e.day = ? AND e.station = ? AND e.seq BETWEEN ? AND ?
           ORDER BY e.seq''',
        (day, station, first, last),
    ).fetchall()
    if len(rows) != last - first + 1:
        raise ValueError('station window is not complete')
    return tuple(StationEvent(seq, meter, source, raw, int(liters))
                 for seq, meter, source, raw, liters in rows)


def clear_raw_readings(connection, day, station, first, last) -> int:
    require_station_transaction(connection)
    validate_day(day)
    validate_station(station)
    validate_window(first, last)
    result = connection.execute(
        '''UPDATE station_events SET raw = NULL
           WHERE day = ? AND station = ? AND seq BETWEEN ? AND ? AND raw IS NOT NULL''',
        (day, station, first, last),
    )
    return result.rowcount

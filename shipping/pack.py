from shipping.evidence import IncrementBlock, build_block_evidence
from shipping.releases import load_release, save_release
from stations.journal import read_event_window
from stations.register import validate_day, validate_identifier, validate_station, validate_window
from stations.store import station_transaction


def issue_block(connection, day, station, block_id, first, last) -> IncrementBlock:
    validate_day(day)
    validate_station(station)
    validate_identifier(block_id, 'block identifier')
    validate_window(first, last)
    with station_transaction(connection):
        previous = load_release(connection, block_id)
        if previous is not None:
            if (previous.day, previous.station, previous.first, previous.last) != (day, station, first, last):
                raise ValueError('block identifier already belongs to a different window')
            return previous
        events = read_event_window(connection, day, station, first, last)
        block = build_block_evidence(block_id, day, station, first, last, events)
        save_release(connection, block)
    return block

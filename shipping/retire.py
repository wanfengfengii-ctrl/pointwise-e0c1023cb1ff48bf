from dataclasses import dataclass

from shipping.acknowledge import load_acknowledgement, stage_acknowledgement
from shipping.releases import load_release
from stations.journal import clear_raw_readings
from stations.register import validate_identifier
from stations.store import station_transaction


@dataclass(frozen=True)
class Retirement:
    block_id: str
    day: str
    station: str
    first: int
    last: int
    receipt_id: int
    cleared_count: int


def _retirement(block, receipt_id, cleared_count):
    return Retirement(block.block_id, block.day, block.station, block.first, block.last,
                      receipt_id, cleared_count)


def load_retirement(connection, block_id) -> Retirement | None:
    validate_identifier(block_id, 'block identifier')
    row = connection.execute(
        '''SELECT r.cleared_count, a.receipt_id FROM retirements AS r
           JOIN acknowledgements AS a ON a.block_id = r.block_id WHERE r.block_id = ?''',
        (block_id,),
    ).fetchone()
    if row is None:
        return None
    return _retirement(load_release(connection, block_id), row['receipt_id'], row['cleared_count'])


def retire_release(connection, block_id, receipt=None) -> Retirement:
    with station_transaction(connection):
        block = load_release(connection, block_id)
        if block is None:
            raise ValueError('no such issued block')
        if receipt is not None:
            acknowledgement = stage_acknowledgement(connection, block, receipt)
        else:
            acknowledgement = load_acknowledgement(connection, block_id)
        if acknowledgement is None:
            raise ValueError('retirement requires a matching acceptance receipt')
        previous = load_retirement(connection, block_id)
        if previous is not None:
            return previous
        count = clear_raw_readings(connection, block.day, block.station, block.first, block.last)
        connection.execute(
            'INSERT INTO retirements (block_id, cleared_count) VALUES (?, ?)', (block_id, count),
        )
        return _retirement(block, acknowledgement.receipt_id, count)

from dataclasses import dataclass

from receiving.store import require_center_transaction
from shipping.evidence import IncrementBlock
from stations.register import validate_day


@dataclass(frozen=True)
class CoverageFact:
    station: str
    sequence: int
    liters: int
    meter_id: str
    source_id: str
    block_id: str


def check_overlap(connection, block: IncrementBlock):
    rows = connection.execute(
        '''SELECT seq, liters, meter_id, source_id FROM day_coverage
           WHERE day = ? AND station = ? AND seq BETWEEN ? AND ?''',
        (block.day, block.station, block.first, block.last),
    )
    covered = {seq: (int(liters), meter, source) for seq, liters, meter, source in rows}
    additions = []
    for item in block.evidence:
        previous = covered.get(item.sequence)
        if previous is None:
            additions.append(item)
        elif previous != (item.liters, item.meter_id, item.source_id):
            raise ValueError(f'conflicting evidence at {block.station} sequence {item.sequence}')
    return tuple(additions)


def store_new_coverage(connection, block: IncrementBlock, additions):
    require_center_transaction(connection)
    connection.executemany(
        '''INSERT INTO day_coverage (day, station, seq, liters, meter_id, source_id, block_id)
           VALUES (?, ?, ?, ?, ?, ?, ?)''',
        ((block.day, block.station, item.sequence, str(item.liters), item.meter_id,
          item.source_id, block.block_id) for item in additions),
    )


def read_day_coverage(connection, day):
    validate_day(day)
    rows = connection.execute(
        '''SELECT station, seq, liters, meter_id, source_id, block_id FROM day_coverage
           WHERE day = ? ORDER BY station, seq''',
        (day,),
    )
    return tuple(CoverageFact(station, seq, int(liters), meter, source, block_id)
                 for station, seq, liters, meter, source, block_id in rows)

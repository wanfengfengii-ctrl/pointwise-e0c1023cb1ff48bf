from shipping.evidence import IncrementBlock, block_from_body, canonical_block
from stations.register import validate_identifier
from stations.store import require_station_transaction


def load_release(connection, block_id) -> IncrementBlock | None:
    validate_identifier(block_id, 'block identifier')
    row = connection.execute(
        'SELECT body FROM releases WHERE block_id = ?', (block_id,),
    ).fetchone()
    return None if row is None else block_from_body(row['body'])


def save_release(connection, block: IncrementBlock):
    require_station_transaction(connection)
    body = canonical_block(block)
    connection.execute(
        'INSERT INTO releases (block_id, day, station, body) VALUES (?, ?, ?, ?)',
        (block.block_id, block.day, block.station, body),
    )

from receiving.receipts import AcceptanceReceipt, body_digest
from shipping.evidence import canonical_block
from shipping.releases import load_release
from stations.register import MAX_SEQUENCE, validate_identifier
from stations.store import require_station_transaction, station_transaction


def load_acknowledgement(connection, block_id) -> AcceptanceReceipt | None:
    validate_identifier(block_id, 'block identifier')
    row = connection.execute(
        'SELECT receipt_id, day, digest FROM acknowledgements WHERE block_id = ?', (block_id,),
    ).fetchone()
    if row is None:
        return None
    return AcceptanceReceipt(row['receipt_id'], 'block', block_id, row['day'], row['digest'])


def stage_acknowledgement(connection, block, receipt) -> AcceptanceReceipt:
    require_station_transaction(connection)
    if not isinstance(receipt, AcceptanceReceipt):
        raise ValueError('missing or invalid acceptance receipt')
    if type(receipt.receipt_id) is not int or not 1 <= receipt.receipt_id <= MAX_SEQUENCE:
        raise ValueError('invalid acceptance receipt identifier')
    expected = ('block', block.block_id, block.day, body_digest(canonical_block(block)), None)
    if (receipt.kind, receipt.material_id, receipt.day, receipt.digest, receipt.account_id) != expected:
        raise ValueError('acceptance receipt does not match the issued block')
    previous = load_acknowledgement(connection, block.block_id)
    if previous is not None:
        if previous != receipt:
            raise ValueError('release already has a different acceptance receipt')
        return previous
    connection.execute(
        'INSERT INTO acknowledgements (block_id, receipt_id, day, digest) VALUES (?, ?, ?, ?)',
        (block.block_id, receipt.receipt_id, receipt.day, receipt.digest),
    )
    return receipt


def record_acknowledgement(connection, block_id, receipt) -> AcceptanceReceipt:
    with station_transaction(connection):
        block = load_release(connection, block_id)
        if block is None:
            raise ValueError('no such issued block')
        return stage_acknowledgement(connection, block, receipt)

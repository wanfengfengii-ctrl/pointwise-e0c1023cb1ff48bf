from receiving.coverage import check_overlap, store_new_coverage
from receiving.receipts import AcceptanceReceipt, retry_receipt, stage_receipt
from receiving.store import center_transaction
from shipping.evidence import IncrementBlock, canonical_block


def accept_block(connection, block: IncrementBlock) -> AcceptanceReceipt:
    body = canonical_block(block)
    with center_transaction(connection):
        previous = connection.execute(
            'SELECT body FROM accepted_blocks WHERE block_id = ?', (block.block_id,),
        ).fetchone()
        if previous is not None:
            return retry_receipt(connection, 'block', block.block_id, previous['body'], body)
        additions = check_overlap(connection, block)
        connection.execute(
            'INSERT INTO accepted_blocks (block_id, day, body) VALUES (?, ?, ?)',
            (block.block_id, block.day, body),
        )
        store_new_coverage(connection, block, additions)
        return stage_receipt(connection, 'block', block.block_id, block.day, body)

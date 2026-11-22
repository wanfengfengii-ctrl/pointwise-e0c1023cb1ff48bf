from accounts.cutover import stage_replacement
from audit.revisions import CorrectionNotice, canonical_notice, notice_from_body
from receiving.receipts import AcceptanceReceipt, retry_receipt, stage_receipt
from receiving.store import center_transaction
from stations.register import validate_identifier


def load_accepted_correction(connection, notice_id) -> CorrectionNotice | None:
    validate_identifier(notice_id, 'notice identifier')
    row = connection.execute(
        'SELECT body FROM accepted_corrections WHERE notice_id = ?', (notice_id,),
    ).fetchone()
    return None if row is None else notice_from_body(row['body'])


def accept_correction(connection, notice: CorrectionNotice) -> AcceptanceReceipt:
    body = canonical_notice(notice)
    with center_transaction(connection):
        previous = connection.execute(
            'SELECT body FROM accepted_corrections WHERE notice_id = ?', (notice.notice_id,),
        ).fetchone()
        if previous is not None:
            return retry_receipt(connection, 'correction', notice.notice_id, previous['body'], body)
        connection.execute(
            'INSERT INTO accepted_corrections (notice_id, day, body) VALUES (?, ?, ?)',
            (notice.notice_id, notice.day, body),
        )
        account_id = stage_replacement(connection, notice)
        return stage_receipt(connection, 'correction', notice.notice_id, notice.day, body, account_id)

from accounts.publish import stage_publication
from audit.revisions import CorrectionNotice
from receiving.store import require_center_transaction


def stage_replacement(connection, notice: CorrectionNotice) -> int:
    require_center_transaction(connection)
    account_id = stage_publication(connection, notice.day, notice)
    connection.execute(
        '''INSERT INTO current_basis (day, notice_id) VALUES (?, ?)
           ON CONFLICT (day) DO UPDATE SET notice_id = excluded.notice_id''',
        (notice.day, notice.notice_id),
    )
    return account_id

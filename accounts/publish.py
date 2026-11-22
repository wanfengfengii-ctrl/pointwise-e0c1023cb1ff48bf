from accounts.materialize import calculate_day
from accounts.provenance import save_account_provenance
from audit.revisions import CorrectionNotice, notice_from_body
from receiving.store import center_transaction, require_center_transaction
from stations.register import validate_day


def load_current_basis(connection, day) -> CorrectionNotice | None:
    validate_day(day)
    row = connection.execute(
        '''SELECT c.body FROM current_basis AS b
           JOIN accepted_corrections AS c ON c.notice_id = b.notice_id WHERE b.day = ?''',
        (day,),
    ).fetchone()
    return None if row is None else notice_from_body(row['body'])


def stage_publication(connection, day, basis: CorrectionNotice | None) -> int:
    require_center_transaction(connection)
    totals = calculate_day(connection, day, basis)
    notice_id = None if basis is None else basis.notice_id
    result = connection.execute(
        '''INSERT INTO accounts (day, notice_id, base_liters, east_liters, west_liters, total_liters)
           VALUES (?, ?, ?, ?, ?, ?)''',
        (day, notice_id, str(totals.base_liters), str(totals.east_liters),
         str(totals.west_liters), str(totals.total_liters)),
    )
    account_id = result.lastrowid
    save_account_provenance(connection, account_id, totals.contributions)
    return account_id


def publish_day(connection, day) -> int:
    with center_transaction(connection):
        basis = load_current_basis(connection, day)
        return stage_publication(connection, day, basis)

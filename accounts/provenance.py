from dataclasses import dataclass

from audit.revisions import notice_from_body
from receiving.store import require_center_transaction


@dataclass(frozen=True)
class ContributionSource:
    station: str
    sequence: int
    meter_id: str
    source_id: str
    block_id: str


def save_account_provenance(connection, account_id, contributions):
    require_center_transaction(connection)
    connection.executemany(
        '''INSERT INTO account_sources (account_id, station, seq, meter_id, source_id, block_id)
           VALUES (?, ?, ?, ?, ?, ?)''',
        ((account_id, row.station, row.sequence, row.meter_id, row.source_id, row.block_id)
         for row in contributions),
    )


def load_account_provenance(connection, account_id):
    row = connection.execute(
        '''SELECT c.body FROM accounts AS a
           LEFT JOIN accepted_corrections AS c ON c.notice_id = a.notice_id
           WHERE a.account_id = ?''',
        (account_id,),
    ).fetchone()
    if row is None:
        raise KeyError(account_id)
    basis = None if row['body'] is None else notice_from_body(row['body'])
    sources = connection.execute(
        '''SELECT station, seq, meter_id, source_id, block_id FROM account_sources
           WHERE account_id = ? ORDER BY station, seq''',
        (account_id,),
    )
    return basis, tuple(ContributionSource(*source) for source in sources)

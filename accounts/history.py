from dataclasses import dataclass

from accounts.provenance import ContributionSource, load_account_provenance
from audit.revisions import CorrectionNotice
from stations.register import MAX_SEQUENCE


@dataclass(frozen=True)
class AccountSnapshot:
    account_id: int
    day: str
    base_liters: int
    east_liters: int
    west_liters: int
    total_liters: int
    basis: CorrectionNotice | None
    sources: tuple[ContributionSource, ...]


def get_account(connection, account_id) -> AccountSnapshot:
    if type(account_id) is not int or not 1 <= account_id <= MAX_SEQUENCE:
        raise KeyError(account_id)
    row = connection.execute(
        '''SELECT day, base_liters, east_liters, west_liters, total_liters
           FROM accounts WHERE account_id = ?''',
        (account_id,),
    ).fetchone()
    if row is None:
        raise KeyError(account_id)
    basis, sources = load_account_provenance(connection, account_id)
    day, base, east, west, total = row
    return AccountSnapshot(account_id, day, int(base), int(east), int(west), int(total), basis, sources)

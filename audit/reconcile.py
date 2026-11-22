from dataclasses import dataclass

from audit.cuts import load_station_cut
from stations.register import validate_day, validate_identifier
from stations.store import station_transaction


@dataclass(frozen=True)
class Reconciliation:
    reconciliation_id: str
    day: str
    east_cut_id: str
    west_cut_id: str
    base_liters: int
    review_ref: str


def reconciliation_cuts(connection, day, east_cut_id, west_cut_id):
    east = load_station_cut(connection, east_cut_id)
    west = load_station_cut(connection, west_cut_id)
    if east is None or west is None:
        raise ValueError('reconciliation requires two known station cuts')
    if (east.day, east.station, west.day, west.station) != (day, 'east', day, 'west'):
        raise ValueError('cuts must identify east and west on the reconciliation day')
    return east, west


def load_reconciliation(connection, reconciliation_id) -> Reconciliation:
    validate_identifier(reconciliation_id, 'reconciliation identifier')
    row = connection.execute(
        '''SELECT day, east_cut_id, west_cut_id, base_liters, review_ref
           FROM reconciliations WHERE reconciliation_id = ?''',
        (reconciliation_id,),
    ).fetchone()
    if row is None:
        raise KeyError(reconciliation_id)
    day, east, west, liters, reference = row
    return Reconciliation(reconciliation_id, day, east, west, int(liters), reference)


def record_reconciliation(connection, reconciliation_id, day, east_cut_id,
                          west_cut_id, base_liters, review_ref) -> Reconciliation:
    validate_identifier(reconciliation_id, 'reconciliation identifier')
    validate_day(day)
    validate_identifier(review_ref, 'review reference')
    if type(base_liters) is not int:
        raise ValueError('reconciliation bottom ledger must be integer liters')
    requested = Reconciliation(reconciliation_id, day, east_cut_id, west_cut_id,
                               base_liters, review_ref)
    with station_transaction(connection):
        reconciliation_cuts(connection, day, east_cut_id, west_cut_id)
        connection.execute(
            '''INSERT INTO reconciliations
               (reconciliation_id, day, east_cut_id, west_cut_id, base_liters, review_ref)
               VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT (reconciliation_id) DO NOTHING''',
            (reconciliation_id, day, east_cut_id, west_cut_id, str(base_liters), review_ref),
        )
        stored = load_reconciliation(connection, reconciliation_id)
        if stored != requested:
            raise ValueError('reconciliation identifier already has different content')
    return stored

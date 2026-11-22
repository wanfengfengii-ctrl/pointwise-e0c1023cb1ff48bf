from dataclasses import dataclass

from audit.revisions import CorrectionNotice
from receiving.coverage import CoverageFact, read_day_coverage


@dataclass(frozen=True)
class DayTotals:
    base_liters: int
    east_liters: int
    west_liters: int
    total_liters: int
    contributions: tuple[CoverageFact, ...]


def calculate_day(connection, day, basis: CorrectionNotice | None = None) -> DayTotals:
    if basis is not None and basis.day != day:
        raise ValueError('bottom ledger belongs to a different day')
    if basis is None:
        base = 0
        cuts = {'east': 0, 'west': 0}
    else:
        base = basis.base_liters
        cuts = {'east': basis.east_cut, 'west': basis.west_cut}
    totals = {'east': 0, 'west': 0}
    contributions = []
    for fact in read_day_coverage(connection, day):
        if fact.sequence > cuts[fact.station]:
            totals[fact.station] += fact.liters
            contributions.append(fact)
    east, west = totals['east'], totals['west']
    return DayTotals(base, east, west, base + east + west, tuple(contributions))

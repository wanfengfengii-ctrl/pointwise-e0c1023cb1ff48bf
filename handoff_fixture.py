import json
import sqlite3
from contextlib import closing, contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory

from accounts.history import get_account
from audit.reconcile import record_reconciliation
from audit.release import issue_correction
from demo import DAY, deliver_and_retire, prepare_day, source_rows
from receiving.audit_intake import accept_correction
from receiving.store import open_center_store
from stations.journal import read_event_window
from stations.store import open_station_store


def build_day(source, center, review_base=100):
    notice, blocks, _ = prepare_day(source)
    if review_base != notice.base_liters:
        review = record_reconciliation(source, "alternate-review", DAY,
                                       notice.east_cut_id, notice.west_cut_id,
                                       review_base, "alternate-worksheet")
        notice = issue_correction(source, "alternate-correction", review.reconciliation_id)
    original = deliver_and_retire(source, center, blocks)
    for station in ("east", "west"):
        if any(row.raw is not None for row in read_event_window(source, DAY, station, 1, 2)):
            raise AssertionError("sample source rows still retain raw readings")
    return original, notice


def prepare_case(directory, review_base=100):
    directory = Path(directory)
    center_path = directory / "center.sqlite"
    with closing(open_center_store(center_path)) as center:
        with closing(open_station_store(directory / "source.sqlite")) as source:
            original, notice = build_day(source, center, review_base)
        revised_id = accept_correction(center, notice).account_id
    with closing(open_center_store(center_path)) as center:
        restored = get_account(center, original.account_id)
        if original != restored:
            raise AssertionError("original account changed during rebuild")
        return restored, get_account(center, revised_id)


def wire(message):
    return json.loads(json.dumps(message, allow_nan=False))


@contextmanager
def reject_commit(connection):
    def authorize(action, operation, _arg, _database, _trigger):
        if action == sqlite3.SQLITE_TRANSACTION and operation == "COMMIT":
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    connection.set_authorizer(authorize)
    try:
        yield
    finally:
        connection.set_authorizer(None)


def main():
    for review_base, expected in ((100, 105), (5, 10)):
        with TemporaryDirectory() as directory:
            original, corrected = prepare_case(directory, review_base)
        totals = [original.total_liters, corrected.total_liters]
        if totals != [14, expected]:
            raise AssertionError(f"unexpected frozen account totals {totals!r}")
        print(json.dumps({"day": original.day, "account_ids": [original.account_id, corrected.account_id],
                          "totals": totals, "sources": [source_rows(original), source_rows(corrected)]}))


if __name__ == "__main__":
    main()

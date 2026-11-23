import json
import sqlite3
from contextlib import closing, contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory

from accounts.history import get_account
from accounts.publish import (acknowledge_handoff, amend_handoff, get_handoff,
                              load_current_basis, publish_day, resume_handoff, start_handoff)
from audit.cuts import freeze_station_cut
from audit.reconcile import record_reconciliation
from audit.release import issue_correction
from receiving.audit_intake import accept_correction, load_accepted_correction
from receiving.intake import accept_block
from receiving.monthly import accept_month_message, open_month_store, read_month_entries
from receiving.receipts import load_receipt
from receiving.store import CenterCommitError, open_center_store
from shipping.pack import issue_block
from shipping.retire import retire_release
from stations.import_rows import Reading, import_rows
from stations.journal import read_event_window
from stations.register import register_station_day
from stations.store import open_station_store
from stations.units import MeterScale


DAY = '2026-05-14'
METERS = {'east': 'E-17', 'west': 'W-29'}


def register_example(source, day=DAY):
    register_station_day(source, day, 'east', METERS['east'], MeterScale(1, 2))
    register_station_day(source, day, 'west', METERS['west'], MeterScale(2, 5))


def observations(station, *values):
    return tuple(Reading(METERS[station], reference, raw) for reference, raw in values)


def add_observation(source, station, reference, raw):
    return import_rows(source, DAY, station, observations(station, (reference, raw)))[0]


def prepare_day(source, east_raw=('8.0', '6/1'), west_cut=1, include_later=False):
    register_example(source)
    add_observation(source, 'east', 'east-1', east_raw[0])
    freeze_station_cut(source, DAY, 'east', 'east-cut')
    add_observation(source, 'west', 'west-1', '12.5')
    if west_cut == 2:
        add_observation(source, 'west', 'west-2', '5')
    freeze_station_cut(source, DAY, 'west', 'west-cut')
    record_reconciliation(source, 'review-1', DAY, 'east-cut', 'west-cut', 100, 'worksheet-1')
    add_observation(source, 'east', 'east-2', east_raw[1])
    if west_cut == 1:
        add_observation(source, 'west', 'west-2', '5')
    notice = issue_correction(source, 'correction-1', 'review-1')
    east = issue_block(source, DAY, 'east', 'east-block', 1, 2)
    west = issue_block(source, DAY, 'west', 'west-block', 1, 2)
    later = None
    if include_later:
        add_observation(source, 'west', 'west-3', '15')
        later = issue_block(source, DAY, 'west', 'west-later', 3, 3)
    return notice, (east, west), later


def deliver_and_retire(source, center, blocks):
    receipts = [accept_block(center, block) for block in blocks]
    old = get_account(center, publish_day(center, DAY))
    for block, receipt in zip(blocks, receipts, strict=True):
        retire_release(source, block.block_id, receipt)
    return old


@contextmanager
def fail_commit_on_insert(connection, table):
    """Use a deferred SQLite constraint to fail the real committing transaction."""
    if table not in ('accounts', 'accepted_blocks'):
        raise ValueError('unsupported demonstration table')
    connection.executescript(f'''
        CREATE TEMP TABLE fault_parent (id INTEGER PRIMARY KEY);
        CREATE TEMP TABLE fault_child (
            id INTEGER REFERENCES fault_parent (id) DEFERRABLE INITIALLY DEFERRED
        );
        CREATE TEMP TRIGGER fault_at_commit AFTER INSERT ON main.{table}
        BEGIN
            INSERT INTO fault_child (id) VALUES (1);
        END;
    ''')
    try:
        yield
    finally:
        connection.executescript('''
            DROP TRIGGER temp.fault_at_commit;
            DROP TABLE temp.fault_child;
            DROP TABLE temp.fault_parent;
        ''')


def amounts(account):
    return [account.base_liters, account.east_liters, account.west_liters, account.total_liters]


def source_rows(account):
    return [[row.station, row.sequence, row.meter_id, row.source_id, row.block_id]
            for row in account.sources]


def show_scenario(case_id, inputs, expected, actual, downstream_expected, downstream_actual,
                  paths, oracle):
    if actual != expected or downstream_actual != downstream_expected:
        raise AssertionError(f'{case_id}: observed {actual!r} and {downstream_actual!r}; '
                             f'expected {expected!r} and {downstream_expected!r}')
    print('POINTWISE_SCENARIO ' + json.dumps({
        'case_id': case_id, 'input': inputs, 'expected': expected, 'actual': actual,
        'downstream_expected': downstream_expected, 'downstream_actual': downstream_actual,
        'code_paths': paths, 'oracle': oracle,
    }, sort_keys=True))


def run_demo(directory):
    source_path = Path(directory) / 'stations.sqlite'
    center_path = Path(directory) / 'center.sqlite'
    first_sources = [
        ['east', 1, 'E-17', 'east-1', 'east-block'],
        ['east', 2, 'E-17', 'east-2', 'east-block'],
        ['west', 1, 'W-29', 'west-1', 'west-block'],
        ['west', 2, 'W-29', 'west-2', 'west-block'],
    ]
    with closing(open_center_store(center_path)) as center:
        with closing(open_station_store(source_path)) as source:
            notice, blocks, later = prepare_day(source, include_later=True)
            old = deliver_and_retire(source, center, blocks)
            retired = [row.raw for station in ('east', 'west')
                       for row in read_event_window(source, DAY, station, 1, 2)]
            if retired != [None, None, None, None]:
                raise AssertionError(f'expected four retired raw readings, observed {retired!r}')
        show_scenario('T1', {'east_raw': ['8.0', '6/1'], 'west_raw': ['12.5', '5'],
                             'east_scale': '1/2', 'west_scale': '2/5'},
                      [0, 7, 7, 14], amounts(old), first_sources, source_rows(old),
                      ['stations/import_rows.py', 'stations/units.py', 'receiving/intake.py',
                       'accounts/publish.py', 'accounts/history.py'],
                      '8/2 + 6/2 = 7 and 12.5*2/5 + 5*2/5 = 7; enumerate four report references')
        with fail_commit_on_insert(center, 'accounts'):
            try:
                accept_correction(center, notice)
            except CenterCommitError as error:
                if not isinstance(error.__cause__, sqlite3.IntegrityError):
                    raise
            else:
                raise AssertionError('expected deferred foreign key to reject account commit')
    with closing(open_center_store(center_path)) as center:
        failed_state = [load_current_basis(center, DAY),
                        load_accepted_correction(center, notice.notice_id),
                        load_receipt(center, 'correction', notice.notice_id)]
        show_scenario('T8', {'notice_id': notice.notice_id, 'failure': 'deferred foreign key'},
                      [None, None, None], failed_state,
                      [[0, 7, 7, 14], first_sources],
                      [amounts(get_account(center, old.account_id)),
                       source_rows(get_account(center, old.account_id))],
                      ['receiving/store.py', 'receiving/audit_intake.py', 'accounts/cutover.py',
                       'accounts/history.py'],
                      'one failed commit has no current basis, accepted notice or receipt; 7 + 7 remains 14')
        replacement = accept_correction(center, notice)
        revised = get_account(center, replacement.account_id)
        suffix_sources = [first_sources[1], first_sources[3]]
        show_scenario('T6', {'base_liters': notice.base_liters,
                             'cuts': [notice.east_cut, notice.west_cut], 'retired_raw': retired},
                      [100, 3, 2, 105], amounts(revised), suffix_sources, source_rows(revised),
                      ['shipping/pack.py', 'receiving/coverage.py', 'receiving/audit_intake.py',
                       'accounts/materialize.py', 'accounts/history.py'],
                      'each cut covers sequence 1; add east 3 and west 2 to the audit 100')
        accept_block(center, later)
        latest = publish_day(center, DAY)
    with closing(open_center_store(center_path)) as center:
        history = [get_account(center, number) for number in (old.account_id, replacement.account_id, latest)]
        show_scenario('T9.next_account', {'late_sequence': later.first, 'late_liters': later.net_liters},
                      [[0, 7, 7, 14], [100, 3, 2, 105], [100, 3, 8, 111]],
                      [amounts(account) for account in history],
                      [first_sources, suffix_sources,
                       suffix_sources + [['west', 3, 'W-29', 'west-3', 'west-later']]],
                      [source_rows(account) for account in history],
                      ['receiving/coverage.py', 'accounts/publish.py', 'accounts/history.py',
                       'accounts/provenance.py'],
                      'west increases from 2 to 8; earlier accounts retain their own source lists')
        print('Fixed daily accounts:', ', '.join(f'{a.account_id} = {a.total_liters} L' for a in history))


def handoff_wire(message):
    return json.loads(json.dumps(message, allow_nan=False))


def prepare_handoff_case(directory):
    directory = Path(directory)
    directory.mkdir(parents=True)
    center_path = directory / 'center.sqlite'
    with closing(open_center_store(center_path)) as center:
        with closing(open_station_store(directory / 'source.sqlite')) as source:
            notice, blocks, _ = prepare_day(source)
            original = deliver_and_retire(source, center, blocks)
        corrected = get_account(center, accept_correction(center, notice).account_id)
    return center_path, original, corrected


def entry_rows(entries):
    return [[entry['kind'], entry['liters'], entry['original_posted']] for entry in entries]


def describe_entries(entries):
    return ', '.join(f"{entry['liters']} L {entry['kind']}" for entry in entries)


def handoff_state(handoff):
    receipts = []
    for key in ('original_receipt', 'correction_receipt'):
        receipt = handoff[key]
        receipts.append(None if receipt is None else receipt['liters'])
    return [handoff['status'], *receipts]


def run_handoff_demo(directory):
    directory = Path(directory)
    center_path, original, corrected = prepare_handoff_case(directory / 'original-first')
    with closing(open_center_store(center_path)) as center, \
            closing(open_month_store(center_path.parent / 'month.sqlite')) as month:
        original_message = start_handoff(center, 'handoff-1', original.account_id)
        declare = amend_handoff(center, 'handoff-1', corrected.account_id)
        lost = accept_month_message(month, handoff_wire(original_message))
        correction_reply = accept_month_message(month, handoff_wire(declare))
        acknowledge_handoff(center, 'handoff-1', handoff_wire(correction_reply))
        entries = read_month_entries(month, DAY)
        resumed = resume_handoff(center, 'handoff-1', 'original')
        replayed = accept_month_message(month, handoff_wire(resumed))
        if replayed != lost:
            raise AssertionError('redelivered original message returned a different reply')
        acknowledge_handoff(center, 'handoff-1', handoff_wire(replayed))
        state = get_handoff(center, 'handoff-1')
        show_scenario('H1.original_first',
                      {'order': ['original', 'correction'], 'lost_reply': 'original',
                       'accounts': [original.total_liters, corrected.total_liters]},
                      [['original', 14, True], ['correction', 91, True]], entry_rows(entries),
                      ['complete', 14, 91], handoff_state(state),
                      ['accounts/publish.py', 'receiving/monthly.py', 'receiving/handoff_wire.py'],
                      'original posts 14 first; correction appends 105 - 14 = 91; '
                      'the lost original reply is acknowledged afterwards')
        print('Month entries (original first):', describe_entries(entries))
    center_path, original, corrected = prepare_handoff_case(directory / 'correction-first')
    with closing(open_center_store(center_path)) as center, \
            closing(open_month_store(center_path.parent / 'month.sqlite')) as month:
        original_message = start_handoff(center, 'handoff-1', original.account_id)
        declare = amend_handoff(center, 'handoff-1', corrected.account_id)
        pending = accept_month_message(month, handoff_wire(declare))
        blocked = accept_month_message(month, handoff_wire(original_message))
        if pending['status'] != 'pending' or blocked['status'] != 'blocked':
            raise AssertionError('expected a pending declaration and a blocked original')
        acknowledge_handoff(center, 'handoff-1', handoff_wire(blocked))
        replayed = accept_month_message(month, handoff_wire(declare))
        if replayed != pending:
            raise AssertionError('redelivered declaration returned a different reply')
        acknowledge_handoff(center, 'handoff-1', handoff_wire(replayed))
        settle = resume_handoff(center, 'handoff-1', 'correction')
        posted = accept_month_message(month, handoff_wire(settle))
        acknowledge_handoff(center, 'handoff-1', handoff_wire(posted))
        entries = read_month_entries(month, DAY)
        state = get_handoff(center, 'handoff-1')
        show_scenario('H2.correction_first',
                      {'order': ['correction', 'original'], 'lost_reply': 'pending',
                       'accounts': [original.total_liters, corrected.total_liters]},
                      [['correction', 105, False]], entry_rows(entries),
                      ['complete', None, 105], handoff_state(state),
                      ['accounts/publish.py', 'receiving/monthly.py', 'receiving/handoff_wire.py'],
                      'the ruling seals the original first; the settlement posts the full 105 '
                      'and the late original stays blocked')
        print('Month entries (correction first):', describe_entries(entries))


if __name__ == '__main__':
    with TemporaryDirectory() as directory:
        run_demo(directory)
    with TemporaryDirectory() as directory:
        run_handoff_demo(directory)

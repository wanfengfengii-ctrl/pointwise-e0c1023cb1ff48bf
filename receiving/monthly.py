"""Independent month-settlement store for named day-account handoffs.

The month store keeps its own SQLite file and never reads the center store.
Original posting and sealing of the original race on this side: whichever
successfully commits first decides the original's final eligibility. A
correction declaration that arrives while the original is still open seals
the original and answers ``pending``; the follow-up settlement then posts the
full corrected amount. When the original is already posted the declaration
posts only the signed difference in the same commit.
"""

import json
import sqlite3
from contextlib import contextmanager

from receiving.handoff_wire import (build_reply, canonical_message, canonical_reply)
from stations.register import validate_day, validate_identifier


SCHEMA = '''
BEGIN IMMEDIATE;
CREATE TABLE IF NOT EXISTS month_handoffs (
    handoff_id TEXT PRIMARY KEY NOT NULL,
    day TEXT NOT NULL,
    original_account_id INTEGER NOT NULL,
    corrected_account_id INTEGER,
    original_state TEXT NOT NULL CHECK (original_state IN ('open', 'posted', 'blocked')),
    correction_state TEXT NOT NULL CHECK (correction_state IN ('none', 'ruled', 'posted')),
    original_message TEXT,
    declare_message TEXT,
    settle_message TEXT,
    original_reply TEXT,
    declare_reply TEXT,
    settle_reply TEXT
);
CREATE TABLE IF NOT EXISTS month_entries (
    receipt_id INTEGER PRIMARY KEY AUTOINCREMENT,
    handoff_id TEXT NOT NULL REFERENCES month_handoffs (handoff_id),
    day TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('original', 'correction')),
    original_account_id INTEGER NOT NULL,
    corrected_account_id INTEGER,
    original_posted INTEGER NOT NULL,
    liters TEXT NOT NULL
);
COMMIT;
'''

HANDOFF_COLUMNS = '''handoff_id, day, original_account_id, corrected_account_id,
                     original_state, correction_state, original_message, declare_message,
                     settle_message, original_reply, declare_reply, settle_reply'''
ENTRY_COLUMNS = '''receipt_id, handoff_id, day, kind, original_account_id,
                   corrected_account_id, original_posted, liters'''


class MonthCommitError(RuntimeError):
    """The month store could not commit the proposed business result."""


def initialize_month_store(connection):
    if connection.in_transaction:
        raise RuntimeError('month initialization requires an idle connection')
    connection.row_factory = sqlite3.Row
    connection.execute('PRAGMA foreign_keys = ON')
    try:
        connection.executescript(SCHEMA)
    except BaseException:
        connection.rollback()
        raise


def open_month_store(path):
    connection = sqlite3.connect(path, isolation_level=None)
    try:
        initialize_month_store(connection)
    except BaseException:
        connection.close()
        raise
    return connection


@contextmanager
def month_transaction(connection):
    connection.execute('BEGIN IMMEDIATE')
    try:
        yield
        try:
            connection.commit()
        except sqlite3.Error as error:
            raise MonthCommitError('month transaction could not commit') from error
    except BaseException:
        connection.rollback()
        raise


def _load_handoff(connection, handoff_id):
    return connection.execute(
        f'SELECT {HANDOFF_COLUMNS} FROM month_handoffs WHERE handoff_id = ?',
        (handoff_id,)).fetchone()


def _entry_receipt(row):
    return {'receipt_id': row['receipt_id'], 'handoff_id': row['handoff_id'], 'day': row['day'],
            'kind': row['kind'], 'original_account_id': row['original_account_id'],
            'corrected_account_id': row['corrected_account_id'],
            'original_posted': bool(row['original_posted']), 'liters': int(row['liters'])}


def _post_entry(connection, row, kind, liters, original_posted, corrected_account_id):
    result = connection.execute(
        '''INSERT INTO month_entries (handoff_id, day, kind, original_account_id,
                                      corrected_account_id, original_posted, liters)
           VALUES (?, ?, ?, ?, ?, ?, ?)''',
        (row['handoff_id'], row['day'], kind, row['original_account_id'], corrected_account_id,
         1 if original_posted else 0, str(liters)))
    return {'receipt_id': result.lastrowid, 'handoff_id': row['handoff_id'], 'day': row['day'],
            'kind': kind, 'original_account_id': row['original_account_id'],
            'corrected_account_id': corrected_account_id, 'original_posted': original_posted,
            'liters': liters}


def _establish(connection, message):
    conflict = connection.execute(
        'SELECT handoff_id FROM month_handoffs WHERE day = ?', (message['day'],)).fetchone()
    if conflict is not None:
        raise ValueError('day already has a different handoff')
    if message['kind'] == 'correction' and message['stage'] == 'settle':
        raise ValueError('settlement without a declared correction')
    corrected = message['corrected_account_id'] if message['kind'] == 'correction' else None
    connection.execute(
        '''INSERT INTO month_handoffs (handoff_id, day, original_account_id,
                                       corrected_account_id, original_state, correction_state)
           VALUES (?, ?, ?, ?, 'open', 'none')''',
        (message['handoff_id'], message['day'], message['original_account_id'], corrected))
    return _load_handoff(connection, message['handoff_id'])


def _check_binding(row, message):
    if row['day'] != message['day']:
        raise ValueError('message belongs to a different day')
    if row['original_account_id'] != message['original_account_id']:
        raise ValueError('handoff already has a different original account')
    if message['kind'] == 'correction':
        corrected = row['corrected_account_id']
        if corrected is not None and corrected != message['corrected_account_id']:
            raise ValueError('handoff already has a different corrected account')


def _stored_reply(row, message, canonical):
    if message['kind'] == 'original':
        stored_message, stored_reply = row['original_message'], row['original_reply']
    elif message['stage'] == 'declare':
        stored_message, stored_reply = row['declare_message'], row['declare_reply']
    else:
        stored_message, stored_reply = row['settle_message'], row['settle_reply']
    if stored_message is None:
        return None
    if stored_message != canonical:
        raise ValueError('handoff already has different material for this message')
    return json.loads(stored_reply)


def _save_reply(connection, message, canonical, reply, updates):
    assignments = ', '.join(f'{column} = ?' for column in (*updates, *message_columns(message)))
    values = tuple(updates.values()) + (canonical, canonical_reply(reply), message['handoff_id'])
    connection.execute(
        f'UPDATE month_handoffs SET {assignments} WHERE handoff_id = ?', values)
    return reply


def message_columns(message):
    if message['kind'] == 'original':
        return ('original_message', 'original_reply')
    if message['stage'] == 'declare':
        return ('declare_message', 'declare_reply')
    return ('settle_message', 'settle_reply')


def _process_original(connection, row, message, canonical):
    if row['original_state'] == 'blocked':
        return _save_reply(connection, message, canonical, build_reply(message, 'blocked'), {})
    receipt = _post_entry(connection, row, 'original', message['liters'], True, None)
    reply = build_reply(message, 'posted', receipt)
    return _save_reply(connection, message, canonical, reply, {'original_state': 'posted'})


def _process_declare(connection, row, message, canonical):
    updates = {}
    if row['corrected_account_id'] is None:
        updates['corrected_account_id'] = message['corrected_account_id']
    if row['original_state'] == 'posted':
        original = connection.execute(
            "SELECT liters FROM month_entries WHERE handoff_id = ? AND kind = 'original'",
            (row['handoff_id'],)).fetchone()
        difference = message['liters'] - int(original['liters'])
        receipt = _post_entry(connection, row, 'correction', difference, True,
                              message['corrected_account_id'])
        reply = build_reply(message, 'posted', receipt)
        updates['correction_state'] = 'posted'
        return _save_reply(connection, message, canonical, reply, updates)
    reply = build_reply(message, 'pending')
    updates['original_state'] = 'blocked'
    updates['correction_state'] = 'ruled'
    return _save_reply(connection, message, canonical, reply, updates)


def _process_settle(connection, row, message, canonical):
    if row['correction_state'] != 'ruled':
        raise ValueError('settlement requires a ruled correction')
    declare = json.loads(row['declare_message'])
    for field in ('day', 'original_account_id', 'corrected_account_id', 'liters'):
        if declare[field] != message[field]:
            raise ValueError('settlement does not match the declared correction')
    receipt = _post_entry(connection, row, 'correction', message['liters'], False,
                          message['corrected_account_id'])
    reply = build_reply(message, 'posted', receipt)
    return _save_reply(connection, message, canonical, reply, {'correction_state': 'posted'})


def accept_month_message(connection, message) -> dict:
    canonical = canonical_message(message)
    with month_transaction(connection):
        row = _load_handoff(connection, message['handoff_id'])
        if row is None:
            row = _establish(connection, message)
        else:
            _check_binding(row, message)
        stored = _stored_reply(row, message, canonical)
        if stored is not None:
            return stored
        if message['kind'] == 'original':
            return _process_original(connection, row, message, canonical)
        if message['stage'] == 'declare':
            return _process_declare(connection, row, message, canonical)
        return _process_settle(connection, row, message, canonical)


def get_month_receipt(connection, handoff_id, kind) -> dict | None:
    validate_identifier(handoff_id, 'handoff identifier')
    if kind not in ('original', 'correction'):
        raise ValueError('unknown handoff kind')
    row = connection.execute(
        f'SELECT {ENTRY_COLUMNS} FROM month_entries WHERE handoff_id = ? AND kind = ?',
        (handoff_id, kind)).fetchone()
    return None if row is None else _entry_receipt(row)


def read_month_entries(connection, day) -> list:
    validate_day(day)
    rows = connection.execute(
        f'SELECT {ENTRY_COLUMNS} FROM month_entries WHERE day = ? ORDER BY receipt_id', (day,))
    return [_entry_receipt(row) for row in rows]

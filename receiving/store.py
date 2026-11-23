import sqlite3
from contextlib import contextmanager


SCHEMA = '''
BEGIN IMMEDIATE;
CREATE TABLE IF NOT EXISTS accepted_blocks (
    block_id TEXT PRIMARY KEY NOT NULL,
    day TEXT NOT NULL,
    body TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS day_coverage (
    day TEXT NOT NULL,
    station TEXT NOT NULL CHECK (station IN ('east', 'west')),
    seq INTEGER NOT NULL CHECK (seq > 0),
    liters TEXT NOT NULL,
    meter_id TEXT NOT NULL,
    source_id TEXT NOT NULL,
    block_id TEXT NOT NULL REFERENCES accepted_blocks (block_id),
    PRIMARY KEY (day, station, seq)
);
CREATE TABLE IF NOT EXISTS accepted_corrections (
    notice_id TEXT PRIMARY KEY NOT NULL,
    day TEXT NOT NULL,
    body TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS accounts (
    account_id INTEGER PRIMARY KEY AUTOINCREMENT,
    day TEXT NOT NULL,
    notice_id TEXT REFERENCES accepted_corrections (notice_id),
    base_liters TEXT NOT NULL,
    east_liters TEXT NOT NULL,
    west_liters TEXT NOT NULL,
    total_liters TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS current_basis (
    day TEXT PRIMARY KEY NOT NULL,
    notice_id TEXT NOT NULL REFERENCES accepted_corrections (notice_id)
);
CREATE TABLE IF NOT EXISTS account_sources (
    account_id INTEGER NOT NULL REFERENCES accounts (account_id),
    station TEXT NOT NULL,
    seq INTEGER NOT NULL,
    meter_id TEXT NOT NULL,
    source_id TEXT NOT NULL,
    block_id TEXT NOT NULL REFERENCES accepted_blocks (block_id),
    PRIMARY KEY (account_id, station, seq)
);
CREATE TABLE IF NOT EXISTS receipts (
    receipt_id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL CHECK (kind IN ('block', 'correction')),
    material_id TEXT NOT NULL,
    day TEXT NOT NULL,
    digest TEXT NOT NULL,
    account_id INTEGER REFERENCES accounts (account_id),
    UNIQUE (kind, material_id),
    CHECK ((kind = 'correction') = (account_id IS NOT NULL))
);
CREATE TABLE IF NOT EXISTS handoffs (
    handoff_id TEXT PRIMARY KEY NOT NULL,
    day TEXT NOT NULL,
    original_account_id INTEGER NOT NULL REFERENCES accounts (account_id),
    corrected_account_id INTEGER REFERENCES accounts (account_id),
    status TEXT NOT NULL CHECK (status IN ('original', 'pending', 'complete')),
    correction_stage TEXT NOT NULL CHECK (correction_stage IN ('declared', 'ruled', 'posted')),
    original_message TEXT NOT NULL,
    correction_message TEXT,
    original_receipt TEXT,
    correction_receipt TEXT,
    original_blocked INTEGER NOT NULL DEFAULT 0
);
COMMIT;
'''


class CenterCommitError(RuntimeError):
    """The center could not commit the proposed business result."""


def initialize_center_store(connection):
    if connection.in_transaction:
        raise RuntimeError('center initialization requires an idle connection')
    connection.row_factory = sqlite3.Row
    connection.execute('PRAGMA foreign_keys = ON')
    try:
        connection.executescript(SCHEMA)
    except BaseException:
        connection.rollback()
        raise


def open_center_store(path):
    connection = sqlite3.connect(path, isolation_level=None)
    try:
        initialize_center_store(connection)
    except BaseException:
        connection.close()
        raise
    return connection


def require_center_transaction(connection):
    if not connection.in_transaction:
        raise RuntimeError('center write requires an active transaction')


@contextmanager
def center_transaction(connection):
    connection.execute('BEGIN IMMEDIATE')
    try:
        yield
        try:
            connection.commit()
        except sqlite3.Error as error:
            raise CenterCommitError('center transaction could not commit') from error
    except BaseException:
        connection.rollback()
        raise

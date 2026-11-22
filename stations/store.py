import sqlite3
from contextlib import contextmanager


SCHEMA = '''
BEGIN IMMEDIATE;
CREATE TABLE IF NOT EXISTS station_days (
    day TEXT NOT NULL,
    station TEXT NOT NULL CHECK (station IN ('east', 'west')),
    meter_id TEXT NOT NULL,
    scale_numerator TEXT NOT NULL,
    scale_denominator TEXT NOT NULL,
    last_sequence INTEGER NOT NULL DEFAULT 0 CHECK (last_sequence >= 0),
    PRIMARY KEY (day, station)
);
CREATE TABLE IF NOT EXISTS station_events (
    day TEXT NOT NULL,
    station TEXT NOT NULL,
    seq INTEGER NOT NULL CHECK (seq > 0),
    source_id TEXT NOT NULL,
    raw TEXT,
    liters TEXT NOT NULL,
    PRIMARY KEY (day, station, seq),
    FOREIGN KEY (day, station) REFERENCES station_days (day, station)
);
CREATE TABLE IF NOT EXISTS station_cuts (
    cut_id TEXT PRIMARY KEY NOT NULL,
    day TEXT NOT NULL,
    station TEXT NOT NULL,
    seq INTEGER NOT NULL CHECK (seq >= 0),
    FOREIGN KEY (day, station) REFERENCES station_days (day, station)
);
CREATE TABLE IF NOT EXISTS reconciliations (
    reconciliation_id TEXT PRIMARY KEY NOT NULL,
    day TEXT NOT NULL,
    east_cut_id TEXT NOT NULL REFERENCES station_cuts (cut_id),
    west_cut_id TEXT NOT NULL REFERENCES station_cuts (cut_id),
    base_liters TEXT NOT NULL,
    review_ref TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS issued_corrections (
    notice_id TEXT PRIMARY KEY NOT NULL,
    reconciliation_id TEXT NOT NULL REFERENCES reconciliations (reconciliation_id),
    body TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS releases (
    block_id TEXT PRIMARY KEY NOT NULL,
    day TEXT NOT NULL,
    station TEXT NOT NULL,
    body TEXT NOT NULL,
    FOREIGN KEY (day, station) REFERENCES station_days (day, station)
);
CREATE TABLE IF NOT EXISTS acknowledgements (
    block_id TEXT PRIMARY KEY NOT NULL REFERENCES releases (block_id),
    receipt_id INTEGER NOT NULL CHECK (receipt_id > 0),
    day TEXT NOT NULL,
    digest TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS retirements (
    block_id TEXT PRIMARY KEY NOT NULL REFERENCES acknowledgements (block_id),
    cleared_count INTEGER NOT NULL CHECK (cleared_count >= 0)
);
COMMIT;
'''


def initialize_station_store(connection):
    if connection.in_transaction:
        raise RuntimeError('station initialization requires an idle connection')
    connection.row_factory = sqlite3.Row
    connection.execute('PRAGMA foreign_keys = ON')
    try:
        connection.executescript(SCHEMA)
    except BaseException:
        connection.rollback()
        raise


def open_station_store(path):
    connection = sqlite3.connect(path, isolation_level=None)
    try:
        initialize_station_store(connection)
    except BaseException:
        connection.close()
        raise
    return connection


def require_station_transaction(connection):
    if not connection.in_transaction:
        raise RuntimeError('station write requires an active transaction')


@contextmanager
def station_transaction(connection):
    connection.execute('BEGIN IMMEDIATE')
    try:
        yield
        connection.commit()
    except BaseException:
        connection.rollback()
        raise

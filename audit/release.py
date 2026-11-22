from audit.reconcile import load_reconciliation, reconciliation_cuts
from audit.revisions import CorrectionNotice, canonical_notice, notice_from_body
from stations.register import validate_identifier
from stations.store import station_transaction


def load_issued_correction(connection, notice_id) -> CorrectionNotice | None:
    validate_identifier(notice_id, 'notice identifier')
    row = connection.execute(
        'SELECT body FROM issued_corrections WHERE notice_id = ?', (notice_id,),
    ).fetchone()
    return None if row is None else notice_from_body(row['body'])


def issue_correction(connection, notice_id, reconciliation_id) -> CorrectionNotice:
    validate_identifier(notice_id, 'notice identifier')
    with station_transaction(connection):
        review = load_reconciliation(connection, reconciliation_id)
        east, west = reconciliation_cuts(connection, review.day, review.east_cut_id,
                                         review.west_cut_id)
        notice = CorrectionNotice(
            notice_id, review.day, east.sequence, west.sequence, review.base_liters,
            review.reconciliation_id, east.cut_id, west.cut_id, review.review_ref,
        )
        body = canonical_notice(notice)
        previous = load_issued_correction(connection, notice_id)
        if previous is not None:
            if previous != notice:
                raise ValueError('notice identifier already has different content')
            return previous
        connection.execute(
            'INSERT INTO issued_corrections (notice_id, reconciliation_id, body) VALUES (?, ?, ?)',
            (notice_id, reconciliation_id, body),
        )
    return notice

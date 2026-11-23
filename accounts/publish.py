import json

from accounts.materialize import calculate_day
from accounts.provenance import save_account_provenance
from audit.revisions import CorrectionNotice, notice_from_body
from receiving.handoff_wire import (body_digest, build_correction_message,
                                    build_original_message, canonical_json, canonical_message,
                                    validate_reply)
from receiving.store import center_transaction, require_center_transaction
from stations.register import MAX_SEQUENCE, validate_day, validate_identifier


def load_current_basis(connection, day) -> CorrectionNotice | None:
    validate_day(day)
    row = connection.execute(
        '''SELECT c.body FROM current_basis AS b
           JOIN accepted_corrections AS c ON c.notice_id = b.notice_id WHERE b.day = ?''',
        (day,),
    ).fetchone()
    return None if row is None else notice_from_body(row['body'])


def stage_publication(connection, day, basis: CorrectionNotice | None) -> int:
    require_center_transaction(connection)
    totals = calculate_day(connection, day, basis)
    notice_id = None if basis is None else basis.notice_id
    result = connection.execute(
        '''INSERT INTO accounts (day, notice_id, base_liters, east_liters, west_liters, total_liters)
           VALUES (?, ?, ?, ?, ?, ?)''',
        (day, notice_id, str(totals.base_liters), str(totals.east_liters),
         str(totals.west_liters), str(totals.total_liters)),
    )
    account_id = result.lastrowid
    save_account_provenance(connection, account_id, totals.contributions)
    return account_id


def publish_day(connection, day) -> int:
    with center_transaction(connection):
        basis = load_current_basis(connection, day)
        return stage_publication(connection, day, basis)


HANDOFF_COLUMNS = '''handoff_id, day, original_account_id, corrected_account_id, status,
                     correction_stage, original_message, correction_message,
                     original_receipt, correction_receipt, original_blocked'''


def _validate_account_argument(account_id, name):
    if type(account_id) is not int or not 1 <= account_id <= MAX_SEQUENCE:
        raise ValueError(f'invalid {name}')


def _load_account_row(connection, account_id):
    return connection.execute(
        'SELECT day, total_liters, notice_id FROM accounts WHERE account_id = ?',
        (account_id,)).fetchone()


def _load_handoff(connection, handoff_id):
    validate_identifier(handoff_id, 'handoff identifier')
    return connection.execute(
        f'SELECT {HANDOFF_COLUMNS} FROM handoffs WHERE handoff_id = ?',
        (handoff_id,)).fetchone()


def _settle_message(row):
    declare = json.loads(row['correction_message'])
    return build_correction_message(row['handoff_id'], row['day'], row['original_account_id'],
                                    row['corrected_account_id'], declare['liters'], 'settle')


def start_handoff(connection, handoff_id, original_account_id) -> dict:
    _validate_account_argument(original_account_id, 'original account')
    with center_transaction(connection):
        account = _load_account_row(connection, original_account_id)
        if account is None:
            raise ValueError('unknown original account')
        day = account['day']
        existing = _load_handoff(connection, handoff_id)
        if existing is not None:
            if existing['original_account_id'] != original_account_id:
                raise ValueError('handoff already has a different original account')
            return json.loads(existing['original_message'])
        conflict = connection.execute(
            'SELECT handoff_id FROM handoffs WHERE day = ?', (day,)).fetchone()
        if conflict is not None:
            raise ValueError('day already has a different handoff')
        message = build_original_message(handoff_id, day, original_account_id,
                                         int(account['total_liters']))
        connection.execute(
            '''INSERT INTO handoffs (handoff_id, day, original_account_id, status,
                                     correction_stage, original_message, original_blocked)
               VALUES (?, ?, ?, 'original', 'declared', ?, 0)''',
            (handoff_id, day, original_account_id, canonical_message(message)))
        return message


def amend_handoff(connection, handoff_id, corrected_account_id) -> dict:
    _validate_account_argument(corrected_account_id, 'corrected account')
    with center_transaction(connection):
        row = _load_handoff(connection, handoff_id)
        if row is None:
            raise ValueError('unknown handoff')
        account = _load_account_row(connection, corrected_account_id)
        if account is None:
            raise ValueError('unknown corrected account')
        if account['day'] != row['day']:
            raise ValueError('corrected account belongs to a different day')
        if corrected_account_id <= row['original_account_id']:
            raise ValueError('corrected account must be later than the original')
        if account['notice_id'] is None:
            raise ValueError('corrected account is not a correction account')
        if row['corrected_account_id'] is not None:
            if row['corrected_account_id'] != corrected_account_id:
                raise ValueError('handoff already has a different corrected account')
            return json.loads(row['correction_message'])
        message = build_correction_message(handoff_id, row['day'], row['original_account_id'],
                                           corrected_account_id, int(account['total_liters']),
                                           'declare')
        connection.execute(
            '''UPDATE handoffs SET corrected_account_id = ?, correction_message = ?,
                   status = 'pending' WHERE handoff_id = ?''',
            (corrected_account_id, canonical_message(message), handoff_id))
        return message


def get_handoff(connection, handoff_id) -> dict | None:
    row = _load_handoff(connection, handoff_id)
    if row is None:
        return None
    return {'handoff_id': row['handoff_id'], 'day': row['day'],
            'original_account_id': row['original_account_id'],
            'corrected_account_id': row['corrected_account_id'],
            'status': row['status'],
            'original_receipt': (None if row['original_receipt'] is None
                                 else json.loads(row['original_receipt'])),
            'correction_receipt': (None if row['correction_receipt'] is None
                                   else json.loads(row['correction_receipt']))}


def resume_handoff(connection, handoff_id, kind) -> dict | None:
    if kind not in ('original', 'correction'):
        raise ValueError('unknown handoff kind')
    row = _load_handoff(connection, handoff_id)
    if row is None:
        return None
    if kind == 'original':
        if row['original_receipt'] is not None or row['original_blocked']:
            return None
        return json.loads(row['original_message'])
    if row['correction_message'] is None or row['correction_receipt'] is not None:
        return None
    if row['correction_stage'] == 'ruled':
        return _settle_message(row)
    return json.loads(row['correction_message'])


def _check_receipt(connection, row, kind, receipt):
    if receipt['handoff_id'] != row['handoff_id'] or receipt['day'] != row['day']:
        raise ValueError('receipt does not match the handoff')
    if receipt['kind'] != kind:
        raise ValueError('receipt kind does not match the reply')
    if receipt['original_account_id'] != row['original_account_id']:
        raise ValueError('receipt names a different original account')
    original_liters = int(_load_account_row(connection, row['original_account_id'])['total_liters'])
    if kind == 'original':
        if receipt['corrected_account_id'] is not None:
            raise ValueError('original receipt must not name a corrected account')
        if not receipt['original_posted']:
            raise ValueError('original receipt must record the original posting')
        if receipt['liters'] != original_liters:
            raise ValueError('original receipt liters do not match the account')
        correction = row['correction_receipt']
        if correction is not None and not json.loads(correction)['original_posted']:
            raise ValueError('correction already proved the original sealed')
        return
    if receipt['corrected_account_id'] != row['corrected_account_id']:
        raise ValueError('receipt names a different corrected account')
    corrected_liters = int(_load_account_row(connection, row['corrected_account_id'])['total_liters'])
    if receipt['original_posted']:
        if row['original_blocked']:
            raise ValueError('receipt contradicts the sealed original')
        expected = corrected_liters - original_liters
    else:
        if row['original_receipt'] is not None:
            raise ValueError('receipt contradicts the posted original')
        expected = corrected_liters
    if receipt['liters'] != expected:
        raise ValueError('correction receipt liters do not match the accounts')


def acknowledge_handoff(connection, handoff_id, reply) -> None:
    validate_identifier(handoff_id, 'handoff identifier')
    validate_reply(reply)
    with center_transaction(connection):
        row = _load_handoff(connection, handoff_id)
        if row is None:
            raise ValueError('unknown handoff')
        if reply['handoff_id'] != handoff_id or reply['day'] != row['day']:
            raise ValueError('reply does not match the handoff')
        kind = reply['kind']
        if kind == 'original':
            issued = row['original_message']
        elif reply['stage'] == 'declare':
            issued = row['correction_message']
            if issued is None:
                raise ValueError('reply names an unissued correction')
        else:
            if row['correction_message'] is None or row['correction_stage'] == 'declared':
                raise ValueError('reply names an unissued settlement')
            issued = canonical_message(_settle_message(row))
        if reply['material'] != body_digest(issued):
            raise ValueError('reply does not match the issued material')
        status = reply['status']
        if status == 'pending':
            if kind != 'correction' or reply['stage'] != 'declare':
                raise ValueError('only a correction declaration can be pending')
            if row['correction_stage'] == 'declared':
                connection.execute(
                    "UPDATE handoffs SET correction_stage = 'ruled' WHERE handoff_id = ?",
                    (handoff_id,))
            return None
        if status == 'blocked':
            if kind != 'original':
                raise ValueError('only the original can be blocked')
            if row['original_receipt'] is not None:
                raise ValueError('original already has a posted receipt')
            correction = row['correction_receipt']
            if correction is not None and json.loads(correction)['original_posted']:
                raise ValueError('correction already recorded the posted original')
            if not row['original_blocked']:
                connection.execute(
                    'UPDATE handoffs SET original_blocked = 1 WHERE handoff_id = ?',
                    (handoff_id,))
            return None
        receipt = reply['receipt']
        _check_receipt(connection, row, kind, receipt)
        canonical = canonical_json(receipt)
        if kind == 'original':
            if row['original_blocked']:
                raise ValueError('original is already sealed')
            if row['original_receipt'] is not None:
                if row['original_receipt'] != canonical:
                    raise ValueError('original already has a different receipt')
                return None
            connection.execute(
                'UPDATE handoffs SET original_receipt = ? WHERE handoff_id = ?',
                (canonical, handoff_id))
            return None
        if row['correction_receipt'] is not None:
            if row['correction_receipt'] != canonical:
                raise ValueError('correction already has a different receipt')
            return None
        blocked = row['original_blocked'] or not receipt['original_posted']
        connection.execute(
            '''UPDATE handoffs SET correction_receipt = ?, correction_stage = 'posted',
                   status = 'complete', original_blocked = ? WHERE handoff_id = ?''',
            (canonical, 1 if blocked else 0, handoff_id))
        return None

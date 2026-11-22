import hashlib
from dataclasses import dataclass

from receiving.store import require_center_transaction
from stations.register import validate_identifier


@dataclass(frozen=True)
class AcceptanceReceipt:
    receipt_id: int
    kind: str
    material_id: str
    day: str
    digest: str
    account_id: int | None = None


def body_digest(body: str) -> str:
    return hashlib.sha256(body.encode('utf-8')).hexdigest()


def load_receipt(connection, kind, material_id) -> AcceptanceReceipt | None:
    if kind not in ('block', 'correction'):
        raise ValueError('unknown receipt kind')
    validate_identifier(material_id, 'material identifier')
    row = connection.execute(
        '''SELECT receipt_id, kind, material_id, day, digest, account_id FROM receipts
           WHERE kind = ? AND material_id = ?''',
        (kind, material_id),
    ).fetchone()
    return None if row is None else AcceptanceReceipt(*row)


def retry_receipt(connection, kind, material_id, stored_body, proposed_body) -> AcceptanceReceipt:
    if stored_body != proposed_body:
        raise ValueError(f'{kind} identifier already has different content')
    receipt = load_receipt(connection, kind, material_id)
    if receipt is None:
        raise RuntimeError(f'committed {kind} has no receipt')
    return receipt


def stage_receipt(connection, kind, material_id, day, body, account_id=None) -> AcceptanceReceipt:
    require_center_transaction(connection)
    digest = body_digest(body)
    result = connection.execute(
        '''INSERT INTO receipts (kind, material_id, day, digest, account_id)
           VALUES (?, ?, ?, ?, ?)''',
        (kind, material_id, day, digest, account_id),
    )
    return AcceptanceReceipt(result.lastrowid, kind, material_id, day, digest, account_id)

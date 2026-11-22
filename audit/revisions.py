import json
from dataclasses import asdict, dataclass

from stations.register import validate_day, validate_identifier, validate_sequence


@dataclass(frozen=True)
class CorrectionNotice:
    notice_id: str
    day: str
    east_cut: int
    west_cut: int
    base_liters: int
    reconciliation_id: str
    east_cut_id: str
    west_cut_id: str
    review_ref: str


def canonical_notice(notice: CorrectionNotice) -> str:
    if not isinstance(notice, CorrectionNotice):
        raise ValueError('invalid correction notice')
    validate_day(notice.day)
    validate_identifier(notice.notice_id, 'notice identifier')
    validate_identifier(notice.reconciliation_id, 'reconciliation identifier')
    validate_identifier(notice.east_cut_id, 'east cut identifier')
    validate_identifier(notice.west_cut_id, 'west cut identifier')
    validate_identifier(notice.review_ref, 'review reference')
    validate_sequence(notice.east_cut)
    validate_sequence(notice.west_cut)
    if notice.east_cut_id == notice.west_cut_id or type(notice.base_liters) is not int:
        raise ValueError('invalid correction cuts or bottom ledger')
    return json.dumps(asdict(notice), sort_keys=True, separators=(',', ':'))


def notice_from_body(body: str) -> CorrectionNotice:
    try:
        notice = CorrectionNotice(**json.loads(body))
    except (TypeError, ValueError) as error:
        raise ValueError('invalid correction body') from error
    canonical_notice(notice)
    return notice

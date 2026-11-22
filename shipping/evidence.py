import json
from dataclasses import asdict, dataclass

from stations.register import validate_day, validate_identifier, validate_station, validate_window


@dataclass(frozen=True)
class SequenceEvidence:
    sequence: int
    liters: int
    meter_id: str
    source_id: str


@dataclass(frozen=True)
class IncrementBlock:
    block_id: str
    day: str
    station: str
    first: int
    last: int
    net_liters: int
    evidence: tuple[SequenceEvidence, ...]


def validate_block_evidence(block: IncrementBlock):
    if not isinstance(block, IncrementBlock):
        raise ValueError('invalid increment block')
    validate_identifier(block.block_id, 'block identifier')
    validate_day(block.day)
    validate_station(block.station)
    validate_window(block.first, block.last)
    if type(block.evidence) is not tuple or len(block.evidence) != block.last - block.first + 1:
        raise ValueError('block evidence does not cover its window')
    if type(block.net_liters) is not int:
        raise ValueError('block net amount must be integer liters')
    sum_liters = 0
    for sequence, item in enumerate(block.evidence, start=block.first):
        if not isinstance(item, SequenceEvidence):
            raise ValueError('invalid sequence evidence')
        if type(item.sequence) is not int or item.sequence != sequence:
            raise ValueError('block evidence must list the window in sequence order')
        if type(item.liters) is not int:
            raise ValueError('sequence amount must be integer liters')
        validate_identifier(item.meter_id, 'meter identifier')
        validate_identifier(item.source_id, 'source identifier')
        sum_liters += item.liters
    if sum_liters != block.net_liters:
        raise ValueError('block net amount disagrees with evidence')


def build_block_evidence(block_id, day, station, first, last, events) -> IncrementBlock:
    evidence = tuple(SequenceEvidence(event.sequence, event.liters, event.meter_id, event.source_id)
                     for event in events)
    block = IncrementBlock(block_id, day, station, first, last,
                           sum(item.liters for item in evidence), evidence)
    validate_block_evidence(block)
    return block


def canonical_block(block: IncrementBlock) -> str:
    validate_block_evidence(block)
    return json.dumps(asdict(block), sort_keys=True, separators=(',', ':'))


def block_from_body(body: str) -> IncrementBlock:
    try:
        payload = json.loads(body)
        evidence = payload['evidence']
        if not isinstance(evidence, list):
            raise ValueError('sequence evidence must be an array')
        payload['evidence'] = tuple(SequenceEvidence(**item) for item in evidence)
        block = IncrementBlock(**payload)
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError('invalid increment body') from error
    validate_block_evidence(block)
    return block

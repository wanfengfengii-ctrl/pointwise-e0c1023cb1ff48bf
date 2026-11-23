"""Wire format shared by the center handoff publisher and the month store.

Messages and replies are JSON round-trippable dictionaries. The center issues
an original message per business day and at most one correction declaration
plus a follow-up settlement. The month store answers every accepted message
with a reply that carries the digest of the message as binding evidence.
"""

import hashlib
import json

from stations.register import MAX_SEQUENCE, validate_day, validate_identifier


MESSAGE_TYPE = 'month-handoff'
KINDS = ('original', 'correction')
STAGES = ('declare', 'settle')
STATUSES = ('posted', 'blocked', 'pending')
MESSAGE_KEYS = frozenset(
    {'type', 'handoff_id', 'day', 'kind', 'original_account_id', 'corrected_account_id', 'liters'})
RECEIPT_KEYS = frozenset(
    {'receipt_id', 'handoff_id', 'day', 'kind', 'original_account_id', 'corrected_account_id',
     'original_posted', 'liters'})


def canonical_json(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'))


def _validate_account_id(value, name):
    if type(value) is not int or not 1 <= value <= MAX_SEQUENCE:
        raise ValueError(f'invalid {name}')


def validate_message(message):
    if not isinstance(message, dict):
        raise ValueError('invalid handoff message')
    kind = message.get('kind')
    if kind not in KINDS:
        raise ValueError('invalid handoff message kind')
    keys = set(MESSAGE_KEYS)
    if kind == 'correction':
        keys.add('stage')
    if set(message) != keys or message.get('type') != MESSAGE_TYPE:
        raise ValueError('invalid handoff message')
    validate_identifier(message['handoff_id'], 'handoff identifier')
    validate_day(message['day'])
    _validate_account_id(message['original_account_id'], 'original account')
    if type(message['liters']) is not int:
        raise ValueError('invalid handoff liters')
    corrected = message['corrected_account_id']
    if kind == 'original':
        if corrected is not None:
            raise ValueError('original message must not name a corrected account')
    else:
        _validate_account_id(corrected, 'corrected account')
        if corrected == message['original_account_id']:
            raise ValueError('corrected account must differ from the original')
        if message['stage'] not in STAGES:
            raise ValueError('invalid correction stage')


def canonical_message(message):
    validate_message(message)
    return canonical_json(message)


def body_digest(canonical):
    return hashlib.sha256(canonical.encode('utf-8')).hexdigest()


def message_digest(message):
    return body_digest(canonical_message(message))


def build_original_message(handoff_id, day, original_account_id, liters):
    message = {'type': MESSAGE_TYPE, 'handoff_id': handoff_id, 'day': day, 'kind': 'original',
               'original_account_id': original_account_id, 'corrected_account_id': None,
               'liters': liters}
    validate_message(message)
    return message


def build_correction_message(handoff_id, day, original_account_id, corrected_account_id,
                             liters, stage):
    message = {'type': MESSAGE_TYPE, 'handoff_id': handoff_id, 'day': day, 'kind': 'correction',
               'stage': stage, 'original_account_id': original_account_id,
               'corrected_account_id': corrected_account_id, 'liters': liters}
    validate_message(message)
    return message


def validate_receipt(receipt):
    if not isinstance(receipt, dict) or set(receipt) != RECEIPT_KEYS:
        raise ValueError('invalid month receipt')
    if type(receipt['receipt_id']) is not int or receipt['receipt_id'] < 1:
        raise ValueError('invalid receipt identifier')
    validate_identifier(receipt['handoff_id'], 'handoff identifier')
    validate_day(receipt['day'])
    if receipt['kind'] not in KINDS:
        raise ValueError('invalid receipt kind')
    _validate_account_id(receipt['original_account_id'], 'original account')
    corrected = receipt['corrected_account_id']
    if corrected is not None:
        _validate_account_id(corrected, 'corrected account')
    if type(receipt['original_posted']) is not bool:
        raise ValueError('invalid original posting flag')
    if type(receipt['liters']) is not int:
        raise ValueError('invalid receipt liters')


def build_reply(message, status, receipt=None):
    if status not in STATUSES:
        raise ValueError('invalid reply status')
    reply = {'status': status, 'handoff_id': message['handoff_id'], 'day': message['day'],
             'kind': message['kind'], 'material': message_digest(message)}
    if message['kind'] == 'correction':
        reply['stage'] = message['stage']
    if receipt is not None:
        validate_receipt(receipt)
        reply['receipt'] = receipt
    return reply


def validate_reply(reply):
    if not isinstance(reply, dict):
        raise ValueError('invalid handoff reply')
    status = reply.get('status')
    kind = reply.get('kind')
    if status not in STATUSES:
        raise ValueError('invalid handoff reply status')
    if kind not in KINDS:
        raise ValueError('invalid handoff reply kind')
    keys = {'status', 'handoff_id', 'day', 'kind', 'material'}
    if kind == 'correction':
        keys.add('stage')
    if status == 'posted':
        keys.add('receipt')
    if set(reply) != keys:
        raise ValueError('invalid handoff reply fields')
    validate_identifier(reply['handoff_id'], 'handoff identifier')
    validate_day(reply['day'])
    material = reply['material']
    if type(material) is not str or len(material) != 64 \
            or any(digit not in '0123456789abcdef' for digit in material):
        raise ValueError('invalid reply material')
    if kind == 'correction' and reply['stage'] not in STAGES:
        raise ValueError('invalid correction stage')
    if status == 'posted':
        validate_receipt(reply['receipt'])


def canonical_reply(reply):
    validate_reply(reply)
    return canonical_json(reply)

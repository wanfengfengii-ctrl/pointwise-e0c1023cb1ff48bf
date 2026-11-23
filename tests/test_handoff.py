import unittest
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory

from accounts.publish import (acknowledge_handoff, amend_handoff, get_handoff, resume_handoff,
                              start_handoff)
from demo import DAY
from handoff_fixture import prepare_case, reject_commit, wire
from receiving.monthly import (MonthCommitError, accept_month_message, get_month_receipt,
                               open_month_store, read_month_entries)
from receiving.store import CenterCommitError, open_center_store


class TestHandoff(unittest.TestCase):
    def setUp(self):
        self.directory = Path(self.enterContext(TemporaryDirectory()))
        self.original, self.corrected = prepare_case(self.directory)
        self.center = self.enterContext(
            closing(open_center_store(self.directory / 'center.sqlite')))
        self.month = self.enterContext(
            closing(open_month_store(self.directory / 'month.sqlite')))
        self.handoff_id = 'handoff-1'

    def reopen_center(self):
        self.center.close()
        self.center = self.enterContext(
            closing(open_center_store(self.directory / 'center.sqlite')))

    def reopen_month(self):
        self.month.close()
        self.month = self.enterContext(
            closing(open_month_store(self.directory / 'month.sqlite')))

    def begin(self):
        original = start_handoff(self.center, self.handoff_id, self.original.account_id)
        declare = amend_handoff(self.center, self.handoff_id, self.corrected.account_id)
        return original, declare

    def extra_case(self, review_base):
        directory = Path(self.enterContext(TemporaryDirectory()))
        original, corrected = prepare_case(directory, review_base)
        center = self.enterContext(closing(open_center_store(directory / 'center.sqlite')))
        month = self.enterContext(closing(open_month_store(directory / 'month.sqlite')))
        return original, corrected, center, month

    def difference_flow(self, review_base):
        original, corrected, center, month = self.extra_case(review_base)
        message = start_handoff(center, self.handoff_id, original.account_id)
        declare = amend_handoff(center, self.handoff_id, corrected.account_id)
        reply = accept_month_message(month, wire(message))
        correction_reply = accept_month_message(month, wire(declare))
        acknowledge_handoff(center, self.handoff_id, wire(reply))
        acknowledge_handoff(center, self.handoff_id, wire(correction_reply))
        return corrected, center, month, correction_reply

    def test_normal_order_and_status_ladder(self):
        original_message, declare = self.begin()
        self.assertEqual(original_message['liters'], 14)
        self.assertEqual(declare['liters'], 105)
        self.assertEqual(get_handoff(self.center, self.handoff_id)['status'], 'pending')
        reply = accept_month_message(self.month, wire(original_message))
        self.assertEqual(reply['status'], 'posted')
        self.assertEqual(reply['receipt']['liters'], 14)
        acknowledge_handoff(self.center, self.handoff_id, wire(reply))
        handoff = get_handoff(self.center, self.handoff_id)
        self.assertEqual(handoff['status'], 'pending')
        self.assertEqual(handoff['original_receipt'], reply['receipt'])
        correction_reply = accept_month_message(self.month, wire(declare))
        self.assertEqual(correction_reply['status'], 'posted')
        self.assertEqual(correction_reply['receipt']['liters'], 91)
        self.assertTrue(correction_reply['receipt']['original_posted'])
        acknowledge_handoff(self.center, self.handoff_id, wire(correction_reply))
        handoff = get_handoff(self.center, self.handoff_id)
        self.assertEqual(handoff['status'], 'complete')
        self.assertEqual(handoff['correction_receipt'], correction_reply['receipt'])
        self.assertIsNone(resume_handoff(self.center, self.handoff_id, 'original'))
        self.assertIsNone(resume_handoff(self.center, self.handoff_id, 'correction'))

    def test_status_original_before_amend(self):
        start_handoff(self.center, self.handoff_id, self.original.account_id)
        handoff = get_handoff(self.center, self.handoff_id)
        self.assertEqual(handoff['status'], 'original')
        self.assertIsNone(handoff['corrected_account_id'])
        self.assertIsNone(handoff['original_receipt'])
        self.assertIsNone(handoff['correction_receipt'])
        self.assertIsNone(resume_handoff(self.center, self.handoff_id, 'correction'))

    def test_original_first_with_lost_reply_and_supplementary_ack(self):
        original_message, declare = self.begin()
        lost = accept_month_message(self.month, wire(original_message))
        self.assertEqual(lost['status'], 'posted')
        correction_reply = accept_month_message(self.month, wire(declare))
        acknowledge_handoff(self.center, self.handoff_id, wire(correction_reply))
        handoff = get_handoff(self.center, self.handoff_id)
        self.assertEqual(handoff['status'], 'complete')
        self.assertIsNone(handoff['original_receipt'])
        self.assertEqual(handoff['correction_receipt']['liters'], 91)
        self.reopen_center()
        self.reopen_month()
        entries = read_month_entries(self.month, DAY)
        self.assertEqual([[e['kind'], e['liters'], e['original_posted']] for e in entries],
                         [['original', 14, True], ['correction', 91, True]])
        self.assertEqual(get_month_receipt(self.month, self.handoff_id, 'original')['liters'], 14)
        self.assertEqual(get_month_receipt(self.month, self.handoff_id, 'correction')['liters'], 91)
        resumed = resume_handoff(self.center, self.handoff_id, 'original')
        self.assertEqual(resumed, original_message)
        replayed = accept_month_message(self.month, wire(resumed))
        self.assertEqual(replayed, lost)
        acknowledge_handoff(self.center, self.handoff_id, wire(replayed))
        handoff = get_handoff(self.center, self.handoff_id)
        self.assertEqual(handoff['status'], 'complete')
        self.assertEqual(handoff['original_receipt']['liters'], 14)
        self.assertEqual(handoff['original_receipt']['receipt_id'], lost['receipt']['receipt_id'])
        self.assertIsNone(resume_handoff(self.center, self.handoff_id, 'original'))

    def test_correction_first_seals_original(self):
        original_message, declare = self.begin()
        pending = accept_month_message(self.month, wire(declare))
        self.assertEqual(pending['status'], 'pending')
        self.assertNotIn('receipt', pending)
        blocked = accept_month_message(self.month, wire(original_message))
        self.assertEqual(blocked['status'], 'blocked')
        self.assertNotIn('receipt', blocked)
        acknowledge_handoff(self.center, self.handoff_id, wire(blocked))
        self.assertIsNone(resume_handoff(self.center, self.handoff_id, 'original'))
        self.assertEqual(resume_handoff(self.center, self.handoff_id, 'correction'), declare)
        self.assertEqual(accept_month_message(self.month, wire(declare)), pending)
        acknowledge_handoff(self.center, self.handoff_id, wire(pending))
        settle = resume_handoff(self.center, self.handoff_id, 'correction')
        self.assertEqual(settle['stage'], 'settle')
        self.assertEqual(settle['liters'], 105)
        posted = accept_month_message(self.month, wire(settle))
        self.assertEqual(posted['status'], 'posted')
        self.assertEqual(posted['receipt']['liters'], 105)
        self.assertFalse(posted['receipt']['original_posted'])
        acknowledge_handoff(self.center, self.handoff_id, wire(posted))
        self.reopen_center()
        self.reopen_month()
        handoff = get_handoff(self.center, self.handoff_id)
        self.assertEqual(handoff['status'], 'complete')
        self.assertIsNone(handoff['original_receipt'])
        self.assertEqual(handoff['correction_receipt']['liters'], 105)
        self.assertFalse(handoff['correction_receipt']['original_posted'])
        entries = read_month_entries(self.month, DAY)
        self.assertEqual([[e['kind'], e['liters'], e['original_posted']] for e in entries],
                         [['correction', 105, False]])
        self.assertIsNone(get_month_receipt(self.month, self.handoff_id, 'original'))
        self.assertEqual(get_month_receipt(self.month, self.handoff_id, 'correction')['liters'], 105)
        self.assertEqual(accept_month_message(self.month, wire(original_message))['status'],
                         'blocked')
        self.assertEqual(accept_month_message(self.month, wire(settle)), posted)
        acknowledge_handoff(self.center, self.handoff_id, wire(pending))
        acknowledge_handoff(self.center, self.handoff_id, wire(blocked))
        self.assertEqual(get_handoff(self.center, self.handoff_id), handoff)

    def test_failed_ruling_then_original_posts_first(self):
        original_message, declare = self.begin()
        with reject_commit(self.month):
            with self.assertRaises(MonthCommitError):
                accept_month_message(self.month, wire(declare))
        self.reopen_month()
        self.assertEqual(read_month_entries(self.month, DAY), [])
        self.assertIsNone(get_month_receipt(self.month, self.handoff_id, 'correction'))
        reply = accept_month_message(self.month, wire(original_message))
        self.assertEqual(reply['status'], 'posted')
        correction_reply = accept_month_message(self.month, wire(declare))
        self.assertEqual(correction_reply['status'], 'posted')
        self.assertEqual(correction_reply['receipt']['liters'], 91)
        acknowledge_handoff(self.center, self.handoff_id, wire(reply))
        acknowledge_handoff(self.center, self.handoff_id, wire(correction_reply))
        self.assertEqual(get_handoff(self.center, self.handoff_id)['status'], 'complete')
        self.assertEqual([e['liters'] for e in read_month_entries(self.month, DAY)], [14, 91])

    def test_failed_settle_keeps_sealed_state(self):
        original_message, declare = self.begin()
        pending = accept_month_message(self.month, wire(declare))
        acknowledge_handoff(self.center, self.handoff_id, wire(pending))
        settle = resume_handoff(self.center, self.handoff_id, 'correction')
        with reject_commit(self.month):
            with self.assertRaises(MonthCommitError):
                accept_month_message(self.month, wire(settle))
        self.reopen_month()
        self.assertEqual(read_month_entries(self.month, DAY), [])
        self.assertEqual(accept_month_message(self.month, wire(original_message))['status'],
                         'blocked')
        posted = accept_month_message(self.month, wire(settle))
        self.assertEqual(posted['receipt']['liters'], 105)
        acknowledge_handoff(self.center, self.handoff_id, wire(posted))
        self.assertEqual(get_handoff(self.center, self.handoff_id)['status'], 'complete')
        self.assertEqual([e['liters'] for e in read_month_entries(self.month, DAY)], [105])

    def test_negative_difference(self):
        corrected, center, month, correction_reply = self.difference_flow(5)
        self.assertEqual(corrected.total_liters, 10)
        self.assertEqual(correction_reply['receipt']['liters'], -4)
        entries = read_month_entries(month, DAY)
        self.assertEqual([e['liters'] for e in entries], [14, -4])
        self.assertTrue(entries[1]['original_posted'])
        handoff = get_handoff(center, self.handoff_id)
        self.assertEqual(handoff['status'], 'complete')
        self.assertEqual(handoff['correction_receipt']['liters'], -4)

    def test_zero_difference(self):
        corrected, center, month, correction_reply = self.difference_flow(9)
        self.assertEqual(corrected.total_liters, 14)
        self.assertEqual(correction_reply['receipt']['liters'], 0)
        self.assertEqual([e['liters'] for e in read_month_entries(month, DAY)], [14, 0])
        self.assertEqual(get_handoff(center, self.handoff_id)['status'], 'complete')

    def test_same_params_reclaim_and_reopen(self):
        original_message, declare = self.begin()
        self.assertEqual(start_handoff(self.center, self.handoff_id, self.original.account_id),
                         original_message)
        self.assertEqual(amend_handoff(self.center, self.handoff_id, self.corrected.account_id),
                         declare)
        reply = accept_month_message(self.month, wire(original_message))
        correction_reply = accept_month_message(self.month, wire(declare))
        self.reopen_center()
        self.reopen_month()
        self.assertEqual(resume_handoff(self.center, self.handoff_id, 'original'), original_message)
        self.assertEqual(resume_handoff(self.center, self.handoff_id, 'correction'), declare)
        self.assertEqual(accept_month_message(self.month, wire(original_message)), reply)
        self.assertEqual(accept_month_message(self.month, wire(declare)), correction_reply)
        self.assertEqual(get_month_receipt(self.month, self.handoff_id, 'original'),
                         reply['receipt'])
        self.assertEqual(get_month_receipt(self.month, self.handoff_id, 'correction'),
                         correction_reply['receipt'])
        self.assertEqual([e['liters'] for e in read_month_entries(self.month, DAY)], [14, 91])
        acknowledge_handoff(self.center, self.handoff_id, wire(reply))
        acknowledge_handoff(self.center, self.handoff_id, wire(correction_reply))
        self.reopen_center()
        handoff = get_handoff(self.center, self.handoff_id)
        self.assertEqual(handoff['status'], 'complete')
        self.assertEqual(handoff['original_receipt'], reply['receipt'])
        self.assertEqual(handoff['correction_receipt'], correction_reply['receipt'])
        acknowledge_handoff(self.center, self.handoff_id, wire(reply))
        acknowledge_handoff(self.center, self.handoff_id, wire(correction_reply))
        self.assertEqual(get_handoff(self.center, self.handoff_id), handoff)

    def test_center_rebinding_rejected(self):
        original_message, declare = self.begin()
        with self.assertRaises(ValueError):
            start_handoff(self.center, self.handoff_id, self.corrected.account_id)
        with self.assertRaises(ValueError):
            start_handoff(self.center, 'handoff-2', self.original.account_id)
        with self.assertRaises(ValueError):
            amend_handoff(self.center, self.handoff_id, self.original.account_id)
        with self.assertRaises(ValueError):
            amend_handoff(self.center, self.handoff_id, self.corrected.account_id + 100)
        with self.assertRaises(ValueError):
            amend_handoff(self.center, 'handoff-unknown', self.corrected.account_id)
        with self.assertRaises(ValueError):
            start_handoff(self.center, 'handoff-unknown', self.original.account_id + 100)
        handoff = get_handoff(self.center, self.handoff_id)
        self.assertEqual(handoff['original_account_id'], self.original.account_id)
        self.assertEqual(handoff['corrected_account_id'], self.corrected.account_id)
        self.assertEqual(resume_handoff(self.center, self.handoff_id, 'original'), original_message)
        self.assertEqual(resume_handoff(self.center, self.handoff_id, 'correction'), declare)

    def test_month_rebinding_and_wrong_material_rejected(self):
        original_message, declare = self.begin()
        reply = accept_month_message(self.month, wire(original_message))
        tampered = wire(original_message)
        tampered['liters'] = 99
        with self.assertRaises(ValueError):
            accept_month_message(self.month, tampered)
        renamed = wire(original_message)
        renamed['handoff_id'] = 'handoff-2'
        with self.assertRaises(ValueError):
            accept_month_message(self.month, renamed)
        cross_day = wire(original_message)
        cross_day['day'] = '2026-05-15'
        with self.assertRaises(ValueError):
            accept_month_message(self.month, cross_day)
        other_account = wire(original_message)
        other_account['original_account_id'] = self.original.account_id + 100
        with self.assertRaises(ValueError):
            accept_month_message(self.month, other_account)
        correction_reply = accept_month_message(self.month, wire(declare))
        self.assertEqual(correction_reply['receipt']['liters'], 91)
        other_correction = wire(declare)
        other_correction['corrected_account_id'] = self.corrected.account_id + 100
        with self.assertRaises(ValueError):
            accept_month_message(self.month, other_correction)
        settle = wire(declare)
        settle['stage'] = 'settle'
        with self.assertRaises(ValueError):
            accept_month_message(self.month, settle)
        self.assertEqual([e['liters'] for e in read_month_entries(self.month, DAY)], [14, 91])
        self.assertEqual(get_month_receipt(self.month, self.handoff_id, 'original'),
                         reply['receipt'])

    def test_invalid_material_rejected(self):
        for bad in (None, 'message', [], {}, {'type': 'month-handoff'},
                    {'type': 'month-handoff', 'kind': 'original'}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                accept_month_message(self.month, bad)
        self.assertEqual(read_month_entries(self.month, DAY), [])
        with self.assertRaises(ValueError):
            get_month_receipt(self.month, self.handoff_id, 'sideways')
        with self.assertRaises(ValueError):
            resume_handoff(self.center, self.handoff_id, 'sideways')

    def test_reply_mismatch_rejected(self):
        original_message, declare = self.begin()
        reply = accept_month_message(self.month, wire(original_message))
        with self.assertRaises(ValueError):
            acknowledge_handoff(self.center, 'handoff-unknown', wire(reply))
        wrong_id = wire(reply)
        wrong_id['handoff_id'] = 'handoff-other'
        with self.assertRaises(ValueError):
            acknowledge_handoff(self.center, self.handoff_id, wrong_id)
        wrong_material = wire(reply)
        wrong_material['material'] = '0' * 64
        with self.assertRaises(ValueError):
            acknowledge_handoff(self.center, self.handoff_id, wrong_material)
        wrong_liters = wire(reply)
        wrong_liters['receipt'] = dict(reply['receipt'], liters=99)
        with self.assertRaises(ValueError):
            acknowledge_handoff(self.center, self.handoff_id, wrong_liters)
        wrong_account = wire(reply)
        wrong_account['receipt'] = dict(reply['receipt'], original_account_id=reply['receipt']
                                        ['original_account_id'] + 100)
        with self.assertRaises(ValueError):
            acknowledge_handoff(self.center, self.handoff_id, wrong_account)
        correction_reply = accept_month_message(self.month, wire(declare))
        as_original = wire(correction_reply)
        as_original['kind'] = 'original'
        del as_original['stage']
        with self.assertRaises(ValueError):
            acknowledge_handoff(self.center, self.handoff_id, as_original)
        for malformed in (None, 'reply', {}, {'status': 'posted'},
                          dict(wire(reply), status='sideways')):
            with self.subTest(malformed=malformed), self.assertRaises(ValueError):
                acknowledge_handoff(self.center, self.handoff_id, malformed)
        handoff = get_handoff(self.center, self.handoff_id)
        self.assertIsNone(handoff['original_receipt'])
        self.assertIsNone(handoff['correction_receipt'])
        acknowledge_handoff(self.center, self.handoff_id, wire(reply))
        acknowledge_handoff(self.center, self.handoff_id, wire(correction_reply))
        self.assertEqual(get_handoff(self.center, self.handoff_id)['status'], 'complete')

    def test_contradictory_replies_rejected(self):
        original_message, declare = self.begin()
        reply = accept_month_message(self.month, wire(original_message))
        correction_reply = accept_month_message(self.month, wire(declare))
        acknowledge_handoff(self.center, self.handoff_id, wire(reply))
        blocked = dict(wire(reply), status='blocked')
        del blocked['receipt']
        with self.assertRaises(ValueError):
            acknowledge_handoff(self.center, self.handoff_id, blocked)
        acknowledge_handoff(self.center, self.handoff_id, wire(correction_reply))
        self.assertEqual(get_handoff(self.center, self.handoff_id)['status'], 'complete')
        sealed = self.extra_case(100)[1:]
        self.assertEqual(sealed[0].total_liters, 105)

    def test_center_commit_failure_keeps_last_state(self):
        with reject_commit(self.center):
            with self.assertRaises(CenterCommitError):
                start_handoff(self.center, self.handoff_id, self.original.account_id)
        self.reopen_center()
        self.assertIsNone(get_handoff(self.center, self.handoff_id))
        original_message, declare = self.begin()
        reply = accept_month_message(self.month, wire(original_message))
        with reject_commit(self.center):
            with self.assertRaises(CenterCommitError):
                acknowledge_handoff(self.center, self.handoff_id, wire(reply))
        self.reopen_center()
        self.assertIsNone(get_handoff(self.center, self.handoff_id)['original_receipt'])
        acknowledge_handoff(self.center, self.handoff_id, wire(reply))
        self.assertEqual(get_handoff(self.center, self.handoff_id)['original_receipt'],
                         reply['receipt'])
        correction_reply = accept_month_message(self.month, wire(declare))
        acknowledge_handoff(self.center, self.handoff_id, wire(correction_reply))
        self.assertEqual(get_handoff(self.center, self.handoff_id)['status'], 'complete')

    def test_amend_commit_failure_keeps_declaration_unsent(self):
        original_message = start_handoff(self.center, self.handoff_id, self.original.account_id)
        with reject_commit(self.center):
            with self.assertRaises(CenterCommitError):
                amend_handoff(self.center, self.handoff_id, self.corrected.account_id)
        self.reopen_center()
        handoff = get_handoff(self.center, self.handoff_id)
        self.assertEqual(handoff['status'], 'original')
        self.assertIsNone(handoff['corrected_account_id'])
        self.assertIsNone(resume_handoff(self.center, self.handoff_id, 'correction'))
        declare = amend_handoff(self.center, self.handoff_id, self.corrected.account_id)
        self.assertEqual(amend_handoff(self.center, self.handoff_id, self.corrected.account_id),
                         declare)
        self.assertEqual(resume_handoff(self.center, self.handoff_id, 'original'), original_message)

    def test_unknown_handoff_queries(self):
        self.assertIsNone(get_handoff(self.center, 'handoff-none'))
        self.assertIsNone(resume_handoff(self.center, 'handoff-none', 'original'))
        self.assertIsNone(resume_handoff(self.center, 'handoff-none', 'correction'))
        self.assertIsNone(get_month_receipt(self.month, 'handoff-none', 'original'))
        self.assertEqual(read_month_entries(self.month, DAY), [])


if __name__ == '__main__':
    unittest.main()

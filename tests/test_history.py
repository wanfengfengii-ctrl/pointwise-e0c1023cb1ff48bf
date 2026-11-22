from accounts.history import get_account
from accounts.publish import publish_day
from demo import DAY, amounts, deliver_and_retire, prepare_day, source_rows
from receiving.audit_intake import accept_correction
from receiving.intake import accept_block
from shipping.pack import issue_block
from tests import WarehouseCase


class TestHistory(WarehouseCase):
    def _history(self, include_later=False):
        notice, blocks, later = prepare_day(self.source, include_later=include_later)
        old = deliver_and_retire(self.source, self.center, blocks)
        self.source.close()
        corrected = get_account(self.center, accept_correction(self.center, notice).account_id)
        return old, corrected, later

    def test_frozen_account(self):
        old, corrected, _ = self._history()
        self.reopen_center()
        self.assertNotEqual(old.account_id, corrected.account_id)
        actual_old = get_account(self.center, old.account_id)
        actual_new = get_account(self.center, corrected.account_id)
        self.assertEqual(actual_old, old)
        self.assertEqual(actual_new, corrected)
        self.assertEqual(amounts(actual_old), [0, 7, 7, 14])
        self.assertEqual(amounts(actual_new), [100, 3, 2, 105])
        self.assertIsNone(actual_old.basis)
        self.assertEqual(actual_new.basis.review_ref, 'worksheet-1')
        self.assertEqual(source_rows(actual_old), [
            ['east', 1, 'E-17', 'east-1', 'east-block'], ['east', 2, 'E-17', 'east-2', 'east-block'],
            ['west', 1, 'W-29', 'west-1', 'west-block'], ['west', 2, 'W-29', 'west-2', 'west-block'],
        ])
        self.assertEqual(source_rows(actual_new), [
            ['east', 2, 'E-17', 'east-2', 'east-block'], ['west', 2, 'W-29', 'west-2', 'west-block'],
        ])

    def test_next_account(self):
        old, corrected, later = self._history(include_later=True)
        self.reopen_center()
        self.assertEqual(amounts(get_account(self.center, old.account_id)), [0, 7, 7, 14])
        self.assertEqual(amounts(get_account(self.center, corrected.account_id)), [100, 3, 2, 105])
        accept_block(self.center, later)
        next_id = publish_day(self.center, DAY)
        self.reopen_center()
        latest = get_account(self.center, next_id)
        self.assertEqual(amounts(latest), [100, 3, 8, 111])
        self.assertEqual(latest.basis, corrected.basis)
        self.assertEqual(source_rows(latest), [
            ['east', 2, 'E-17', 'east-2', 'east-block'], ['west', 2, 'W-29', 'west-2', 'west-block'],
            ['west', 3, 'W-29', 'west-3', 'west-later'],
        ])
        self.assertEqual(get_account(self.center, old.account_id), old)
        self.assertEqual(get_account(self.center, corrected.account_id), corrected)

    def test_unknown_account(self):
        self.load_regular()
        accept_block(self.center, issue_block(self.source, DAY, 'east', 'east-block', 1, 2))
        original = get_account(self.center, publish_day(self.center, DAY))
        self.reopen_center()
        with self.assertRaises(KeyError):
            get_account(self.center, original.account_id + 500)
        self.assertEqual(get_account(self.center, original.account_id), original)
        self.assertEqual(amounts(original), [0, 7, 0, 7])

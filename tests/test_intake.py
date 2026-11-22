import sqlite3
from dataclasses import replace

from accounts.history import get_account
from accounts.publish import publish_day
from demo import DAY, amounts, fail_commit_on_insert
from receiving.coverage import read_day_coverage
from receiving.intake import accept_block
from receiving.receipts import load_receipt
from receiving.store import CenterCommitError
from shipping.acknowledge import load_acknowledgement
from shipping.pack import issue_block
from shipping.retire import load_retirement, retire_release
from stations.journal import read_event_window
from tests import WarehouseCase


class TestIntake(WarehouseCase):
    def setUp(self):
        super().setUp()
        self.load_regular()
        self.east = issue_block(self.source, DAY, 'east', 'east-block', 1, 2)
        self.west = issue_block(self.source, DAY, 'west', 'west-block', 1, 2)

    def snapshot(self):
        return get_account(self.center, publish_day(self.center, DAY))

    def test_exact_retry(self):
        original = accept_block(self.center, self.east)
        before = self.snapshot()
        self.assertEqual(amounts(before), [0, 7, 0, 7])
        self.reopen_center()
        self.assertEqual(accept_block(self.center, self.east), original)
        self.assertEqual(load_receipt(self.center, 'block', self.east.block_id), original)
        after = self.snapshot()
        self.assertEqual(amounts(after), [0, 7, 0, 7])
        self.assertEqual(after.sources, before.sources)

    def test_commit_failure(self):
        with fail_commit_on_insert(self.center, 'accepted_blocks'):
            with self.assertRaises(CenterCommitError) as failure:
                accept_block(self.center, self.east)
            self.assertIsInstance(failure.exception.__cause__, sqlite3.IntegrityError)
        self.reopen_center()
        self.assertIsNone(load_receipt(self.center, 'block', self.east.block_id))
        self.assertEqual(read_day_coverage(self.center, DAY), ())
        self.assertEqual(amounts(self.snapshot()), [0, 0, 0, 0])
        original = accept_block(self.center, self.east)
        self.assertEqual(accept_block(self.center, self.east), original)
        self.assertEqual(len(read_day_coverage(self.center, DAY)), 2)
        self.assertEqual(amounts(self.snapshot()), [0, 7, 0, 7])

    def test_compatible_overlap(self):
        first = issue_block(self.source, DAY, 'east', 'east-first', 1, 1)
        first_receipt = accept_block(self.center, first)
        self.assertEqual(amounts(self.snapshot()), [0, 4, 0, 4])
        self.reopen_center()
        self.assertEqual(accept_block(self.center, first), first_receipt)
        second_receipt = accept_block(self.center, self.east)
        self.assertNotEqual(second_receipt.receipt_id, first_receipt.receipt_id)
        accept_block(self.center, self.west)
        account = self.snapshot()
        self.assertEqual(amounts(account), [0, 7, 7, 14])
        self.assertEqual([(r.station, r.sequence, r.block_id) for r in account.sources], [
            ('east', 1, 'east-first'), ('east', 2, 'east-block'),
            ('west', 1, 'west-block'), ('west', 2, 'west-block'),
        ])

    def test_changed_retry(self):
        original = accept_block(self.center, self.east)
        changed = replace(self.east, net_liters=8,
                          evidence=(replace(self.east.evidence[0], liters=5), self.east.evidence[1]))
        with self.assertRaisesRegex(ValueError, 'different content'):
            accept_block(self.center, changed)
        self.assertEqual(load_receipt(self.center, 'block', self.east.block_id), original)
        self.assertEqual(amounts(self.snapshot()), [0, 7, 0, 7])
        self.assertEqual([r.liters for r in read_day_coverage(self.center, DAY)], [4, 3])

    def test_conflicting_overlap(self):
        first = issue_block(self.source, DAY, 'east', 'east-first', 1, 1)
        accept_block(self.center, first)
        prior = read_day_coverage(self.center, DAY)
        current = self.east.evidence[0]
        cases = [(replace(current, liters=6), 9), (replace(current, source_id='other-reference'), 7)]
        for item, total in cases:
            with self.subTest(item=item):
                bad = replace(self.east, block_id='east-conflict', net_liters=total,
                              evidence=(item, self.east.evidence[1]))
                with self.assertRaisesRegex(ValueError, 'conflicting evidence'):
                    accept_block(self.center, bad)
                self.assertEqual(read_day_coverage(self.center, DAY), prior)
                self.assertIsNone(load_receipt(self.center, 'block', bad.block_id))
        self.assertEqual(amounts(self.snapshot()), [0, 4, 0, 4])
        accept_block(self.center, self.east)
        self.assertEqual(amounts(self.snapshot()), [0, 7, 0, 7])

    def test_unmatched_ack(self):
        west_receipt = accept_block(self.center, self.west)
        target = self.east.block_id
        with self.assertRaises(ValueError):
            retire_release(self.source, target)
        with self.assertRaisesRegex(ValueError, 'does not match'):
            retire_release(self.source, target, west_receipt)
        self.assertIsNone(load_acknowledgement(self.source, target))
        self.assertIsNone(load_retirement(self.source, target))
        self.assertEqual([r.raw for r in read_event_window(self.source, DAY, 'east', 1, 2)], ['8.0', '6/1'])
        east_receipt = accept_block(self.center, self.east)
        self.assertEqual(retire_release(self.source, target, east_receipt).cleared_count, 2)
        self.assertEqual([r.raw for r in read_event_window(self.source, DAY, 'east', 1, 2)], [None, None])

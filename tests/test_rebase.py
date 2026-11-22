import sqlite3
from contextlib import closing
from dataclasses import replace

from accounts.history import get_account
from accounts.publish import load_current_basis, publish_day
from audit.cuts import freeze_station_cut, load_station_cut
from audit.reconcile import load_reconciliation, record_reconciliation
from audit.release import issue_correction, load_issued_correction
from audit.revisions import canonical_notice, notice_from_body
from demo import (DAY, add_observation, amounts, deliver_and_retire, fail_commit_on_insert,
                  prepare_day, register_example, source_rows)
from receiving.audit_intake import accept_correction, load_accepted_correction
from receiving.coverage import read_day_coverage
from receiving.intake import accept_block
from receiving.receipts import load_receipt
from receiving.store import CenterCommitError, open_center_store
from shipping.pack import issue_block
from stations.journal import latest_sequence, read_event_window
from stations.store import open_station_store
from tests import WarehouseCase


class TestRebase(WarehouseCase):
    def test_late_notice_after_retire(self):
        notice, blocks, _ = prepare_day(self.source)
        self.assertEqual((notice.east_cut, notice.west_cut, notice.base_liters), (1, 1, 100))
        old = deliver_and_retire(self.source, self.center, blocks)
        self.assertEqual(amounts(old), [0, 7, 7, 14])
        for station in ('east', 'west'):
            self.assertEqual([r.raw for r in read_event_window(self.source, DAY, station, 1, 2)],
                             [None, None])
        self.source.close()
        self.reopen_center()
        receipt = accept_correction(self.center, notice)
        revised = get_account(self.center, receipt.account_id)
        self.assertEqual(revised.total_liters, 105,
                         f'REBASE_TAIL_LOST observed={revised.total_liters} L expected=105 L')
        self.assertEqual(amounts(revised), [100, 3, 2, 105])
        self.assertEqual(revised.basis, notice)
        self.assertEqual(source_rows(revised), [
            ['east', 2, 'E-17', 'east-2', 'east-block'], ['west', 2, 'W-29', 'west-2', 'west-block'],
        ])
        self.reopen_center()
        self.assertEqual(get_account(self.center, old.account_id), old)
        self.assertEqual(len(old.sources), 4)
        self.assertEqual(get_account(self.center, revised.account_id), revised)

    def test_equal_total_pair(self):
        cases = [(('8.0', '6/1'), 3, 105), (('6/1', '8.0'), 4, 106)]
        for raw, east_suffix, expected in cases:
            with self.subTest(raw=raw):
                with closing(open_center_store(self.directory / f'center-{expected}.sqlite')) as center:
                    with closing(open_station_store(self.directory / f'source-{expected}.sqlite')) as source:
                        notice, blocks, _ = prepare_day(source, east_raw=raw)
                        self.assertEqual([block.net_liters for block in blocks], [7, 7])
                        old = deliver_and_retire(source, center, blocks)
                    self.assertEqual(amounts(old), [0, 7, 7, 14])
                    new = get_account(center, accept_correction(center, notice).account_id)
                    self.assertEqual(amounts(new), [100, east_suffix, 2, expected])
                    self.assertEqual([(r.station, r.sequence) for r in new.sources], [('east', 2), ('west', 2)])
                    self.assertEqual(get_account(center, old.account_id), old)

    def test_asymmetric_cuts(self):
        notice, blocks, _ = prepare_day(self.source, west_cut=2)
        self.assertEqual((notice.east_cut, notice.west_cut), (1, 2))
        old = deliver_and_retire(self.source, self.center, blocks)
        self.source.close()
        new = get_account(self.center, accept_correction(self.center, notice).account_id)
        self.assertEqual(amounts(new), [100, 3, 0, 103])
        self.assertEqual([(r.station, r.sequence) for r in new.sources], [('east', 2)])
        self.assertEqual(get_account(self.center, old.account_id), old)

    def test_frozen_identity(self):
        notice, _, _ = prepare_day(self.source)
        self.assertEqual(latest_sequence(self.source, DAY, 'east'), 2)
        self.assertEqual(latest_sequence(self.source, DAY, 'west'), 2)
        frozen = load_station_cut(self.source, 'east-cut')
        self.assertEqual(frozen.sequence, 1)
        self.assertEqual(freeze_station_cut(self.source, DAY, 'east', 'east-cut'), frozen)
        review = load_reconciliation(self.source, 'review-1')
        self.assertEqual((review.east_cut_id, review.west_cut_id, review.review_ref),
                         ('east-cut', 'west-cut', 'worksheet-1'))
        for east, west in (('missing', 'west-cut'), ('west-cut', 'east-cut')):
            with self.subTest(east=east, west=west), self.assertRaises(ValueError):
                record_reconciliation(self.source, 'bad-review', DAY, east, west, 100, 'sheet')
        other_day = '2026-05-15'
        register_example(self.source, other_day)
        freeze_station_cut(self.source, other_day, 'west', 'west-next-day')
        with self.assertRaises(ValueError):
            record_reconciliation(self.source, 'bad-review', DAY, 'east-cut', 'west-next-day', 100, 'sheet')
        with self.assertRaises(ValueError):
            record_reconciliation(self.source, 'bad-review', DAY, 'east-cut', 'west-cut', True, 'sheet')
        changed = record_reconciliation(self.source, 'changed-review', DAY, 'east-cut', 'west-cut',
                                        101, 'changed-sheet')
        with self.assertRaises(ValueError):
            issue_correction(self.source, notice.notice_id, changed.reconciliation_id)
        self.reopen_source()
        self.assertEqual(issue_correction(self.source, notice.notice_id, 'review-1'), notice)
        self.assertEqual(load_issued_correction(self.source, notice.notice_id), notice)
        self.assertEqual(notice_from_body(canonical_notice(notice)), notice)
        self.assertEqual((notice.east_cut, notice.west_cut, notice.base_liters), (1, 1, 100))

    def test_late_covered_and_uncovered(self):
        notice, _, _ = prepare_day(self.source)
        covered = issue_block(self.source, DAY, 'west', 'west-covered', 1, 1)
        uncovered = issue_block(self.source, DAY, 'west', 'west-uncovered', 2, 2)
        self.source.close()
        first_account = accept_correction(self.center, notice).account_id
        self.assertEqual(amounts(get_account(self.center, first_account)), [100, 0, 0, 100])
        covered_receipt = accept_block(self.center, covered)
        cover_only = get_account(self.center, publish_day(self.center, DAY))
        self.assertEqual(amounts(cover_only), [100, 0, 0, 100])
        self.assertEqual(cover_only.sources, ())
        uncovered_receipt = accept_block(self.center, uncovered)
        self.assertEqual(accept_block(self.center, covered), covered_receipt)
        self.assertEqual(accept_block(self.center, uncovered), uncovered_receipt)
        changed = replace(uncovered, net_liters=3, evidence=(replace(uncovered.evidence[0], liters=3),))
        with self.assertRaises(ValueError):
            accept_block(self.center, changed)
        self.assertEqual([(r.sequence, r.liters) for r in read_day_coverage(self.center, DAY)], [(1, 5), (2, 2)])
        latest = get_account(self.center, publish_day(self.center, DAY))
        self.assertEqual(amounts(latest), [100, 0, 2, 102])
        self.assertEqual(source_rows(latest), [['west', 2, 'W-29', 'west-2', 'west-uncovered']])
        self.assertEqual(amounts(get_account(self.center, first_account)), [100, 0, 0, 100])

    def test_notice_retry(self):
        notice, _, _ = prepare_day(self.source)
        original = accept_correction(self.center, notice)
        account = get_account(self.center, original.account_id)
        self.reopen_center()
        self.assertEqual(accept_correction(self.center, notice), original)
        self.assertEqual(load_receipt(self.center, 'correction', notice.notice_id), original)
        with self.assertRaisesRegex(ValueError, 'different content'):
            accept_correction(self.center, replace(notice, base_liters=101))
        self.assertEqual(load_accepted_correction(self.center, notice.notice_id), notice)
        self.assertEqual(get_account(self.center, account.account_id), account)
        with self.assertRaises(KeyError):
            get_account(self.center, account.account_id + 1)

    def _rolled_back_correction(self):
        notice, blocks, _ = prepare_day(self.source)
        old = deliver_and_retire(self.source, self.center, blocks)
        self.assertEqual(amounts(old), [0, 7, 7, 14])
        self.assertEqual([r.source_id for r in old.sources], ['east-1', 'east-2', 'west-1', 'west-2'])
        self.source.close()
        with fail_commit_on_insert(self.center, 'accounts'):
            with self.assertRaises(CenterCommitError) as failure:
                accept_correction(self.center, notice)
            self.assertIsInstance(failure.exception.__cause__, sqlite3.IntegrityError)
        self.reopen_center()
        self.assertIsNone(load_current_basis(self.center, DAY))
        self.assertIsNone(load_accepted_correction(self.center, notice.notice_id))
        self.assertIsNone(load_receipt(self.center, 'correction', notice.notice_id))
        self.assertEqual(get_account(self.center, old.account_id), old)
        with self.assertRaises(KeyError):
            get_account(self.center, old.account_id + 1)
        return old, notice

    def test_failed_publish(self):
        old, _ = self._rolled_back_correction()
        next_account = get_account(self.center, publish_day(self.center, DAY))
        self.assertEqual(amounts(next_account), [0, 7, 7, 14])
        self.assertEqual(next_account.sources, old.sources)
        self.assertIsNone(next_account.basis)

    def test_retry_after_failure(self):
        old, notice = self._rolled_back_correction()
        receipt = accept_correction(self.center, notice)
        self.assertEqual(accept_correction(self.center, notice), receipt)
        self.assertEqual(load_current_basis(self.center, DAY), notice)
        revised = get_account(self.center, receipt.account_id)
        self.assertEqual(amounts(revised), [100, 3, 2, 105])
        self.assertEqual([r.source_id for r in revised.sources], ['east-2', 'west-2'])
        self.assertEqual(get_account(self.center, old.account_id), old)
        with self.assertRaises(KeyError):
            get_account(self.center, receipt.account_id + 1)

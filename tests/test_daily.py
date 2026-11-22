from accounts.history import get_account
from accounts.publish import publish_day
from demo import DAY, add_observation, amounts, observations, register_example, source_rows
from receiving.intake import accept_block
from receiving.receipts import load_receipt
from receiving.store import initialize_center_store
from shipping.acknowledge import load_acknowledgement, record_acknowledgement
from shipping.evidence import block_from_body, canonical_block
from shipping.pack import issue_block
from shipping.releases import load_release
from shipping.retire import load_retirement, retire_release
from stations.import_rows import Reading, import_rows
from stations.journal import latest_sequence, read_event_window
from stations.register import load_station_day, register_station_day
from stations.store import initialize_station_store
from stations.units import MeterScale
from tests import WarehouseCase


class TestDaily(WarehouseCase):
    def test_two_station_day(self):
        east, west = self.load_regular()
        self.assertEqual([(r.sequence, r.raw, r.liters) for r in east], [(1, '8.0', 4), (2, '6/1', 3)])
        self.assertEqual([(r.sequence, r.raw, r.liters) for r in west], [(1, '12.5', 5), (2, '5', 2)])
        for station in ('east', 'west'):
            block = issue_block(self.source, DAY, station, station + '-block', 1, 2)
            self.assertEqual(block.net_liters, 7)
            self.assertEqual(block_from_body(canonical_block(block)), block)
            receipt = accept_block(self.center, block)
            self.assertEqual(retire_release(self.source, block.block_id, receipt).cleared_count, 2)
            self.assertEqual([r.raw for r in read_event_window(self.source, DAY, station, 1, 2)], [None, None])
        account = get_account(self.center, publish_day(self.center, DAY))
        self.assertEqual(amounts(account), [0, 7, 7, 14])
        self.assertIsNone(account.basis)
        self.assertEqual(source_rows(account), [
            ['east', 1, 'E-17', 'east-1', 'east-block'], ['east', 2, 'E-17', 'east-2', 'east-block'],
            ['west', 1, 'W-29', 'west-1', 'west-block'], ['west', 2, 'W-29', 'west-2', 'west-block'],
        ])

    def test_zero_reading(self):
        self.load_regular()
        zero = add_observation(self.source, 'east', 'east-zero', '0.00')
        self.assertEqual((zero.sequence, zero.liters), (3, 0))
        accept_block(self.center, issue_block(self.source, DAY, 'east', 'east-with-zero', 1, 3))
        accept_block(self.center, issue_block(self.source, DAY, 'west', 'west-block', 1, 2))
        account = get_account(self.center, publish_day(self.center, DAY))
        self.assertEqual(amounts(account), [0, 7, 7, 14])
        self.assertEqual(len(account.sources), 5)
        self.assertEqual(source_rows(account)[2], ['east', 3, 'E-17', 'east-zero', 'east-with-zero'])

    def test_release_retry_after_retire(self):
        self.load_regular()
        original = issue_block(self.source, DAY, 'east', 'east-block', 1, 2)
        receipt = accept_block(self.center, original)
        self.assertEqual(record_acknowledgement(self.source, original.block_id, receipt), receipt)
        retired = retire_release(self.source, original.block_id)
        self.assertEqual((retired.first, retired.last, retired.receipt_id, retired.cleared_count),
                         (1, 2, receipt.receipt_id, 2))
        self.reopen_source()
        self.assertEqual(load_acknowledgement(self.source, original.block_id), receipt)
        self.assertEqual(load_retirement(self.source, original.block_id), retired)
        self.assertEqual(retire_release(self.source, original.block_id, receipt), retired)
        self.assertEqual(load_release(self.source, original.block_id), original)
        reissued = issue_block(self.source, DAY, 'east', original.block_id, 1, 2)
        self.assertEqual(canonical_block(reissued), canonical_block(original))
        self.assertEqual(accept_block(self.center, reissued), receipt)
        with self.assertRaises(ValueError):
            issue_block(self.source, DAY, 'east', original.block_id, 2, 2)
        self.assertEqual([r.raw for r in read_event_window(self.source, DAY, 'east', 1, 2)], [None, None])

    def test_repeat_initialize(self):
        scale = MeterScale(3, 2)
        configured = register_station_day(self.source, DAY, 'east', 'custom-meter', scale)
        first = import_rows(self.source, DAY, 'east', [Reading('custom-meter', 'a', 2)])[0]
        self.assertEqual((first.sequence, first.liters), (1, 3))
        block = issue_block(self.source, DAY, 'east', 'custom-block', 1, 1)
        receipt = accept_block(self.center, block)
        old = get_account(self.center, publish_day(self.center, DAY))
        self.assertEqual(amounts(old), [0, 3, 0, 3])
        initialize_station_store(self.source)
        initialize_center_store(self.center)
        self.reopen_source()
        self.reopen_center()
        self.assertEqual(load_station_day(self.source, DAY, 'east'), configured)
        self.assertEqual(register_station_day(self.source, DAY, 'east', 'custom-meter', MeterScale(6, 4)),
                         configured)
        with self.assertRaises(ValueError):
            register_station_day(self.source, DAY, 'east', 'custom-meter', MeterScale(1, 1))
        self.assertEqual(latest_sequence(self.source, DAY, 'east'), 1)
        self.assertEqual(load_receipt(self.center, 'block', block.block_id), receipt)
        self.assertEqual(get_account(self.center, old.account_id), old)
        second = import_rows(self.source, DAY, 'east', [Reading('custom-meter', 'b', '4')])[0]
        self.assertEqual((second.sequence, second.liters), (2, 6))

    def test_invalid_conversion(self):
        register_example(self.source)
        original = add_observation(self.source, 'east', 'east-first', '8')
        valid = Reading('E-17', 'valid', '6')
        for bad in ('1', None, '5/0'):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                import_rows(self.source, DAY, 'east', [valid, Reading('E-17', 'bad', bad)])
        with self.assertRaises(ValueError):
            import_rows(self.source, DAY, 'east', [])
        self.reopen_source()
        self.assertEqual(latest_sequence(self.source, DAY, 'east'), 1)
        self.assertEqual(read_event_window(self.source, DAY, 'east', 1, 1), (original,))
        with self.assertRaises(ValueError):
            read_event_window(self.source, DAY, 'east', 1, 2)
        recovered = import_rows(self.source, DAY, 'east', [valid])[0]
        self.assertEqual((recovered.sequence, recovered.liters), (2, 3))

    def test_bad_source(self):
        register_example(self.source)
        good = Reading('E-17', 'good', '8')
        for bad in (Reading('W-29', 'wrong-meter', '6'), Reading('E-17', ' ', '6')):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                import_rows(self.source, DAY, 'east', [good, bad])
        self.reopen_source()
        self.assertEqual(latest_sequence(self.source, DAY, 'east'), 0)
        recovered = import_rows(self.source, DAY, 'east', [good])[0]
        self.assertEqual((recovered.sequence, recovered.source_id, recovered.liters), (1, 'good', 4))

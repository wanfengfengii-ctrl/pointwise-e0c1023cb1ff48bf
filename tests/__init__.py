import unittest
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory

from demo import DAY, observations, register_example
from receiving.store import open_center_store
from stations.import_rows import import_rows
from stations.store import open_station_store


class WarehouseCase(unittest.TestCase):
    def setUp(self):
        self.directory = Path(self.enterContext(TemporaryDirectory()))
        self.source_path = self.directory / 'source.sqlite'
        self.center_path = self.directory / 'center.sqlite'
        self.source = self.enterContext(closing(open_station_store(self.source_path)))
        self.center = self.enterContext(closing(open_center_store(self.center_path)))

    def reopen_source(self):
        self.source.close()
        self.source = self.enterContext(closing(open_station_store(self.source_path)))

    def reopen_center(self):
        self.center.close()
        self.center = self.enterContext(closing(open_center_store(self.center_path)))

    def load_regular(self):
        register_example(self.source)
        east = import_rows(self.source, DAY, 'east', observations('east', ('east-1', '8.0'),
                                                                 ('east-2', '6/1')))
        west = import_rows(self.source, DAY, 'west', observations('west', ('west-1', '12.5'),
                                                                 ('west-2', '5')))
        return east, west

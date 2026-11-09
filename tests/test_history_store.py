import os
import tempfile
import unittest

from history_store import OperationHistory


class OperationHistoryTests(unittest.TestCase):
    def test_records_are_persistent_and_newest_first(self):
        with tempfile.TemporaryDirectory() as directory:
            history = OperationHistory(directory)
            history.record("Split", "storage/emulated/0/map.png",
                           "storage/emulated/0/RadarTools", "144 tiles")
            history.record("Merge", "storage/emulated/0/RadarTools",
                           "storage/emulated/0/map.png", "map.png")

            reopened = OperationHistory(directory)
            records = reopened.list_records()
            self.assertEqual(len(records), 2)
            self.assertEqual(records[0]["operation"], "Merge")
            self.assertEqual(records[1]["operation"], "Split")
            self.assertEqual(records[1]["details"], "144 tiles")
            self.assertEqual(records[0]["status"], "success")
            self.assertTrue(os.path.isfile(os.path.join(
                directory, "operation_history.json")))


if __name__ == "__main__":
    unittest.main()

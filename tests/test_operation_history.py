import json
import os
import tempfile
import unittest
from unittest import mock

from operation_history import OperationHistory


class OperationHistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.temp.name, "history.json")

    def tearDown(self):
        self.temp.cleanup()

    def test_persists_and_reloads_entries(self):
        store = OperationHistory(self.path)
        self.assertTrue(store.add("Merged 144 tiles", "/input", "/output"))
        entry = OperationHistory(self.path).list_entries()[0]
        self.assertEqual(entry["title"], "Merged 144 tiles")
        self.assertEqual(entry["input_path"], "/input")
        self.assertEqual(entry["output_path"], "/output")
        self.assertEqual(entry["status"], "success")

    def test_caps_entries_and_keeps_newest_first(self):
        store = OperationHistory(self.path, max_entries=3)
        for number in range(5):
            store.add("Operation " + str(number))
        self.assertEqual(
            [item["title"] for item in store.list_entries()],
            ["Operation 4", "Operation 3", "Operation 2"],
        )

    def test_corrupt_history_does_not_crash_and_can_recover(self):
        with open(self.path, "w", encoding="utf-8") as target:
            target.write("{not valid json")
        store = OperationHistory(self.path)
        self.assertEqual(store.list_entries(), [])
        self.assertTrue(store.add("Recovered"))
        with open(self.path, encoding="utf-8") as source:
            self.assertEqual(json.load(source)[0]["title"], "Recovered")

    def test_disk_write_failure_does_not_raise_or_lose_memory_entry(self):
        store = OperationHistory(self.path)
        with mock.patch("operation_history.os.replace", side_effect=OSError("disk full")):
            self.assertFalse(store.add("Still recorded in memory"))
        self.assertEqual(store.list_entries()[0]["title"], "Still recorded in memory")

    def test_failed_status_is_normalized(self):
        store = OperationHistory(self.path)
        store.add("Split map", status="failed")
        self.assertEqual(store.list_entries()[0]["status"], "failed")


if __name__ == "__main__":
    unittest.main()

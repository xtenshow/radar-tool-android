import os
import tempfile
import unittest
from unittest import mock

from file_transactions import atomic_write, commit_staged_files


class FileTransactionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = self.temp.name
        self.output = os.path.join(self.root, "output")
        os.makedirs(self.output)

    def tearDown(self):
        self.temp.cleanup()

    def test_atomic_write_replaces_complete_file(self):
        destination = os.path.join(self.output, "map.png")
        with open(destination, "wb") as target:
            target.write(b"old")
        atomic_write(destination, lambda path: open(path, "wb").write(b"new"))
        with open(destination, "rb") as source:
            self.assertEqual(source.read(), b"new")

    def test_atomic_write_preserves_old_file_when_writer_fails(self):
        destination = os.path.join(self.output, "map.png")
        with open(destination, "wb") as target:
            target.write(b"old")
        def fail(_path):
            raise RuntimeError("write failed")
        with self.assertRaises(RuntimeError):
            atomic_write(destination, fail)
        with open(destination, "rb") as source:
            self.assertEqual(source.read(), b"old")

    def test_batch_commit_and_restore_existing_outputs(self):
        staging = os.path.join(self.output, "stage")
        os.makedirs(staging)
        for name, data in (("a.png", b"new-a"), ("b.png", b"new-b")):
            with open(os.path.join(staging, name), "wb") as target:
                target.write(data)
        with open(os.path.join(self.output, "a.png"), "wb") as target:
            target.write(b"old-a")
        self.assertEqual(
            commit_staged_files(staging, self.output, ["a.png", "b.png"]), 2
        )
        for name, expected in (("a.png", b"new-a"), ("b.png", b"new-b")):
            with open(os.path.join(self.output, name), "rb") as source:
                self.assertEqual(source.read(), expected)

    def test_rejects_path_traversal(self):
        staging = os.path.join(self.output, "stage")
        os.makedirs(staging)
        with self.assertRaises(ValueError):
            commit_staged_files(staging, self.output, ["../escape.txt"])

    def test_rollback_restores_original_when_batch_replace_fails(self):
        staging = os.path.join(self.output, "stage")
        os.makedirs(staging)
        with open(os.path.join(staging, "a.png"), "wb") as target:
            target.write(b"new")
        with open(os.path.join(self.output, "a.png"), "wb") as target:
            target.write(b"old")
        real_replace = os.replace
        calls = {"count": 0}
        def fail_on_install(source, destination):
            calls["count"] += 1
            # 1: backup the old file, 2: fail installing staged file.
            if calls["count"] == 2:
                raise OSError("simulated disk error")
            return real_replace(source, destination)
        with mock.patch("file_transactions.os.replace", side_effect=fail_on_install):
            with self.assertRaises(OSError):
                commit_staged_files(staging, self.output, ["a.png"])
        with open(os.path.join(self.output, "a.png"), "rb") as source:
            self.assertEqual(source.read(), b"old")


if __name__ == "__main__":
    unittest.main()

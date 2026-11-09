import unittest

from android_storage import document_id_to_display_path


class DisplayPathTests(unittest.TestCase):
    def test_primary_volume_nested_path(self):
        self.assertEqual(
            document_id_to_display_path("primary:RadarTools/Purple"),
            "storage/emulated/0/RadarTools/Purple",
        )

    def test_primary_downloads_path(self):
        self.assertEqual(
            document_id_to_display_path("primary:Download/Maps"),
            "storage/emulated/0/Download/Maps",
        )

    def test_other_volume_is_not_reported_as_internal_storage(self):
        self.assertEqual(
            document_id_to_display_path("ABCD-1234:RadarTools/Purple"),
            "storage/ABCD-1234/RadarTools/Purple",
        )


if __name__ == "__main__":
    unittest.main()

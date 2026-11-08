import unittest

from android_storage import _to_java_byte_values


class JavaByteConversionTests(unittest.TestCase):
    def test_all_byte_values_remain_in_unsigned_range(self):
        payload = bytes(range(256))
        values = _to_java_byte_values(payload)

        self.assertEqual(values, list(range(256)))
        self.assertTrue(all(0 <= value <= 255 for value in values))

    def test_high_bit_bytes_round_trip_without_negative_values(self):
        payload = bytes(range(0x80, 0x100))
        values = _to_java_byte_values(payload)

        self.assertEqual(bytes(values), payload)
        self.assertTrue(all(value >= 128 for value in values))


if __name__ == "__main__":
    unittest.main()

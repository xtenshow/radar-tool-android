import struct
import unittest

from PIL import Image

from image_algorithms import (
    SECTION_TEX_DICT,
    RW_VERSION,
    make_txd,
    recolor_image,
    tile_position,
)


class TilePositionTests(unittest.TestCase):
    def test_grid_corners_without_flip(self):
        self.assertEqual(tile_position(0, 256, 256), (0, 0))
        self.assertEqual(tile_position(11, 256, 256), (2816, 0))
        self.assertEqual(tile_position(143, 256, 256), (2816, 2816))

    def test_vertical_flip(self):
        self.assertEqual(tile_position(0, 256, 256, True), (0, 2816))
        self.assertEqual(tile_position(132, 256, 256, True), (0, 0))

    def test_rejects_invalid_tile_indices_and_dimensions(self):
        for index in (-1, 144):
            with self.subTest(index=index), self.assertRaises(ValueError):
                tile_position(index, 256, 256)
        with self.assertRaises(ValueError):
            tile_position(0, 0, 256)

    def test_all_144_positions_are_unique_and_cover_same_grid_when_flipped(self):
        normal = {tile_position(i, 10, 20) for i in range(144)}
        flipped = {tile_position(i, 10, 20, True) for i in range(144)}
        expected = {(x * 10, y * 20) for y in range(12) for x in range(12)}
        self.assertEqual(len(normal), 144)
        self.assertEqual(normal, expected)
        self.assertEqual(flipped, expected)


class RecolorTests(unittest.TestCase):
    def test_replaces_matching_color_and_preserves_alpha(self):
        source = Image.new("RGBA", (2, 1))
        source.putdata([(100, 100, 100, 23), (0, 0, 255, 200)])
        result = recolor_image(source, (100, 100, 100), (255, 0, 0), 10)
        self.assertEqual(list(result.getdata()), [(255, 0, 0, 23), (0, 0, 255, 200)])
        self.assertEqual(list(source.getdata())[0], (100, 100, 100, 23))

    def test_tolerance_boundary_does_not_replace_far_pixel(self):
        source = Image.new("RGBA", (1, 1), (255, 255, 255, 77))
        result = recolor_image(source, (0, 0, 0), (255, 0, 0), 10)
        self.assertEqual(result.getpixel((0, 0)), (255, 255, 255, 77))

    def test_invalid_rgb_values_are_rejected(self):
        source = Image.new("RGBA", (1, 1), (0, 0, 0, 255))
        with self.assertRaises(ValueError):
            recolor_image(source, (256, 0, 0), (0, 0, 0), 20)


class TxdTests(unittest.TestCase):
    def test_txd_has_renderware_header_and_expected_texture_data(self):
        image = Image.new("RGBA", (1, 1), (10, 20, 30, 40))
        data = make_txd("radar00", image)
        section_type, section_length, version = struct.unpack("<III", data[:12])
        self.assertEqual(section_type, SECTION_TEX_DICT)
        self.assertEqual(section_length, len(data) - 12)
        self.assertEqual(version, RW_VERSION)
        self.assertIn(b"radar00", data)
        self.assertIn(bytes((30, 20, 10, 40)), data)

    def test_txd_rejects_non_ascii_texture_names(self):
        with self.assertRaises(UnicodeEncodeError):
            make_txd("رادار", Image.new("RGBA", (1, 1)))

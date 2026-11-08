"""Pure image and RenderWare helpers, kept independent from Kivy for testing."""
import struct

from PIL import Image


DEFAULT_GRID_SIZE = 12
TOTAL_TILES = DEFAULT_GRID_SIZE * DEFAULT_GRID_SIZE

RW_VERSION = 0x1803FFFF
SECTION_STRUCT = 0x01
SECTION_EXTENSION = 0x03
SECTION_TEX_NATIVE = 0x15
SECTION_TEX_DICT = 0x16
PLATFORM_D3D9 = 9
RASTER_FORMAT = 0x0500
D3DFMT_A8R8G8B8 = 21
FILTER_FLAGS = 0x1106
DEPTH = 32
MIPMAP_COUNT = 1
TEX_TYPE = 4
D3D9_FLAGS = 0x01


def _rw_section(section_type, data):
    return struct.pack("<III", section_type, len(data), RW_VERSION) + data


def make_txd(texture_name, image):
    """Build one GTA SA D3D9/A8R8G8B8 RenderWare texture dictionary."""
    if not isinstance(texture_name, str) or not texture_name:
        raise ValueError("Texture name must be a non-empty string.")
    img = image.convert("RGBA")
    width, height = img.size
    if width <= 0 or height <= 0 or width > 65535 or height > 65535:
        raise ValueError("TXD dimensions must be between 1 and 65535 pixels.")

    red, green, blue, alpha = img.split()
    pixel_data = Image.merge("RGBA", (blue, green, red, alpha)).tobytes()
    name_bytes = texture_name.encode("ascii")[:31].ljust(32, b"\x00")
    alpha_bytes = b"\x00" * 32

    native = (
        struct.pack("<II", PLATFORM_D3D9, FILTER_FLAGS)
        + name_bytes + alpha_bytes
        + struct.pack(
            "<IIHHBBBBI",
            RASTER_FORMAT, D3DFMT_A8R8G8B8, width, height,
            DEPTH, MIPMAP_COUNT, TEX_TYPE, D3D9_FLAGS,
            len(pixel_data),
        )
        + pixel_data
    )
    tex_native = _rw_section(
        SECTION_TEX_NATIVE,
        _rw_section(SECTION_STRUCT, native)
        + _rw_section(SECTION_EXTENSION, b""),
    )
    return _rw_section(
        SECTION_TEX_DICT,
        _rw_section(SECTION_STRUCT, struct.pack("<HH", 1, 2))
        + tex_native
        + _rw_section(SECTION_EXTENSION, b""),
    )


def tile_position(index, tile_width, tile_height, flip_vertical=False):
    """Return the top-left output coordinate for a tile in the 12x12 grid."""
    if not isinstance(index, int) or not 0 <= index < TOTAL_TILES:
        raise ValueError("Tile index must be between 0 and 143.")
    if tile_width <= 0 or tile_height <= 0:
        raise ValueError("Tile dimensions must be positive.")
    column = index % DEFAULT_GRID_SIZE
    row = index // DEFAULT_GRID_SIZE
    if flip_vertical:
        row = DEFAULT_GRID_SIZE - 1 - row
    return column * tile_width, row * tile_height


def recolor_image(image, target_rgb, replace_rgb, tolerance):
    """Replace colors by distance while preserving each source alpha channel.

    Uses row/pixel access rather than a full-size Python list of pixel tuples,
    keeping peak auxiliary memory low on Android devices.
    """
    img = image if image.mode == "RGBA" else image.convert("RGBA")
    if len(target_rgb) != 3 or len(replace_rgb) != 3:
        raise ValueError("Target and replacement colors must be RGB triples.")
    target = tuple(int(channel) for channel in target_rgb)
    replacement = tuple(int(channel) for channel in replace_rgb)
    if any(channel < 0 or channel > 255 for channel in target + replacement):
        raise ValueError("RGB channels must be between 0 and 255.")

    tolerance = max(1.0, float(tolerance))
    tr, tg, tb = target
    rr, rg, rb = replacement
    delta_r, delta_g, delta_b = rr - tr, rg - tg, rb - tb

    source_pixels = img.load()
    result = Image.new("RGBA", img.size)
    result_pixels = result.load()
    for y in range(img.height):
        for x in range(img.width):
            red, green, blue, alpha = source_pixels[x, y]
            diff_r = red - tr
            diff_g = green - tg
            diff_b = blue - tb
            distance = (diff_r * diff_r + diff_g * diff_g + diff_b * diff_b) ** 0.5
            mask = 1.0 - distance / tolerance
            if mask <= 0.0:
                result_pixels[x, y] = (red, green, blue, alpha)
                continue
            mask = min(1.0, mask)
            new_red = max(0, min(255, int(round(red + delta_r * mask))))
            new_green = max(0, min(255, int(round(green + delta_g * mask))))
            new_blue = max(0, min(255, int(round(blue + delta_b * mask))))
            result_pixels[x, y] = (new_red, new_green, new_blue, alpha)
    return result

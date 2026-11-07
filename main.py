"""
GTA SA Radar Tool — Android Edition (KivyMD)
Complete implementation — Merge / Split / TXD / Color Studio
"""

import os
import threading
import colorsys
import tempfile

from PIL import Image

from kivy.clock import Clock, mainthread
from kivy.graphics import Color, RoundedRectangle, Rectangle
from kivy.graphics.texture import Texture
from kivy.metrics import dp
from kivy.properties import NumericProperty
from kivy.uix.scrollview import ScrollView
from kivy.uix.widget import Widget
from kivy.uix.image import Image as KivyImage
from kivy.uix.screenmanager import Screen, ScreenManager

from kivymd.app import MDApp
from kivymd.uix.boxlayout import MDBoxLayout
from kivymd.uix.card import MDCard
from kivymd.uix.label import MDLabel
from kivymd.uix.button import MDButton, MDButtonText
from kivymd.uix.textfield import MDTextField
from kivymd.uix.slider import MDSlider
from kivymd.uix.selectioncontrol import MDSwitch
from kivymd.uix.progressindicator import MDLinearProgressIndicator
from kivymd.uix.dialog import (
    MDDialog, MDDialogHeadlineText, MDDialogSupportingText,
    MDDialogContentContainer, MDDialogButtonContainer,
)

from plyer import filechooser
from android_storage import AndroidStorage
from operation_history import OperationHistory
from file_transactions import atomic_write, commit_staged_files
from image_algorithms import make_txd, tile_position, recolor_image

try:
    from android.permissions import request_permissions, Permission
    ANDROID = True
except ImportError:
    ANDROID = False


# ============================================================================
#  CONSTANTS
# ============================================================================
DEFAULT_TILE_SIZE = 256
GRID_SIZE = 12
TOTAL_TILES = GRID_SIZE * GRID_SIZE
MAX_PROCESS_PIXELS = 12_000_000

BG_DEEP       = "#07080d"
BG_SIDEBAR    = "#0b0c12"
BG_MAIN       = "#0e0f16"
BG_CARD       = "#171922"
BG_INPUT      = "#1c1e2a"
BORDER_STRONG = "#2d3140"
FG            = "#e8eaf5"
FG_DIM        = "#9498b0"
FG_MUTED      = "#5f6380"
PRIMARY       = "#8b5cf6"
PRIMARY_HOVER = "#a78bfa"
GREEN         = "#10b981"
RED           = "#ef4444"
YELLOW        = "#f59e0b"
WHITE         = "#ffffff"

# RenderWare / TXD constants (identical to desktop v8)
RW_VERSION         = 0x1803FFFF
SECTION_STRUCT     = 0x01
SECTION_EXTENSION  = 0x03
SECTION_TEX_NATIVE = 0x15
SECTION_TEX_DICT   = 0x16
PLATFORM_D3D9      = 9
RASTER_FORMAT      = 0x0500
D3DFMT_A8R8G8B8    = 21
FILTER_FLAGS       = 0x1106
DEPTH              = 32
MIPMAP_COUNT       = 1
TEX_TYPE           = 4
D3D9_FLAGS         = 0x01


# ============================================================================
#  HELPERS
# ============================================================================
def hex_rgba(h, a=1.0):
    return (int(h[1:3], 16) / 255.0,
            int(h[3:5], 16) / 255.0,
            int(h[5:7], 16) / 255.0, a)


def hex_rgb(rgb):
    return "#{:02x}{:02x}{:02x}".format(*rgb)


def truncate_path(p, maxlen=48):
    return p if len(p) <= maxlen else "..." + p[-(maxlen - 3):]


def pil_to_texture(pil_img):
    if pil_img.mode != "RGBA":
        pil_img = pil_img.convert("RGBA")
    data = pil_img.tobytes()
    tex = Texture.create(size=pil_img.size, colorfmt="rgba")
    tex.blit_buffer(data, colorfmt="rgba", bufferfmt="ubyte")
    tex.flip_vertical()
    return tex


def _safe_save_image(image, destination, image_format="PNG"):
    """Save to a sibling temporary file so failure preserves the old output."""
    atomic_write(
        destination,
        lambda temporary_path: image.save(temporary_path, format=image_format),
    )


def _picker_error(app_ref, title, exc):
    message = str(exc).strip() or "The file picker could not be opened."
    if app_ref is not None:
        app_ref.set_status("File access failed", RED)
        app_ref.show_dialog(title, message[:1200])


def pick_file(on_pick, filters=None, app_ref=None):
    if ANDROID:
        if app_ref is None or app_ref.storage is None:
            _picker_error(app_ref, "Android file access unavailable",
                          "The Android Storage Access Framework could not be initialized.")
            return
        app_ref.storage.pick_file(on_pick, mime_type="image/*")
        return

    def cb(selection):
        if selection:
            on_pick(selection[0])

    try:
        filechooser.open_file(on_selection=cb, filters=filters or [])
    except Exception as exc:
        _picker_error(app_ref, "File picker error", exc)


def pick_dir(on_pick, app_ref=None, output=False):
    if ANDROID:
        if app_ref is None or app_ref.storage is None:
            _picker_error(app_ref, "Android folder access unavailable",
                          "The Android Storage Access Framework could not be initialized.")
            return
        app_ref.storage.pick_directory(output=output, callback=on_pick)
        return

    def cb(selection):
        if selection:
            on_pick(selection[0])

    try:
        filechooser.choose_dir(on_selection=cb)
    except Exception as exc:
        _picker_error(app_ref, "Folder picker error", exc)


def pick_save_file(on_pick, app_ref=None, default_name="recolored.png"):
    if ANDROID:
        if app_ref is None or app_ref.storage is None:
            _picker_error(app_ref, "Android save unavailable",
                          "The Android Storage Access Framework could not be initialized.")
            return
        app_ref.storage.pick_save_file(on_pick, default_name=default_name)
        return

    def cb(selection):
        if selection:
            on_pick(selection[0])

    try:
        filechooser.save_file(on_selection=cb, filters=["*.png"])
    except Exception as exc:
        _picker_error(app_ref, "Save picker error", exc)


# Image, tile and TXD algorithms are imported from image_algorithms.py.


# ============================================================================
#  CUSTOM WIDGETS
# ============================================================================
class ColorSwatch(Widget):
    def __init__(self, color_hex=WHITE, on_click=None, **kw):
        kw.setdefault("size_hint", (None, None))
        kw.setdefault("size", (dp(56), dp(56)))
        super().__init__(**kw)
        self._color_hex = color_hex
        self._on_click = on_click
        self.bind(pos=self._redraw, size=self._redraw)
        self._redraw()

    def _redraw(self, *a):
        self.canvas.clear()
        with self.canvas:
            Color(*hex_rgba(self._color_hex))
            RoundedRectangle(pos=self.pos, size=self.size, radius=[dp(10)])
            Color(*hex_rgba(BORDER_STRONG))
            from kivy.graphics import Line
            Line(rounded_rectangle=(self.x, self.y, self.width,
                                    self.height, dp(10)), width=1.2)

    def set_color(self, hex_color):
        self._color_hex = hex_color
        self._redraw()

    def on_touch_down(self, touch):
        if self.collide_point(*touch.pos) and self._on_click:
            self._on_click()
            return True
        return super().on_touch_down(touch)


class HueBar(Widget):
    hue = NumericProperty(0.0)

    def __init__(self, on_change=None, **kw):
        super().__init__(**kw)
        self._on_change = on_change
        self._tex = self._build_gradient()
        self.bind(pos=self._redraw, size=self._redraw)
        self._redraw()

    def _build_gradient(self):
        w = 256
        img = Image.new("RGB", (w, 1))
        for x in range(w):
            r, g, b = (round(c * 255)
                       for c in colorsys.hsv_to_rgb(x / (w - 1), 1, 1))
            img.putpixel((x, 0), (r, g, b))
        return pil_to_texture(img)

    def _redraw(self, *a):
        self.canvas.clear()
        with self.canvas:
            Color(1, 1, 1, 1)
            Rectangle(texture=self._tex, pos=self.pos, size=self.size)

    def on_touch_down(self, touch):
        if self.collide_point(*touch.pos):
            touch.grab(self)
            self._pick(touch.x)
            return True
        return super().on_touch_down(touch)

    def on_touch_move(self, touch):
        if touch.grab_current is self:
            self._pick(touch.x)
            return True
        return super().on_touch_move(touch)

    def on_touch_up(self, touch):
        if touch.grab_current is self:
            touch.ungrab(self)
            return True
        return super().on_touch_up(touch)

    def _pick(self, x):
        w = max(1, self.width)
        h = max(0.0, min(1.0, (x - self.x) / w))
        self.hue = h
        if self._on_change:
            r, g, b = (round(c * 255) for c in colorsys.hsv_to_rgb(h, 1, 1))
            self._on_change(r, g, b)


# ============================================================================
#  BASE TOOL ITEM
# ============================================================================
class BaseToolItem(Screen):
    def __init__(self, app_ref, text="", icon="", **kw):
        super().__init__(**kw)
        self.app_ref = app_ref
        self.tab_text = text
        self.tab_icon = icon
        self.action_button = None
        self._operation_running = False
        self._history_context = None

    def _begin_operation(self, title, input_path="", output_path=""):
        if self._operation_running:
            self.app_ref.show_dialog(
                "Operation already running",
                "Please let the current operation finish before starting another.",
            )
            return False
        self._operation_running = True
        self._history_context = {
            "title": title,
            "input_path": input_path or "",
            "output_path": output_path or "",
        }
        if self.action_button is not None:
            self.action_button.disabled = True
        return True

    @mainthread
    def _finish_operation(self, succeeded, detail=""):
        context = self._history_context or {}
        storage = getattr(self.app_ref, "storage", None)
        input_path = context.get("input_path", "")
        output_path = context.get("output_path", "")
        if storage is not None:
            try:
                input_path = storage.describe_path(input_path)
            except Exception:
                input_path = str(input_path or "")
            try:
                output_path = storage.describe_path(output_path)
            except Exception:
                output_path = str(output_path or "")
        try:
            self.app_ref.history.add(
                context.get("title", "Operation"),
                input_path=input_path,
                output_path=output_path,
                status="success" if succeeded else "failed",
                detail=detail,
            )
            if getattr(self.app_ref, "history_tab", None) is not None:
                self.app_ref.history_tab.refresh()
        except Exception:
            # A broken history file must never interrupt the actual operation.
            pass
        self._history_context = None
        self._operation_running = False
        if self.action_button is not None:
            self.action_button.disabled = False

    def _label(self, text, color=FG, size=14, bold=False, height=None):
        return MDLabel(
            text=text, font_size=dp(size), bold=bold,
            theme_text_color="Custom", text_color=hex_rgba(color),
            size_hint_y=None, height=height or dp(size + 12),
        )

    def _card(self):
        c = MDCard(
            orientation="vertical",
            padding=dp(16), spacing=dp(10),
            radius=[dp(14)],
            md_bg_color=hex_rgba(BG_CARD),
            size_hint_y=None,
        )
        c.bind(minimum_height=c.setter("height"))
        return c

    def _button(self, text, on_press, bg=PRIMARY, fg=WHITE, height=48):
        btn = MDButton(
            MDButtonText(text=text, theme_text_color="Custom",
                         text_color=hex_rgba(fg)),
            style="filled", md_bg_color=hex_rgba(bg),
            size_hint_y=None, height=dp(height),
        )
        btn.bind(on_release=lambda x: on_press())
        return btn

    def _folder_card(self, title, hint, on_pick):
        card = self._card()
        card.add_widget(self._label(title, color=FG, size=14,
                                    bold=True, height=dp(24)))
        path_lbl = self._label(hint, color=FG_MUTED, size=11, height=dp(20))
        card.add_widget(path_lbl)
        btn = MDButton(MDButtonText(text="Browse"), style="tonal",
                       size_hint_y=None, height=dp(42))
        btn.bind(on_release=lambda x: on_pick())
        card.add_widget(btn)
        card.path_label = path_lbl
        return card

    def _header(self, title, subtitle):
        h = MDBoxLayout(orientation="vertical", size_hint_y=None,
                        height=dp(86),
                        padding=(dp(16), dp(12), dp(16), dp(8)),
                        spacing=dp(2))
        h.add_widget(self._label(title, color=FG, size=22,
                                 bold=True, height=dp(36)))
        h.add_widget(self._label(subtitle, color=FG_DIM, size=12,
                                 height=dp(22)))
        return h

    def _make_content_root(self):
        content = MDBoxLayout(orientation="vertical")
        with content.canvas.before:
            Color(*hex_rgba(BG_MAIN))
            bg = Rectangle(pos=content.pos, size=content.size)
        content.bind(pos=lambda *a: setattr(bg, "pos", content.pos),
                     size=lambda *a: setattr(bg, "size", content.size))
        return content


# ============================================================================
# OPERATION HISTORY
# ============================================================================
class HistoryItem(BaseToolItem):
    def __init__(self, app_ref, **kw):
        super().__init__(app_ref, name="history", text="History",
                         icon="history", **kw)
        self._build()

    def _build(self):
        content = self._make_content_root()
        content.add_widget(self._header(
            "Operation History",
            "A local record of completed and failed tasks",
        ))
        refresh_row = MDBoxLayout(
            orientation="horizontal",
            size_hint_y=None,
            height=dp(48),
            padding=(dp(16), 0, dp(16), 0),
        )
        refresh_row.add_widget(Widget())
        refresh = self._button("Refresh", self.refresh, height=40)
        refresh.size_hint_x = None
        refresh.width = dp(110)
        refresh_row.add_widget(refresh)
        content.add_widget(refresh_row)

        self._history_body = MDBoxLayout(
            orientation="vertical",
            size_hint_y=None,
            spacing=dp(10),
            padding=dp(16),
        )
        self._history_body.bind(
            minimum_height=self._history_body.setter("height")
        )
        scroll = ScrollView()
        scroll.add_widget(self._history_body)
        content.add_widget(scroll)
        self.add_widget(content)
        self.refresh()

    def refresh(self):
        self._history_body.clear_widgets()
        entries = self.app_ref.history.list_entries()
        if not entries:
            self._history_body.add_widget(self._label(
                "No operations recorded yet.",
                color=FG_MUTED, size=13, height=dp(38),
            ))
            return

        for entry in entries:
            succeeded = entry.get("status") == "success"
            color = GREEN if succeeded else RED
            card = self._card()
            card.add_widget(self._label(
                entry.get("title", "Operation"),
                color=color, size=14, bold=True, height=dp(25),
            ))
            card.add_widget(self._label(
                entry.get("timestamp", "").replace("T", " "),
                color=FG_MUTED, size=10, height=dp(17),
            ))
            input_path = entry.get("input_path") or "Not recorded"
            output_path = entry.get("output_path") or "Not recorded"
            card.add_widget(self._label(
                "INPUT  " + truncate_path(input_path, 96),
                color=FG_DIM, size=10, height=dp(28),
            ))
            card.add_widget(self._label(
                "OUTPUT " + truncate_path(output_path, 96),
                color=FG_DIM, size=10, height=dp(28),
            ))
            detail = entry.get("detail", "")
            if detail:
                card.add_widget(self._label(
                    truncate_path(detail, 120),
                    color=FG_MUTED, size=10, height=dp(26),
                ))
            self._history_body.add_widget(card)


# ============================================================================
#  MERGE TAB
# ============================================================================
class MergeItem(BaseToolItem):
    def __init__(self, app_ref, **kw):
        super().__init__(app_ref, name="merge", text="Merge",
                         icon="map-marker-multiple", **kw)
        self.input_folder = ""
        self.output_folder = ""
        self._build()

    def _build(self):
        content = self._make_content_root()
        content.add_widget(self._header("Merge Tiles",
                                        "Combine 144 radar tiles into map.png"))

        scroll = ScrollView()
        body = MDBoxLayout(orientation="vertical", size_hint_y=None,
                           spacing=dp(12), padding=dp(16))
        body.bind(minimum_height=body.setter("height"))

        c_in = self._folder_card("Input Folder",
                                 "Folder containing radar*.png",
                                 self._pick_input)
        self.input_label = c_in.path_label
        body.add_widget(c_in)

        c_out = self._folder_card("Output Folder",
                                  "Where map.png will be saved",
                                  self._pick_output)
        self.output_label = c_out.path_label
        body.add_widget(c_out)

        settings = self._card()
        settings.add_widget(self._label("TILE SIZE", color=FG_DIM,
                                        size=11, bold=True, height=dp(18)))
        row = MDBoxLayout(size_hint_y=None, height=dp(56), spacing=dp(8))
        self.tile_w = MDTextField(text=str(DEFAULT_TILE_SIZE),
                                  input_filter="int", size_hint_x=0.5,
                                  halign="center", size_hint_y=None,
                                  height=dp(56))
        self.tile_h = MDTextField(text=str(DEFAULT_TILE_SIZE),
                                  input_filter="int", size_hint_x=0.5,
                                  halign="center", size_hint_y=None,
                                  height=dp(56))
        row.add_widget(self.tile_w)
        row.add_widget(self.tile_h)
        settings.add_widget(row)

        flip_row = MDBoxLayout(size_hint_y=None, height=dp(44), spacing=dp(8))
        self.flip = MDSwitch()
        flip_row.add_widget(self.flip)
        flip_row.add_widget(self._label("Flip vertical (bottom-up tiles)",
                                        color=FG, size=13, height=dp(44)))
        settings.add_widget(flip_row)
        body.add_widget(settings)

        self.action_button = self._button(
            "Merge Tiles", self._do_merge, bg=GREEN, fg=BG_DEEP
        )
        body.add_widget(self.action_button)

        self.progress = MDLinearProgressIndicator(size_hint_y=None, height=dp(4), value=0, type="determinate")
        body.add_widget(self.progress)

        scroll.add_widget(body)
        content.add_widget(scroll)
        self.add_widget(content)

    def _pick_input(self):
        pick_dir(self._on_input, app_ref=self.app_ref, output=False)

    def _pick_output(self):
        pick_dir(self._on_output, app_ref=self.app_ref, output=True)

    def _on_input(self, path):
        self.input_folder = path
        self.input_label.text = truncate_path(path)
        self.input_label.text_color = hex_rgba(PRIMARY)

    def _on_output(self, path):
        self.output_folder = path
        self.output_label.text = truncate_path(path)
        self.output_label.text_color = hex_rgba(PRIMARY)

    def _do_merge(self):
        if not self.input_folder:
            return self.app_ref.show_dialog("Error",
                                            "Select an input folder.")
        if not self.output_folder:
            return self.app_ref.show_dialog("Error",
                                            "Select an output folder.")
        try:
            tw = int(self.tile_w.text)
            th = int(self.tile_h.text)
            if tw <= 0 or th <= 0:
                raise ValueError
            if tw * GRID_SIZE * th * GRID_SIZE > MAX_PROCESS_PIXELS:
                raise ValueError("The merged map would exceed the safe 12-megapixel processing limit.")
        except ValueError as exc:
            return self.app_ref.show_dialog(
                "Error", str(exc) or "Tile size must be positive integers.")
        if not self._begin_operation(
            "Merged 144 radar tiles", self.input_folder, self.output_folder
        ):
            return
        self.app_ref.set_status("Merging tiles...", YELLOW)
        self.progress.value = 0
        try:
            threading.Thread(
                target=self._worker,
                args=(self.input_folder, self.output_folder,
                      tw, th, self.flip.active),
                daemon=True,
            ).start()
        except Exception as exc:
            self._fail(str(exc))

    def _worker(self, in_dir, out_dir, tw, th, flip):
        try:
            missing = [
                f"radar{i:02}.png"
                for i in range(TOTAL_TILES)
                if not os.path.isfile(os.path.join(in_dir, f"radar{i:02}.png"))
            ]
            if missing:
                shown = ", ".join(missing[:24])
                suffix = f" ... and {len(missing) - 24} more" if len(missing) > 24 else ""
                self._fail(
                    f"Merge cancelled: {len(missing)} of {TOTAL_TILES} required tiles are missing. "
                    f"Missing: {shown}{suffix}"
                )
                return

            canvas = Image.new("RGBA", (tw * GRID_SIZE, th * GRID_SIZE))
            found = 0
            for i in range(TOTAL_TILES):
                p = os.path.join(in_dir, f"radar{i:02}.png")
                with Image.open(p) as source:
                    if source.width * source.height > MAX_PROCESS_PIXELS:
                        raise ValueError(f"{os.path.basename(p)} exceeds the safe image size limit.")
                    img = source.convert("RGBA")
                if img.size != (tw, th):
                    img = img.resize((tw, th), Image.LANCZOS)
                x, y = tile_position(i, tw, th, flip)
                canvas.paste(img, (x, y))
                found += 1
                if i % 12 == 0:
                    self._update_progress(found / TOTAL_TILES)
            _safe_save_image(canvas, os.path.join(out_dir, "map.png"))
            if self.app_ref.storage is not None:
                self.app_ref.storage.export_directory_if_needed(out_dir)
            self._done(found, tw, th)
        except Exception as e:
            self._fail(str(e))

    @mainthread
    def _update_progress(self, v):
        self.progress.value = v * 100

    @mainthread
    def _done(self, found, tw, th):
        self.progress.value = 100
        self.app_ref.set_status(f"Merged {found} tiles -> map.png", GREEN)
        self._finish_operation(
            True, f"{found} tiles; output size {tw * GRID_SIZE}x{th * GRID_SIZE}"
        )
        self.app_ref.show_dialog(
            "Done",
            f"map.png saved ({tw * GRID_SIZE}x{th * GRID_SIZE}) "
            f"from {found} tiles."
        )

    @mainthread
    def _fail(self, msg):
        self.progress.value = 0
        self.app_ref.set_status("Merge failed", RED)
        self._finish_operation(False, msg)
        self.app_ref.show_dialog("Error", msg)


# ============================================================================
#  SPLIT TAB
# ============================================================================
class SplitItem(BaseToolItem):
    def __init__(self, app_ref, **kw):
        super().__init__(app_ref, name="split", text="Split",
                         icon="scissors-cutting", **kw)
        self.image_path = ""
        self.output_folder = ""
        self._build()

    def _build(self):
        content = self._make_content_root()
        content.add_widget(self._header("Split Map",
                                        "Slice map.png into 144 radar tiles"))

        scroll = ScrollView()
        body = MDBoxLayout(orientation="vertical", size_hint_y=None,
                           spacing=dp(12), padding=dp(16))
        body.bind(minimum_height=body.setter("height"))

        c_img = self._folder_card("Map Image", "Full map.png",
                                  self._pick_image)
        self.image_label = c_img.path_label
        body.add_widget(c_img)

        c_out = self._folder_card("Output Folder",
                                  "radar00.png ... radar143.png",
                                  self._pick_output)
        self.output_label = c_out.path_label
        body.add_widget(c_out)

        settings = self._card()
        settings.add_widget(self._label("TILE SIZE", color=FG_DIM,
                                        size=11, bold=True, height=dp(18)))
        row = MDBoxLayout(size_hint_y=None, height=dp(56), spacing=dp(8))
        self.tile_w = MDTextField(text=str(DEFAULT_TILE_SIZE),
                                  input_filter="int", size_hint_x=0.5,
                                  halign="center", size_hint_y=None,
                                  height=dp(56))
        self.tile_h = MDTextField(text=str(DEFAULT_TILE_SIZE),
                                  input_filter="int", size_hint_x=0.5,
                                  halign="center", size_hint_y=None,
                                  height=dp(56))
        row.add_widget(self.tile_w)
        row.add_widget(self.tile_h)
        settings.add_widget(row)

        flip_row = MDBoxLayout(size_hint_y=None, height=dp(44), spacing=dp(8))
        self.flip = MDSwitch()
        flip_row.add_widget(self.flip)
        flip_row.add_widget(self._label("Flip vertical",
                                        color=FG, size=13, height=dp(44)))
        settings.add_widget(flip_row)
        body.add_widget(settings)

        self.action_button = self._button("Split Map", self._do_split)
        body.add_widget(self.action_button)
        self.progress = MDLinearProgressIndicator(size_hint_y=None, height=dp(4), value=0, type="determinate")
        body.add_widget(self.progress)

        scroll.add_widget(body)
        content.add_widget(scroll)
        self.add_widget(content)

    def _pick_image(self):
        pick_file(self._on_image, filters=["*.png", "*.jpg", "*.jpeg"],
                  app_ref=self.app_ref)

    def _pick_output(self):
        pick_dir(self._on_output, app_ref=self.app_ref, output=True)

    def _on_image(self, path):
        self.image_path = path
        self.image_label.text = truncate_path(path)
        self.image_label.text_color = hex_rgba(PRIMARY)

    def _on_output(self, path):
        self.output_folder = path
        self.output_label.text = truncate_path(path)
        self.output_label.text_color = hex_rgba(PRIMARY)

    def _do_split(self):
        if not self.image_path:
            return self.app_ref.show_dialog("Error", "Pick a map image.")
        if not self.output_folder:
            return self.app_ref.show_dialog("Error", "Pick an output folder.")
        try:
            tw = int(self.tile_w.text)
            th = int(self.tile_h.text)
            if tw <= 0 or th <= 0:
                raise ValueError
            if tw * GRID_SIZE * th * GRID_SIZE > MAX_PROCESS_PIXELS:
                raise ValueError("The split map would exceed the safe 12-megapixel processing limit.")
        except ValueError as exc:
            return self.app_ref.show_dialog(
                "Error", str(exc) or "Tile size must be positive integers.")
        if not self._begin_operation(
            "Split map into 144 tiles", self.image_path, self.output_folder
        ):
            return
        self.app_ref.set_status("Splitting map...", YELLOW)
        self.progress.value = 0
        try:
            threading.Thread(
                target=self._worker,
                args=(self.image_path, self.output_folder,
                      tw, th, self.flip.active),
                daemon=True,
            ).start()
        except Exception as exc:
            self._fail(str(exc))

    def _worker(self, img_path, out_dir, tw, th, flip):
        try:
            with Image.open(img_path) as source:
                if source.width * source.height > MAX_PROCESS_PIXELS:
                    raise ValueError("The selected map image exceeds the safe 12-megapixel processing limit.")
                img = source.convert("RGBA")
            target = (tw * GRID_SIZE, th * GRID_SIZE)
            if img.size != target:
                img = img.resize(target, Image.LANCZOS)
            if not os.path.isdir(out_dir):
                raise FileNotFoundError("The selected output folder is unavailable.")
            filenames = [f"radar{i:02}.png" for i in range(TOTAL_TILES)]
            with tempfile.TemporaryDirectory(
                prefix=".radartool-split-", dir=out_dir
            ) as staging_dir:
                for i, filename in enumerate(filenames):
                    x, y = tile_position(i, tw, th, flip)
                    tile = img.crop((x, y, x + tw, y + th))
                    tile.save(os.path.join(staging_dir, filename), format="PNG")
                    if i % 12 == 0:
                        self._update_progress((i + 1) / TOTAL_TILES)
                commit_staged_files(staging_dir, out_dir, filenames)
            if self.app_ref.storage is not None:
                self.app_ref.storage.export_directory_if_needed(out_dir)
            self._done(tw, th)
        except Exception as e:
            self._fail(str(e))

    @mainthread
    def _update_progress(self, v):
        self.progress.value = v * 100

    @mainthread
    def _done(self, tw, th):
        self.progress.value = 100
        self.app_ref.set_status("Exported 144 tiles", GREEN)
        self._finish_operation(True, f"144 output tiles at {tw}x{th}")
        self.app_ref.show_dialog(
            "Done",
            f"All {TOTAL_TILES} tiles exported at {tw}x{th}.")

    @mainthread
    def _fail(self, msg):
        self.progress.value = 0
        self.app_ref.set_status("Split failed", RED)
        self._finish_operation(False, msg)
        self.app_ref.show_dialog("Error", msg)


# ============================================================================
#  TXD CONVERTER TAB
# ============================================================================
class TxDItem(BaseToolItem):
    def __init__(self, app_ref, **kw):
        super().__init__(app_ref, name="txd", text="TXD",
                         icon="package-variant-closed", **kw)
        self.input_folder = ""
        self.output_folder = ""
        self._build()

    def _build(self):
        content = self._make_content_root()
        content.add_widget(self._header("PNG -> TXD",
                                        "Convert radar PNGs to RenderWare TXD"))

        scroll = ScrollView()
        body = MDBoxLayout(orientation="vertical", size_hint_y=None,
                           spacing=dp(12), padding=dp(16))
        body.bind(minimum_height=body.setter("height"))

        c_in = self._folder_card("PNG Folder", "radar*.png files",
                                 self._pick_input)
        self.input_label = c_in.path_label
        body.add_widget(c_in)

        c_out = self._folder_card("TXD Folder", ".txd output",
                                  self._pick_output)
        self.output_label = c_out.path_label
        body.add_widget(c_out)

        info = self._card()
        info.add_widget(self._label("HOW IT WORKS", color=PRIMARY,
                                    size=11, bold=True, height=dp(18)))
        info.add_widget(self._label(
            "All radar*.png files are converted to .txd with the "
            "same base name.\nCompatible with GTA SA (D3D9 / A8R8G8B8).",
            color=FG_DIM, size=11, height=dp(56)))
        body.add_widget(info)

        self.action_button = self._button("Convert to TXD", self._do_convert)
        body.add_widget(self.action_button)
        self.progress = MDLinearProgressIndicator(size_hint_y=None, height=dp(4), value=0, type="determinate")
        body.add_widget(self.progress)

        scroll.add_widget(body)
        content.add_widget(scroll)
        self.add_widget(content)

    def _pick_input(self):
        pick_dir(self._on_input, app_ref=self.app_ref, output=False)

    def _pick_output(self):
        pick_dir(self._on_output, app_ref=self.app_ref, output=True)

    def _on_input(self, path):
        self.input_folder = path
        self.input_label.text = truncate_path(path)
        self.input_label.text_color = hex_rgba(PRIMARY)

    def _on_output(self, path):
        self.output_folder = path
        self.output_label.text = truncate_path(path)
        self.output_label.text_color = hex_rgba(PRIMARY)

    def _do_convert(self):
        if not self.input_folder:
            return self.app_ref.show_dialog("Error", "Select PNG folder.")
        if not self.output_folder:
            return self.app_ref.show_dialog("Error", "Select TXD folder.")
        if not self._begin_operation(
            "Converted PNG tiles to TXD", self.input_folder, self.output_folder
        ):
            return
        self.app_ref.set_status("Converting...", YELLOW)
        self.progress.value = 0
        try:
            threading.Thread(
                target=self._worker,
                args=(self.input_folder, self.output_folder),
                daemon=True,
            ).start()
        except Exception as exc:
            self._fail(str(exc))

    def _worker(self, input_folder, output_folder):
        try:
            if not os.path.isdir(output_folder):
                raise FileNotFoundError("The selected TXD output folder is unavailable.")
            files = sorted([
                f for f in os.listdir(input_folder)
                if f.lower().startswith("radar")
                and f.lower().endswith(".png")
            ])
            if not files:
                self._fail("No radar*.png files found in folder.")
                return
            ok, errors, generated = 0, [], []
            with tempfile.TemporaryDirectory(
                prefix=".radartool-txd-", dir=output_folder
            ) as staging_dir:
                for idx, filename in enumerate(files):
                    try:
                        name = os.path.splitext(filename)[0]
                        with Image.open(os.path.join(input_folder, filename)) as source:
                            if source.width * source.height > MAX_PROCESS_PIXELS:
                                raise ValueError("Image exceeds the safe 12-megapixel processing limit.")
                            data = make_txd(name, source)
                        output_name = name + ".txd"
                        if output_name in generated:
                            raise ValueError(
                                "Duplicate output name from multiple PNG files: "
                                + output_name
                            )
                        with open(os.path.join(staging_dir, output_name), "wb") as target:
                            target.write(data)
                        generated.append(output_name)
                        ok += 1
                    except Exception as exc:
                        errors.append(f"{filename}: {exc}")
                    self._update_progress((idx + 1) / len(files))
                if generated:
                    commit_staged_files(staging_dir, output_folder, generated)
            if self.app_ref.storage is not None and generated:
                self.app_ref.storage.export_directory_if_needed(output_folder)
            self._done(ok, len(errors), errors)
        except Exception as e:
            self._fail(str(e))

    @mainthread
    def _update_progress(self, v):
        self.progress.value = v * 100

    @mainthread
    def _done(self, ok, err_count, errors):
        self.progress.value = 100
        if err_count:
            self.app_ref.set_status(
                f"{ok} converted, {err_count} failed", RED)
            self.app_ref.show_dialog(
                "Completed with errors",
                f"Converted {ok} file(s).\n\n" + "\n".join(errors[:5]))
        else:
            self.app_ref.set_status(
                f"Converted {ok} files -> TXD", GREEN)
            self.app_ref.show_dialog(
                "Done",
                f"Successfully converted {ok} PNG file(s) to TXD!")
        self._finish_operation(
            err_count == 0,
            f"{ok} converted; {err_count} failed. " + "; ".join(errors[:5]),
        )

    @mainthread
    def _fail(self, msg):
        self.progress.value = 0
        self.app_ref.set_status("Convert failed", RED)
        self._finish_operation(False, msg)
        self.app_ref.show_dialog("Error", msg)


# ============================================================================
#  COLOR STUDIO TAB  (complete)
# ============================================================================
class ColorItem(BaseToolItem):
    def __init__(self, app_ref, **kw):
        super().__init__(app_ref, name="color", text="Color",
                         icon="palette", **kw)
        self.image_path = ""
        self.pil_image = None
        self.preview_src = None
        self.target_rgb = (128, 128, 128)
        self.replace_rgb = (255, 0, 0)
        self.tolerance = 40
        self._preview_event = None
        self._image_load_token = 0
        self._build()

    def _build(self):
        content = self._make_content_root()
        content.add_widget(self._header("Color Studio",
                                        "Live recolor with real-time preview"))

        scroll = ScrollView()
        body = MDBoxLayout(orientation="vertical", size_hint_y=None,
                           spacing=dp(12), padding=dp(16))
        body.bind(minimum_height=body.setter("height"))

        # --- image picker ---
        img_card = self._card()
        img_card.add_widget(self._label("Map Image", color=FG, size=14,
                                        bold=True, height=dp(24)))
        self._img_path_label = self._label("No image loaded",
                                           color=FG_MUTED, size=11,
                                           height=dp(20))
        img_card.add_widget(self._img_path_label)
        b = MDButton(MDButtonText(text="Browse Image"), style="tonal",
                     size_hint_y=None, height=dp(42))
        b.bind(on_release=lambda x: self._pick_image())
        img_card.add_widget(b)
        body.add_widget(img_card)

        # --- original (tap to pick target color) ---
        orig_card = self._card()
        orig_card.add_widget(self._label("TAP TO PICK TARGET COLOR",
                                         color=FG_DIM, size=10, bold=True,
                                         height=dp(18)))
        self._orig_image = KivyImage(size_hint_y=None, height=dp(200),
                                     allow_stretch=True, keep_ratio=True)
        self._orig_image.bind(on_touch_down=self._tap_original)
        orig_card.add_widget(self._orig_image)
        body.add_widget(orig_card)

        # --- live preview ---
        live_card = self._card()
        live_card.add_widget(self._label("LIVE PREVIEW", color=RED,
                                         size=10, bold=True, height=dp(18)))
        self._live_image = KivyImage(size_hint_y=None, height=dp(200),
                                     allow_stretch=True, keep_ratio=True)
        live_card.add_widget(self._live_image)
        body.add_widget(live_card)

        # --- swatches ---
        swatch_card = self._card()
        srow = MDBoxLayout(size_hint_y=None, height=dp(110),
                           spacing=dp(12))
        tcol = MDBoxLayout(orientation="vertical", spacing=dp(4))
        tcol.add_widget(self._label("TARGET", color=FG_DIM, size=10,
                                    bold=True, height=dp(16)))
        # Target color is sampled from the original image; this swatch is display-only.
        self._target_swatch = ColorSwatch(hex_rgb(self.target_rgb))
        tcol.add_widget(self._target_swatch)
        self._target_label = self._label(str(self.target_rgb),
                                         color=FG_MUTED, size=11,
                                         height=dp(18))
        tcol.add_widget(self._target_label)
        srow.add_widget(tcol)

        arrow = self._label("->", color=PRIMARY, size=26, bold=True,
                            height=dp(110))
        srow.add_widget(arrow)

        rcol = MDBoxLayout(orientation="vertical", spacing=dp(4))
        rcol.add_widget(self._label("REPLACE", color=FG_DIM, size=10,
                                    bold=True, height=dp(16)))
        self._replace_swatch = ColorSwatch(
            hex_rgb(self.replace_rgb), on_click=self._pick_replace_color)
        rcol.add_widget(self._replace_swatch)
        self._replace_label = self._label(str(self.replace_rgb),
                                          color=FG_MUTED, size=11,
                                          height=dp(18))
        rcol.add_widget(self._replace_label)
        srow.add_widget(rcol)
        swatch_card.add_widget(srow)
        body.add_widget(swatch_card)

        # --- hue bar ---
        hue_card = self._card()
        hue_card.add_widget(self._label("QUICK HUE", color=FG_DIM,
                                        size=10, bold=True, height=dp(16)))
        hue_bar = HueBar(size_hint_y=None, height=dp(32),
                         on_change=self._on_hue_change)
        hue_card.add_widget(hue_bar)
        body.add_widget(hue_card)

        # --- tolerance ---
        tol_card = self._card()
        tol_row = MDBoxLayout(size_hint_y=None, height=dp(28))
        tol_row.add_widget(self._label("TOLERANCE", color=FG_DIM, size=10,
                                       bold=True, height=dp(28)))
        tol_row.add_widget(Widget())
        self._tol_label = self._label("40", color=PRIMARY, size=14,
                                      bold=True, height=dp(28))
        tol_row.add_widget(self._tol_label)
        tol_card.add_widget(tol_row)
        self._tol_slider = MDSlider(min=1, max=150, value=40,
                                    size_hint_y=None, height=dp(36))
        self._tol_slider.bind(value=self._on_tol_change)
        tol_card.add_widget(self._tol_slider)
        tol_card.add_widget(self._label("Higher = replace more colors",
                                        color=FG_MUTED, size=10,
                                        height=dp(16)))
        body.add_widget(tol_card)

        # --- save ---
        self.action_button = self._button(
            "Save Result", self._save, bg=GREEN, fg=BG_DEEP
        )
        body.add_widget(self.action_button)

        scroll.add_widget(body)
        content.add_widget(scroll)
        self.add_widget(content)

    # -- image picking --------------------------------------------------
    def _pick_image(self):
        pick_file(self._on_image, filters=["*.png", "*.jpg", "*.jpeg"],
                  app_ref=self.app_ref)

    def _on_image(self, path):
        self._image_load_token += 1
        token = self._image_load_token
        self.app_ref.set_status("Loading image...", YELLOW)
        try:
            threading.Thread(
                target=self._load_image_worker,
                args=(path, token),
                daemon=True,
            ).start()
        except Exception as exc:
            self._image_load_failed(token, exc)

    def _load_image_worker(self, path, token):
        try:
            with Image.open(path) as source:
                if source.width * source.height > MAX_PROCESS_PIXELS:
                    raise ValueError("Image exceeds the safe 12-megapixel processing limit.")
                loaded_image = source.convert("RGBA")
            preview = loaded_image.copy()
            preview.thumbnail((400, 400), Image.LANCZOS)
            self._image_load_succeeded(path, token, loaded_image, preview)
        except Exception as exc:
            self._image_load_failed(token, exc)

    @mainthread
    def _image_load_succeeded(self, path, token, loaded_image, preview):
        if token != self._image_load_token:
            return
        self.image_path = path
        self.pil_image = loaded_image
        self.preview_src = preview
        self._img_path_label.text = truncate_path(path)
        self._img_path_label.text_color = hex_rgba(PRIMARY)
        self._orig_image.texture = pil_to_texture(preview)
        self._schedule_preview()
        self.app_ref.set_status(
            f"Loaded {os.path.basename(path)}", GREEN)

    @mainthread
    def _image_load_failed(self, token, exc):
        if token != self._image_load_token:
            return
        self.app_ref.set_status("Load failed", RED)
        self.app_ref.show_dialog(
            "Error", f"Could not open image:\n{exc}")

    def _tap_original(self, instance, touch):
        if self.preview_src is None:
            return False
        if not instance.collide_point(*touch.pos):
            return False
        tex = instance.texture
        if tex is None:
            return False

        tex_w, tex_h = tex.size
        widget_w, widget_h = instance.size
        if tex_w <= 0 or tex_h <= 0:
            return False

        scale = min(widget_w / tex_w, widget_h / tex_h)
        disp_w = tex_w * scale
        disp_h = tex_h * scale
        off_x = (widget_w - disp_w) / 2
        off_y = (widget_h - disp_h) / 2

        lx = touch.x - instance.x - off_x
        ly = touch.y - instance.y - off_y
        if lx < 0 or ly < 0 or lx >= disp_w or ly >= disp_h:
            return False

        tx = max(0, min(tex_w - 1, int(lx / scale)))
        ty = max(0, min(tex_h - 1, tex_h - 1 - int(ly / scale)))

        try:
            r, g, b, _ = self.preview_src.getpixel((tx, ty))
        except Exception:
            return False

        self.target_rgb = (r, g, b)
        self._target_swatch.set_color(hex_rgb(self.target_rgb))
        self._target_label.text = str(self.target_rgb)
        self._schedule_preview()
        return True

    # -- params --------------------------------------------------------
    def _pick_replace_color(self):
        """Open an in-app RGB picker for the replacement color."""
        rgb = list(self.replace_rgb)

        preview = MDCard(
            orientation="vertical",
            size_hint_y=None,
            height=dp(48),
            padding=dp(8),
            radius=[dp(12)],
            md_bg_color=hex_rgba(hex_rgb(rgb)),
        )
        preview_hex = MDLabel(
            text=hex_rgb(rgb).upper(),
            halign="center",
            valign="middle",
            theme_text_color="Custom",
            text_color=hex_rgba(WHITE),
            size_hint_y=None,
            height=dp(30),
        )
        preview.add_widget(preview_hex)

        content = MDBoxLayout(
            orientation="vertical",
            spacing=dp(10),
            padding=(dp(12), dp(8), dp(12), dp(8)),
            size_hint_y=None,
            height=dp(230),
        )
        content.add_widget(preview)

        channel_values = []
        for index, channel_name in enumerate(("R", "G", "B")):
            row = MDBoxLayout(
                orientation="horizontal",
                spacing=dp(8),
                size_hint_y=None,
                height=dp(42),
            )
            channel_label = MDLabel(
                text=channel_name,
                size_hint_x=None,
                width=dp(22),
                theme_text_color="Custom",
                text_color=hex_rgba(FG),
            )
            value_label = MDLabel(
                text=str(rgb[index]),
                size_hint_x=None,
                width=dp(34),
                halign="right",
                theme_text_color="Custom",
                text_color=hex_rgba(FG_DIM),
            )
            slider = MDSlider(
                min=0,
                max=255,
                value=rgb[index],
                step=1,
                size_hint_y=None,
                height=dp(38),
            )
            channel_values.append((slider, value_label))
            row.add_widget(channel_label)
            row.add_widget(slider)
            row.add_widget(value_label)
            content.add_widget(row)

        def update_preview(*_args):
            for channel, (slider, value_label) in enumerate(channel_values):
                rgb[channel] = max(0, min(255, int(round(slider.value))))
                value_label.text = str(rgb[channel])
            color = hex_rgb(tuple(rgb))
            preview.md_bg_color = hex_rgba(color)
            preview_hex.text = color.upper()
            # Keep the preview text readable against both dark and light colors.
            brightness = (rgb[0] * 299 + rgb[1] * 587 + rgb[2] * 114) / 1000
            preview_hex.text_color = hex_rgba(BG_DEEP if brightness > 150 else WHITE)

        for slider, _value_label in channel_values:
            slider.bind(value=update_preview)

        dialog = MDDialog(
            MDDialogHeadlineText(text="Replacement RGB color"),
            MDDialogSupportingText(
                text="Adjust the red, green and blue channels."),
            MDDialogContentContainer(
                content,
                orientation="vertical",
            ),
            MDDialogButtonContainer(
                Widget(),
                MDButton(
                    MDButtonText(text="Cancel"),
                    style="text",
                    on_release=lambda *_: dialog.dismiss(),
                ),
                MDButton(
                    MDButtonText(text="Apply"),
                    style="filled",
                    md_bg_color=hex_rgba(PRIMARY),
                    on_release=lambda *_: self._apply_replace_rgb(dialog, rgb),
                ),
                spacing=dp(8),
            ),
        )
        dialog.open()

    def _apply_replace_rgb(self, dialog, rgb):
        dialog.dismiss()
        self.replace_rgb = tuple(max(0, min(255, int(v))) for v in rgb)
        self._replace_swatch.set_color(hex_rgb(self.replace_rgb))
        self._replace_label.text = str(self.replace_rgb)
        self._schedule_preview()

    def _on_hue_change(self, r, g, b):
        self.replace_rgb = (r, g, b)
        self._replace_swatch.set_color(hex_rgb(self.replace_rgb))
        self._replace_label.text = str(self.replace_rgb)
        self._schedule_preview()

    def _on_tol_change(self, instance, value):
        self.tolerance = int(value)
        self._tol_label.text = str(self.tolerance)
        self._schedule_preview()

    # -- live preview --------------------------------------------------
    def _schedule_preview(self):
        if self._preview_event is not None:
            self._preview_event.cancel()
        self._preview_event = Clock.schedule_once(self._update_preview, 0.2)

    @mainthread
    def _update_preview(self, dt):
        self._preview_event = None
        if self.preview_src is None:
            return
        try:
            result = recolor_image(self.preview_src, self.target_rgb,
                                   self.replace_rgb, self.tolerance)
            self._live_image.texture = pil_to_texture(result)
        except Exception as exc:
            self.app_ref.set_status("Preview update failed", RED)
            # Preview errors are visible but do not interrupt the editor.

    # -- save ----------------------------------------------------------
    def _save(self):
        if self.pil_image is None:
            return self.app_ref.show_dialog(
                "Error", "Please load an image first.")

        def on_pick(output_path):
            if not output_path:
                return
            if not self._begin_operation(
                "Recolored and saved image", self.image_path, output_path
            ):
                return
            # Keep a reference to the immutable source image instead of making
            # a second full-size copy on the UI thread.
            image_snapshot = self.pil_image
            target_snapshot = tuple(self.target_rgb)
            replace_snapshot = tuple(self.replace_rgb)
            tolerance_snapshot = int(self.tolerance)
            self.app_ref.set_status("Applying color replacement...", YELLOW)
            try:
                threading.Thread(
                    target=self._save_worker,
                    args=(output_path, image_snapshot, target_snapshot,
                          replace_snapshot, tolerance_snapshot),
                    daemon=True,
                ).start()
            except Exception as exc:
                self._save_fail(str(exc))

        pick_save_file(on_pick, app_ref=self.app_ref,
                       default_name="recolored.png")

    def _save_worker(self, out_path, image, target_rgb, replace_rgb, tolerance):
        try:
            if not out_path.lower().endswith(".png"):
                out_path += ".png"
            result = recolor_image(image, target_rgb, replace_rgb, tolerance)
            _safe_save_image(result, out_path)
            filename = os.path.basename(out_path)
            output_description = out_path
            if self.app_ref.storage is not None:
                output_description = self.app_ref.storage.describe_path(out_path)
                filename = self.app_ref.storage.export_file_if_needed(out_path)
            self._save_done(filename, output_description)
        except Exception as exc:
            self._save_fail(str(exc))

    @mainthread
    def _save_done(self, filename, output_description):
        if self._history_context is not None:
            self._history_context["output_path"] = output_description
        self.app_ref.set_status(f"Saved -> {filename}", GREEN)
        self._finish_operation(True, f"Saved file: {filename}")
        self.app_ref.show_dialog(
            "Done", f"Color replaced and saved!\n{filename}")

    @mainthread
    def _save_fail(self, msg):
        self.app_ref.set_status("Save failed", RED)
        self._finish_operation(False, msg)
        self.app_ref.show_dialog("Error", msg)


# ============================================================================
#  MAIN APP
# ============================================================================
class RadarApp(MDApp):
    def build(self):
        self.title = "GTA SA Radar Tool"
        self.theme_cls.theme_style = "Dark"
        # KivyMD 2 uses Material 3 dynamic colors; provide a real RGBA palette source.
        self.theme_cls.primary_palette = "#8b5cf6"

        self.storage = None
        self._storage_init_error = None
        self.history = OperationHistory(
            os.path.join(self.user_data_dir, "operation_history.json")
        )
        if ANDROID:
            try:
                self.storage = AndroidStorage(self)
            except Exception as exc:
                self._storage_init_error = str(exc)

        root = MDBoxLayout(orientation="vertical",
                           md_bg_color=hex_rgba(BG_MAIN))

        # KivyMD 2.0 uses a separate navigation bar; keep each tool as a screen.
        self.merge_tab = MergeItem(self)
        self.split_tab = SplitItem(self)
        self.txd_tab = TxDItem(self)
        self.color_tab = ColorItem(self)
        self.history_tab = HistoryItem(self)

        screen_manager = ScreenManager()
        for screen in (
            self.merge_tab, self.split_tab, self.txd_tab,
            self.color_tab, self.history_tab,
        ):
            screen_manager.add_widget(screen)
        screen_manager.current = self.merge_tab.name
        root.add_widget(screen_manager)

        nav = MDBoxLayout(
            orientation="horizontal",
            size_hint_y=None,
            height=dp(56),
            spacing=dp(2),
            padding=(dp(4), 0, dp(4), 0),
            md_bg_color=hex_rgba(BG_SIDEBAR),
        )
        for screen in (
            self.merge_tab, self.split_tab, self.txd_tab,
            self.color_tab, self.history_tab,
        ):
            button = MDButton(
                MDButtonText(text=screen.tab_text),
                style="text",
                size_hint_x=1,
                size_hint_y=None,
                height=dp(48),
            )
            button.bind(
                on_release=lambda _button, target=screen: setattr(
                    screen_manager, "current", target.name
                )
            )
            nav.add_widget(button)
        root.add_widget(nav)

        # Persistent status bar
        status_box = MDBoxLayout(
            orientation="horizontal",
            size_hint_y=None, height=dp(32),
            padding=(dp(16), 0, dp(16), 0),
            spacing=dp(8),
            md_bg_color=hex_rgba(BG_SIDEBAR),
        )
        self._status_dot = MDLabel(
            text="\u25CF",  # ●
            font_size=dp(14),
            theme_text_color="Custom",
            text_color=hex_rgba(GREEN),
            size_hint_x=None, width=dp(20),
            valign="middle",
        )
        self._status_label = MDLabel(
            text="Ready", font_size=dp(12),
            theme_text_color="Custom",
            text_color=hex_rgba(FG_DIM),
            size_hint_y=None, height=dp(32),
        )
        status_box.add_widget(self._status_dot)
        status_box.add_widget(self._status_label)
        root.add_widget(status_box)

        if self._storage_init_error:
            Clock.schedule_once(
                lambda dt: self.show_dialog(
                    "Android storage initialization failed",
                    "File and folder operations may not work. "
                    + self._storage_init_error),
                0.2)

        return root

    def set_status(self, text, color=GREEN):
        self._status_label.text = text
        self._status_dot.text_color = hex_rgba(color)

    def show_dialog(self, title, text):
        def _ok(*args):
            dialog.dismiss()

        dialog = MDDialog(
            MDDialogHeadlineText(text=title),
            MDDialogSupportingText(text=text),
            MDDialogButtonContainer(
                MDButton(
                    MDButtonText(text="OK"), style="text",
                    on_release=_ok,
                ),
                spacing=dp(8),
            ),
        )
        dialog.open()


if __name__ == "__main__":
    RadarApp().run()
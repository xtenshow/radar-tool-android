"""In-app browser for Android Storage Access Framework locations.

The operating system is used only once to grant access to a starting folder.
Routine browsing and file/folder selection are rendered inside this app.
"""
import threading

from kivy.clock import Clock
from kivy.metrics import dp
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.gridlayout import GridLayout
from kivy.uix.label import Label
from kivy.uix.modalview import ModalView
from kivy.uix.scrollview import ScrollView
from kivy.uix.textinput import TextInput


BG = (0.055, 0.06, 0.085, 1)
PANEL = (0.105, 0.11, 0.15, 1)
PANEL_ALT = (0.14, 0.145, 0.19, 1)
FG = (0.91, 0.92, 0.97, 1)
MUTED = (0.62, 0.64, 0.72, 1)
ACCENT = (0.55, 0.36, 0.96, 1)


class InternalFileBrowser(ModalView):
    """Small, native-looking in-app browser for SAF-backed folders."""

    def __init__(self, app_ref, mode, callback, default_name="recolored.png", **kwargs):
        super().__init__(
            size_hint=(0.96, 0.92),
            auto_dismiss=False,
            background_color=(0, 0, 0, 0.72),
            **kwargs,
        )
        self.app_ref = app_ref
        self.storage = app_ref.storage
        self.mode = mode  # file, input_dir, output_dir, save_file
        self.callback = callback
        self.default_name = default_name
        self._stack = []
        self._items = []
        self._visible_items = []
        self._row_images = {}
        self._thumbnail_cache = {}
        self._thumbnail_inflight = set()
        self._thumbnail_generation = 0
        self._thumbnail_schedule = None
        self._busy = False

        root = BoxLayout(
            orientation="vertical",
            spacing=dp(8),
            padding=dp(12),
        )
        with root.canvas.before:
            from kivy.graphics import Color, RoundedRectangle
            self._bg_color = Color(*PANEL)
            self._bg_rect = RoundedRectangle(pos=root.pos, size=root.size, radius=[dp(16)])
        root.bind(
            pos=lambda *_: setattr(self._bg_rect, "pos", root.pos),
            size=lambda *_: setattr(self._bg_rect, "size", root.size),
        )

        header = BoxLayout(size_hint_y=None, height=dp(42), spacing=dp(8))
        title = {
            "file": "Choose image",
            "input_dir": "Choose input folder",
            "output_dir": "Choose output folder",
            "save_file": "Save image",
        }.get(mode, "Browse files")
        self.title_label = Label(
            text=title, color=FG, bold=True, font_size="18sp",
            halign="left", valign="middle",
        )
        self.title_label.bind(size=lambda w, *_: setattr(w, "text_size", w.size))
        close_button = self._button("✕", self.dismiss, width=dp(44))
        header.add_widget(self.title_label)
        header.add_widget(close_button)
        root.add_widget(header)

        path_row = BoxLayout(size_hint_y=None, height=dp(38), spacing=dp(8))
        self.up_button = self._button("‹ Up", self._go_up, width=dp(64))
        path_row.add_widget(self.up_button)
        self.path_label = Label(
            text="Checking storage access…", color=MUTED,
            halign="left", valign="middle",
        )
        self.path_label.bind(size=lambda w, *_: setattr(w, "text_size", w.size))
        path_row.add_widget(self.path_label)
        root.add_widget(path_row)

        self.search = TextInput(
            hint_text="Search this folder",
            multiline=False,
            size_hint_y=None,
            height=dp(42),
            background_normal="",
            background_active="",
            background_color=PANEL_ALT,
            foreground_color=FG,
            hint_text_color=MUTED,
            cursor_color=ACCENT,
            padding=(dp(10), dp(10)),
        )
        self.search.bind(text=lambda *_: self._render_items())
        root.add_widget(self.search)

        self.scroll = ScrollView(do_scroll_x=False, bar_width=dp(4))
        self.rows = GridLayout(cols=1, spacing=dp(5), size_hint_y=None)
        self.rows.bind(minimum_height=self.rows.setter("height"))
        self.scroll.add_widget(self.rows)
        self.scroll.bind(scroll_y=lambda *_: self._schedule_visible_thumbnails())
        root.add_widget(self.scroll)

        self.status_label = Label(
            text="", color=MUTED, size_hint_y=None, height=dp(26),
            halign="left", valign="middle",
        )
        self.status_label.bind(size=lambda w, *_: setattr(w, "text_size", w.size))
        root.add_widget(self.status_label)

        if mode == "save_file":
            self.filename = TextInput(
                text=default_name,
                hint_text="File name",
                multiline=False,
                size_hint_y=None,
                height=dp(42),
                background_normal="",
                background_active="",
                background_color=PANEL_ALT,
                foreground_color=FG,
                hint_text_color=MUTED,
                cursor_color=ACCENT,
                padding=(dp(10), dp(10)),
            )
            root.add_widget(self.filename)
        else:
            self.filename = None

        footer = BoxLayout(size_hint_y=None, height=dp(46), spacing=dp(8))
        cancel_button = self._button("Cancel", self.dismiss)
        footer.add_widget(cancel_button)
        if mode in ("input_dir", "output_dir"):
            label = "Use this folder"
            footer.add_widget(self._button(label, self._choose_folder, accent=True))
        elif mode == "save_file":
            footer.add_widget(self._button("Save here", self._save_here, accent=True))
        root.add_widget(footer)

        self.add_widget(root)
        self.bind(on_open=lambda *_: self._start())

    def _button(self, text, callback, width=None, accent=False):
        button = Button(
            text=text,
            size_hint=(None, 1) if width else (1, 1),
            width=width or 0,
            background_normal="",
            background_down="",
            background_color=ACCENT if accent else PANEL_ALT,
            color=FG,
            font_size="14sp",
        )
        button.bind(on_release=lambda *_: callback())
        return button

    def _start(self):
        self._thumbnail_generation += 1
        if self.storage.browser_root_available():
            self._stack = [(self.storage.get_root_document_id(), "Shared folder")]
            self._refresh()
        else:
            self.rows.clear_widgets()
            self.path_label.text = "Storage access has not been granted"
            self.status_label.text = "Grant one folder once; browsing then stays inside this app."
            grant_button = self._button(
                "Grant folder access", self._grant_root, accent=True)
            grant_button.size_hint_y = None
            grant_button.height = dp(48)
            self.rows.add_widget(grant_button)

    def _grant_root(self):
        self.status_label.text = "Waiting for Android folder permission…"
        self.storage.request_root_access(self._root_granted)

    def _root_granted(self, granted):
        if not granted:
            self.status_label.text = "Access was not granted."
            return
        self._stack = [(self.storage.get_root_document_id(), "Shared folder")]
        self.search.text = ""
        self._refresh()

    def _go_up(self):
        if len(self._stack) > 1:
            self._stack.pop()
            self.search.text = ""
            self._refresh()

    def _refresh(self):
        if self._busy or not self._stack:
            return
        self._thumbnail_generation += 1
        self._busy = True
        document_id, folder_name = self._stack[-1]
        self.path_label.text = self.storage.get_document_display_path(document_id)
        self.up_button.disabled = len(self._stack) <= 1
        self.status_label.text = "Loading folder…"

        def worker():
            try:
                items = self.storage.list_browser_directory(document_id)
                error = None
            except Exception as exc:
                items, error = [], str(exc)
            Clock.schedule_once(lambda _dt: self._loaded(items, error), 0)

        threading.Thread(target=worker, daemon=True).start()

    def _loaded(self, items, error):
        self._busy = False
        if error:
            self._items = []
            self.status_label.text = "Could not read folder: " + error[:180]
            self._render_items()
            return
        self._items = sorted(
            items,
            key=lambda item: (not item["is_dir"], item["name"].casefold()),
        )
        self.status_label.text = f"{len(items)} item(s)"
        self._render_items()

    def _render_items(self):
        if not hasattr(self, "rows"):
            return
        self.rows.clear_widgets()
        self._row_images = {}
        query = self.search.text.strip().casefold()
        self._visible_items = [
            item for item in self._items
            if not query or query in item["name"].casefold()
        ]
        if not self._visible_items:
            label = Label(
                text="No matching items",
                color=MUTED,
                size_hint_y=None,
                height=dp(52),
            )
            self.rows.add_widget(label)
            return

        for item in self._visible_items:
            row = BoxLayout(
                orientation="horizontal",
                size_hint_y=None,
                height=dp(72),
                spacing=dp(8),
                padding=(dp(4), dp(4), dp(4), dp(4)),
            )
            is_image = (
                not item["is_dir"]
                and item["name"].lower().endswith((".png", ".jpg", ".jpeg", ".webp"))
            )
            if item["is_dir"]:
                leading = Label(
                    text="📁", size_hint=(None, 1), width=dp(58),
                    color=FG, font_size="24sp")
            elif is_image:
                leading = KivyImage(
                    source=self._thumbnail_cache.get(item["document_id"], ""),
                    size_hint=(None, None),
                    size=(dp(62), dp(62)),
                    allow_stretch=True,
                    keep_ratio=True,
                )
                self._row_images[item["document_id"]] = leading
            else:
                leading = Label(
                    text=(item["name"].rsplit(".", 1)[-1][:4].upper()
                          if "." in item["name"] else "FILE"),
                    size_hint=(None, 1), width=dp(58),
                    color=MUTED, font_size="11sp")

            button = Button(
                text=item["name"] + ("   ›" if item["is_dir"] else ""),
                size_hint=(1, 1),
                background_normal="",
                background_down="",
                background_color=PANEL_ALT if item["is_dir"] else PANEL,
                color=FG,
                font_size="14sp",
                halign="left",
                valign="middle",
                padding=(dp(12), 0),
            )
            button.bind(
                size=lambda w, *_: setattr(w, "text_size", (w.width - dp(20), None))
            )
            button.bind(on_release=lambda _btn, value=item: self._activate(value))
            row.add_widget(leading)
            row.add_widget(button)
            self.rows.add_widget(row)

        self._schedule_visible_thumbnails()

    def _schedule_visible_thumbnails(self):
        if self._thumbnail_schedule is not None:
            self._thumbnail_schedule.cancel()
        self._thumbnail_schedule = Clock.schedule_once(
            lambda _dt: self._load_visible_thumbnails(), 0.12)

    def _load_visible_thumbnails(self):
        self._thumbnail_schedule = None
        if not self._visible_items or self._busy:
            return
        row_height = dp(72)
        max_offset = max(0, self.rows.height - self.scroll.height)
        top_offset = (1.0 - self.scroll.scroll_y) * max_offset
        first = max(0, int(top_offset / row_height) - 2)
        visible_count = int(self.scroll.height / row_height) + 5
        candidates = self._visible_items[first:first + visible_count]
        needed = []
        for item in candidates:
            key = item["document_id"]
            is_image = (
                not item["is_dir"]
                and item["name"].lower().endswith((".png", ".jpg", ".jpeg", ".webp"))
            )
            if (is_image and key not in self._thumbnail_cache
                    and key not in self._thumbnail_inflight):
                self._thumbnail_inflight.add(key)
                needed.append(item)
        if not needed:
            return

        generation = self._thumbnail_generation

        def worker():
            for item in needed:
                key = item["document_id"]
                try:
                    path = self.storage.create_browser_thumbnail(key)
                    error = None
                except Exception as exc:
                    path = None
                    error = str(exc)
                Clock.schedule_once(
                    lambda _dt, doc_id=key, thumb=path, message=error, gen=generation:
                        self._thumbnail_ready(doc_id, thumb, message, gen),
                    0,
                )

        threading.Thread(target=worker, daemon=True).start()

    def _thumbnail_ready(self, document_id, path, error, generation):
        self._thumbnail_inflight.discard(document_id)
        if generation != self._thumbnail_generation:
            return
        if path:
            self._thumbnail_cache[document_id] = path
            widget = self._row_images.get(document_id)
            if widget is not None:
                widget.source = path


    def _activate(self, item):
        if item["is_dir"]:
            self._stack.append((item["document_id"], item["name"]))
            self.search.text = ""
            self._refresh()
            return
        if self.mode == "file":
            name = item["name"].lower()
            if not name.endswith((".png", ".jpg", ".jpeg")):
                self.status_label.text = "Choose a PNG or JPEG image."
                return
            self._run_selection(
                lambda done: self.storage.stage_browser_file(item["document_id"], done)
            )
        elif self.mode == "save_file" and self.filename is not None:
            self.filename.text = item["name"]
            self.status_label.text = "File name copied. Saving will not overwrite an existing file."

    def _run_selection(self, operation):
        if self._busy:
            return
        self._busy = True
        self.status_label.text = "Preparing selection…"

        def complete(path):
            Clock.schedule_once(lambda _dt: self._finish(path), 0)

        def worker():
            try:
                operation(complete)
            except Exception as exc:
                message = str(exc)
                Clock.schedule_once(
                    lambda _dt, text=message: self._selection_error(text), 0)

        threading.Thread(target=worker, daemon=True).start()

    def _finish(self, path):
        self._busy = False
        self.dismiss()
        Clock.schedule_once(lambda _dt: self.callback(path), 0)

    def _selection_error(self, message):
        self._busy = False
        self.status_label.text = "Selection failed: " + message[:180]

    def _choose_folder(self):
        document_id = self._stack[-1][0]
        self._run_selection(
            lambda done: self.storage.stage_browser_directory(
                document_id,
                output=(self.mode == "output_dir"),
                callback=done,
            )
        )

    def _save_here(self):
        name = (self.filename.text or "").strip()
        if not name:
            self.status_label.text = "Enter a file name."
            return
        if not name.lower().endswith(".png"):
            name += ".png"
        document_id = self._stack[-1][0]
        self._run_selection(
            lambda done: self.storage.create_browser_save_target(
                document_id, name, done
            )
        )

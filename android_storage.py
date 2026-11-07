"""Android Storage Access Framework adapter for Radar Tool.

Selected Android documents/directories are copied into app-private staging
folders so image algorithms can keep using normal filesystem paths. Results
are then exported to the user-selected SAF destination.
"""
import mimetypes
import os
import tempfile
import threading
from kivy.clock import Clock


class AndroidStorage:
    """SAF adapter; instantiate only on Android."""

    REQUEST_CODE_BASE = 5100
    RESULT_OK = -1

    def __init__(self, app_ref):
        from android import activity as android_activity
        from jnius import autoclass

        self.app_ref = app_ref
        self.Intent = autoclass("android.content.Intent")
        self.DocumentsContract = autoclass("android.provider.DocumentsContract")
        self.Document = autoclass("android.provider.DocumentsContract$Document")
        self.OpenableColumns = autoclass("android.provider.OpenableColumns")
        self.activity = autoclass("org.kivy.android.PythonActivity").mActivity
        self.resolver = self.activity.getContentResolver()
        self._pending = {}
        self._input_dirs = {}
        self._input_files = {}
        self._output_dirs = {}
        self._output_files = {}
        self._next_request_code = self.REQUEST_CODE_BASE
        self.cache_root = os.path.join(app_ref.user_data_dir, "radar_saf_cache")
        os.makedirs(self.cache_root, exist_ok=True)
        self._android_activity_module = android_activity
        android_activity.bind(on_activity_result=self._on_activity_result)

    def _report_error(self, title, exc):
        message = str(exc).strip() or "Android storage operation failed."

        def report(_dt):
            try:
                self.app_ref.set_status("File access failed", "#ef4444")
                self.app_ref.show_dialog(title, message[:1200])
            except Exception:
                pass

        Clock.schedule_once(report, 0)

    @staticmethod
    def _dispatch_callback(callback, value):
        if callback is not None:
            Clock.schedule_once(lambda _dt: callback(value), 0)

    def _launch(self, intent, request):
        code = self._next_request_code
        self._next_request_code += 1
        if self._next_request_code > 64000:
            self._next_request_code = self.REQUEST_CODE_BASE
        self._pending[code] = request
        try:
            self.activity.startActivityForResult(intent, code)
        except Exception as exc:
            self._pending.pop(code, None)
            self._report_error("Could not open Android picker", exc)

    def pick_file(self, callback, mime_type="image/*"):
        intent = self.Intent(self.Intent.ACTION_OPEN_DOCUMENT)
        intent.addCategory(self.Intent.CATEGORY_OPENABLE)
        intent.setType(mime_type)
        intent.addFlags(
            self.Intent.FLAG_GRANT_READ_URI_PERMISSION
            | self.Intent.FLAG_GRANT_PERSISTABLE_URI_PERMISSION
        )
        self._launch(intent, {"kind": "file", "callback": callback})

    def pick_directory(self, output, callback):
        intent = self.Intent(self.Intent.ACTION_OPEN_DOCUMENT_TREE)
        intent.addFlags(
            self.Intent.FLAG_GRANT_READ_URI_PERMISSION
            | self.Intent.FLAG_GRANT_WRITE_URI_PERMISSION
            | self.Intent.FLAG_GRANT_PERSISTABLE_URI_PERMISSION
        )
        self._launch(intent, {
            "kind": "directory",
            "output": bool(output),
            "callback": callback,
        })

    def pick_save_file(self, callback, default_name="recolored.png"):
        intent = self.Intent(self.Intent.ACTION_CREATE_DOCUMENT)
        intent.addCategory(self.Intent.CATEGORY_OPENABLE)
        intent.setType("image/png")
        intent.putExtra(self.Intent.EXTRA_TITLE, default_name)
        intent.addFlags(
            self.Intent.FLAG_GRANT_READ_URI_PERMISSION
            | self.Intent.FLAG_GRANT_WRITE_URI_PERMISSION
            | self.Intent.FLAG_GRANT_PERSISTABLE_URI_PERMISSION
        )
        self._launch(intent, {"kind": "save_file", "callback": callback})

    def _new_staging_dir(self, prefix):
        return tempfile.mkdtemp(prefix=prefix, dir=self.cache_root)

    def _on_activity_result(self, request_code, result_code, intent):
        try:
            code = int(request_code)
        except Exception:
            return
        request = self._pending.pop(code, None)
        if request is None or int(result_code) != self.RESULT_OK or intent is None:
            return
        try:
            uri = intent.getData()
            if uri is None:
                raise RuntimeError("Android returned no document URI.")

            # Keep the provider grant for the current app installation.
            granted = int(intent.getFlags()) & (
                self.Intent.FLAG_GRANT_READ_URI_PERMISSION
                | self.Intent.FLAG_GRANT_WRITE_URI_PERMISSION
            )
            if granted:
                try:
                    self.resolver.takePersistableUriPermission(uri, granted)
                except Exception:
                    # Some document providers do not offer persistable grants.
                    pass

            # Folder enumeration and byte copying must not block Android's UI.
            worker = threading.Thread(
                target=self._process_activity_result,
                args=(uri, request),
                daemon=True,
            )
            worker.start()
        except Exception as exc:
            self._report_error("Could not access the selected item", exc)

    def _process_activity_result(self, uri, request):
        try:
            kind = request["kind"]
            if kind == "file":
                self._finish_file_pick(uri, request["callback"])
            elif kind == "directory":
                self._finish_directory_pick(
                    uri, request["output"], request["callback"])
            elif kind == "save_file":
                self._finish_save_pick(uri, request["callback"])
            else:
                raise RuntimeError("Unknown Android picker result.")
        except Exception as exc:
            self._report_error("Could not access the selected item", exc)

    def _finish_file_pick(self, uri, callback):
        name = self._display_name(uri) or "selected_image"
        local_dir = self._new_staging_dir("input_file_")
        local_path = os.path.join(local_dir, self._safe_name(name))
        self._copy_uri_to_path(uri, local_path)
        self._input_files[os.path.realpath(local_path)] = uri
        self._dispatch_callback(callback, local_path)

    def _finish_directory_pick(self, tree_uri, output, callback):
        local_dir = self._new_staging_dir("output_" if output else "input_")
        key = os.path.realpath(local_dir)
        if output:
            self._output_dirs[key] = tree_uri
        else:
            self._input_dirs[key] = tree_uri
            self._copy_tree_files_to_directory(tree_uri, local_dir)
        self._dispatch_callback(callback, local_dir)

    def _finish_save_pick(self, uri, callback):
        local_dir = self._new_staging_dir("save_")
        local_path = os.path.join(local_dir, "recolored.png")
        self._output_files[os.path.realpath(local_path)] = uri
        self._dispatch_callback(callback, local_path)

    @staticmethod
    def _safe_name(name):
        name = str(name or "").replace("\x00", "")
        name = os.path.basename(name)
        return "selected_file" if name in ("", ".", "..") else name

    def _display_name(self, uri):
        cursor = None
        try:
            columns = [self.OpenableColumns.DISPLAY_NAME]
            cursor = self.resolver.query(uri, columns, None, None, None)
            if cursor is not None and cursor.moveToFirst():
                index = cursor.getColumnIndex(self.OpenableColumns.DISPLAY_NAME)
                if index >= 0:
                    return str(cursor.getString(index))
        finally:
            if cursor is not None:
                cursor.close()
        return None

    def _list_children(self, tree_uri):
        tree_id = self.DocumentsContract.getTreeDocumentId(tree_uri)
        children_uri = self.DocumentsContract.buildChildDocumentsUriUsingTree(
            tree_uri, tree_id)
        columns = [
            self.Document.COLUMN_DOCUMENT_ID,
            self.Document.COLUMN_DISPLAY_NAME,
            self.Document.COLUMN_MIME_TYPE,
        ]
        cursor = self.resolver.query(children_uri, columns, None, None, None)
        if cursor is None:
            raise RuntimeError(
                "Android could not list the selected folder. Try another folder provider.")
        items = []
        try:
            id_col = cursor.getColumnIndex(self.Document.COLUMN_DOCUMENT_ID)
            name_col = cursor.getColumnIndex(self.Document.COLUMN_DISPLAY_NAME)
            mime_col = cursor.getColumnIndex(self.Document.COLUMN_MIME_TYPE)
            if min(id_col, name_col, mime_col) < 0:
                raise RuntimeError("The selected folder provider returned incomplete metadata.")
            while cursor.moveToNext():
                items.append((
                    str(cursor.getString(id_col)),
                    str(cursor.getString(name_col)),
                    str(cursor.getString(mime_col)),
                ))
        finally:
            cursor.close()
        return items

    def _copy_tree_files_to_directory(self, tree_uri, local_dir):
        copied = 0
        for doc_id, name, mime_type in self._list_children(tree_uri):
            if mime_type == self.Document.MIME_TYPE_DIR:
                continue
            document_uri = self.DocumentsContract.buildDocumentUriUsingTree(
                tree_uri, doc_id)
            self._copy_uri_to_path(
                document_uri, os.path.join(local_dir, self._safe_name(name)))
            copied += 1
        if copied == 0:
            Clock.schedule_once(
                lambda _dt: self.app_ref.set_status("Selected folder is empty", "#f59e0b"),
                0,
            )

    def _copy_uri_to_path(self, uri, path):
        stream = self.resolver.openInputStream(uri)
        if stream is None:
            raise RuntimeError("Android could not open the selected file for reading.")
        buffer = [0] * 32768
        try:
            with open(path, "wb") as out:
                while True:
                    count = int(stream.read(buffer))
                    if count < 0:
                        break
                    if count == 0:
                        raise RuntimeError("Android returned an empty read while copying a file.")
                    out.write(bytes((int(buffer[i]) & 0xff) for i in range(count)))
        finally:
            stream.close()

    def _write_bytes_to_uri(self, uri, data):
        stream = self.resolver.openOutputStream(uri)
        if stream is None:
            raise RuntimeError("Android could not open the destination for writing.")
        try:
            chunk_size = 32768
            for offset in range(0, len(data), chunk_size):
                chunk = data[offset:offset + chunk_size]
                signed = [byte if byte < 128 else byte - 256 for byte in chunk]
                stream.write(signed)
            stream.flush()
        finally:
            stream.close()

    def _delete_existing_child(self, tree_uri, name):
        for doc_id, existing_name, _mime_type in self._list_children(tree_uri):
            if existing_name == name:
                existing_uri = self.DocumentsContract.buildDocumentUriUsingTree(
                    tree_uri, doc_id)
                deleted = self.DocumentsContract.deleteDocument(
                    self.resolver, existing_uri)
                if not deleted:
                    raise RuntimeError(
                        f"Cannot replace existing file {name!r} in the selected folder.")
                return

    def _copy_file_to_uri(self, uri, local_path):
        stream = self.resolver.openOutputStream(uri)
        if stream is None:
            raise RuntimeError("Android could not open the destination for writing.")
        try:
            with open(local_path, "rb") as source:
                while True:
                    chunk = source.read(32768)
                    if not chunk:
                        break
                    signed = [byte if byte < 128 else byte - 256 for byte in chunk]
                    stream.write(signed)
            stream.flush()
        finally:
            stream.close()

    def _write_file_to_tree(self, tree_uri, local_path):
        filename = self._safe_name(os.path.basename(local_path))
        mime_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        tree_id = self.DocumentsContract.getTreeDocumentId(tree_uri)
        parent_uri = self.DocumentsContract.buildDocumentUriUsingTree(
            tree_uri, tree_id
        )

        existing_uri = None
        for doc_id, existing_name, _mime_type in self._list_children(tree_uri):
            if existing_name == filename:
                existing_uri = self.DocumentsContract.buildDocumentUriUsingTree(
                    tree_uri, doc_id
                )
                break

        backup_uri = None
        backup_name = ".radartool-backup-" + os.urandom(6).hex()
        if existing_uri is not None:
            backup_uri = self.DocumentsContract.renameDocument(
                self.resolver, existing_uri, backup_name
            )
            if backup_uri is None:
                raise RuntimeError(
                    "The selected folder provider cannot safely replace "
                    f"{filename!r}; the existing file was left untouched."
                )

        output_uri = None
        temporary_uri = None
        try:
            if backup_uri is None:
                # No previous output exists, so create the final file directly.
                # This also supports providers that do not implement rename.
                output_uri = self.DocumentsContract.createDocument(
                    self.resolver, parent_uri, mime_type, filename
                )
                if output_uri is None:
                    raise RuntimeError(
                        f"Android could not create {filename!r} in the selected folder."
                    )
                self._copy_file_to_uri(output_uri, local_path)
            else:
                # Preserve the previous file under a backup name until the
                # completed replacement has been written and renamed.
                temporary_name = ".radartool-new-" + os.urandom(6).hex()
                temporary_uri = self.DocumentsContract.createDocument(
                    self.resolver, parent_uri, mime_type, temporary_name
                )
                if temporary_uri is None:
                    raise RuntimeError("Android could not create a temporary output file.")
                self._copy_file_to_uri(temporary_uri, local_path)
                output_uri = self.DocumentsContract.renameDocument(
                    self.resolver, temporary_uri, filename
                )
                if output_uri is None:
                    raise RuntimeError(
                        "The selected folder provider cannot rename the completed "
                        f"temporary file to {filename!r}."
                    )
                temporary_uri = None

        except Exception as write_error:
            for partial_uri in (temporary_uri, output_uri):
                if partial_uri is not None:
                    try:
                        self.DocumentsContract.deleteDocument(self.resolver, partial_uri)
                    except Exception:
                        pass
            if backup_uri is not None:
                try:
                    restored = self.DocumentsContract.renameDocument(
                        self.resolver, backup_uri, filename
                    )
                    if restored is None:
                        raise RuntimeError("Provider returned no URI while restoring backup.")
                except Exception as restore_error:
                    raise RuntimeError(
                        f"Writing {filename!r} failed. The old file remains under "
                        f"backup name {backup_name!r}; restoration failed: {restore_error}"
                    ) from write_error
            raise

        if backup_uri is not None:
            try:
                self.DocumentsContract.deleteDocument(self.resolver, backup_uri)
            except Exception:
                # The new output is complete; keep the backup if cleanup is unsupported.
                pass

    def export_directory_if_needed(self, local_directory):
        tree_uri = self._output_dirs.get(os.path.realpath(local_directory))
        if tree_uri is None:
            return 0
        if not os.path.isdir(local_directory):
            raise RuntimeError("The app's staging output folder no longer exists.")
        files = sorted(
            os.path.join(local_directory, name)
            for name in os.listdir(local_directory)
            if os.path.isfile(os.path.join(local_directory, name))
        )
        # An empty output folder is valid when the operation produced no
        # files (for example, all TXD conversions failed). Let the caller show
        # its more specific conversion errors instead of masking them here.
        if not files:
            return 0
        for path in files:
            self._write_file_to_tree(tree_uri, path)
        return len(files)

    def describe_path(self, local_path):
        """Return a useful source/destination address for history entries.

        SAF items do not expose ordinary filesystem paths; their content URI is
        the stable address available to the app.
        """
        if not local_path:
            return ""
        key = os.path.realpath(local_path)
        for mapping, label in (
            (self._input_dirs, "Android input folder"),
            (self._output_dirs, "Android output folder"),
            (self._input_files, "Android input file"),
            (self._output_files, "Android output file"),
        ):
            uri = mapping.get(key)
            if uri is not None:
                return label + ": " + str(uri.toString())
        return str(local_path)

    def export_file_if_needed(self, local_path):
        key = os.path.realpath(local_path)
        uri = self._output_files.get(key)
        if uri is None:
            return os.path.basename(local_path)
        display_name = self._display_name(uri) or os.path.basename(local_path)
        self._copy_file_to_uri(uri, local_path)
        self._output_files.pop(key, None)
        return display_name

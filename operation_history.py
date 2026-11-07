"""Crash-tolerant, bounded operation history for Radar Tool.

The store is intentionally independent from Kivy so its persistence behavior can
be tested on ordinary CPython and reused across app upgrades.
"""
import json
import os
import tempfile
import threading
from datetime import datetime, timezone


class OperationHistory:
    MAX_ENTRIES = 100

    def __init__(self, file_path, max_entries=MAX_ENTRIES):
        self.file_path = os.path.abspath(file_path)
        self.max_entries = max(1, int(max_entries))
        self._lock = threading.RLock()
        self._entries = self._load()

    def _load(self):
        try:
            with open(self.file_path, "r", encoding="utf-8") as source:
                data = json.load(source)
            if not isinstance(data, list):
                return []
            valid = []
            for item in data:
                if not isinstance(item, dict):
                    continue
                if not all(key in item for key in (
                    "timestamp", "title", "status", "input_path", "output_path"
                )):
                    continue
                valid.append({
                    "timestamp": str(item["timestamp"]),
                    "title": str(item["title"]),
                    "status": "success" if item["status"] == "success" else "failed",
                    "input_path": str(item["input_path"]),
                    "output_path": str(item["output_path"]),
                    "detail": str(item.get("detail", "")),
                })
            return valid[:self.max_entries]
        except (OSError, ValueError, TypeError):
            return []

    def list_entries(self):
        with self._lock:
            return [dict(item) for item in self._entries]

    def add(self, title, input_path="", output_path="", status="success",
            detail=""):
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "title": str(title or "Operation"),
            "status": "success" if status == "success" else "failed",
            "input_path": str(input_path or ""),
            "output_path": str(output_path or ""),
            "detail": str(detail or ""),
        }
        with self._lock:
            self._entries = ([entry] + self._entries)[:self.max_entries]
            directory = os.path.dirname(self.file_path) or "."
            temporary_path = None
            try:
                os.makedirs(directory, exist_ok=True)
                fd, temporary_path = tempfile.mkstemp(
                    prefix=".radartool-history-", suffix=".tmp", dir=directory
                )
                with os.fdopen(fd, "w", encoding="utf-8") as target:
                    json.dump(self._entries, target, ensure_ascii=False, indent=2)
                    target.flush()
                    os.fsync(target.fileno())
                os.replace(temporary_path, self.file_path)
                return True
            except OSError:
                if temporary_path:
                    try:
                        os.unlink(temporary_path)
                    except OSError:
                        pass
                # Keep the entry in memory; history must never break the operation.
                return False

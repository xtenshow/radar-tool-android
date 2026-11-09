"""Persistent, bounded operation history for Radar Tool."""
import json
import os
import threading
from datetime import datetime


class OperationHistory:
    """Thread-safe JSON history stored inside the app's private data folder."""

    MAX_RECORDS = 250

    def __init__(self, data_directory):
        os.makedirs(data_directory, exist_ok=True)
        self.path = os.path.join(data_directory, "operation_history.json")
        self._lock = threading.RLock()

    def _read_unlocked(self):
        try:
            with open(self.path, "r", encoding="utf-8") as source:
                data = json.load(source)
            return data if isinstance(data, list) else []
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return []

    def list_records(self):
        with self._lock:
            return list(self._read_unlocked())

    def record(self, operation, input_path, output_path, details=""):
        entry = {
            "timestamp": datetime.now().astimezone().isoformat(timespec="seconds"),
            "operation": str(operation),
            "input": str(input_path or ""),
            "output": str(output_path or ""),
            "details": str(details or ""),
            "status": "success",
        }
        with self._lock:
            records = self._read_unlocked()
            records.insert(0, entry)
            records = records[:self.MAX_RECORDS]
            temporary = self.path + ".tmp"
            with open(temporary, "w", encoding="utf-8") as target:
                json.dump(records, target, ensure_ascii=False, indent=2)
                target.flush()
                os.fsync(target.fileno())
            os.replace(temporary, self.path)
        return entry

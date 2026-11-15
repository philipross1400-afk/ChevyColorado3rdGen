"""Keep user logs beside the portable app, outside a frozen app's temp files."""
from pathlib import Path
import sys


def app_directory():
    return Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent


def logs_directory():
    return app_directory() / "dashboard_logs"

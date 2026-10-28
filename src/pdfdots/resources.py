import sys
from pathlib import Path


def resource_path(name: str) -> Path:
    bundle_directory = getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2])
    return Path(bundle_directory) / name

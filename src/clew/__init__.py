from __future__ import annotations

import importlib.metadata as _metadata

try:
    __version__: str = _metadata.version("clew-ai")
except _metadata.PackageNotFoundError:
    __version__ = "0.0.0+local"

__all__ = ["__version__"]

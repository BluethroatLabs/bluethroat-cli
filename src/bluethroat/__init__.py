"""Command-line client for Bluethroat Labs tools."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("bluethroat")
except PackageNotFoundError:
    __version__ = "0.0.0+unknown"

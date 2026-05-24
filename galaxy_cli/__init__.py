"""galaxy-cli: CLI harness for Galaxy bioinformatics platform."""

from importlib.metadata import PackageNotFoundError, version


try:
    __version__ = version("galaxy-cli")
except PackageNotFoundError:
    __version__ = "0+unknown"

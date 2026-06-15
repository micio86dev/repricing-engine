"""Base ingestor abstraction and shared CSV reading utilities."""

import csv
import io
from abc import ABC, abstractmethod
from pathlib import Path

import polars as pl

from repricing_engine.exceptions import IngestionError

_SNIFF_DELIMITERS = ",;\t|"
_SNIFF_SAMPLE_BYTES = 8192


def read_csv_rows(file_path: Path) -> list[dict[str, str]]:
    """Read a CSV into a list of string-keyed/-valued row dicts.

    Handles a UTF-8 BOM and auto-detects the delimiter. Every value is read as a
    string; downstream normalization is responsible for typing.

    Args:
        file_path: Path to the CSV file.

    Returns:
        One dict per data row, keyed by (stripped) column name.

    Raises:
        IngestionError: If the file is missing or cannot be parsed.
    """
    if not file_path.exists():
        msg = f"CSV file not found: {file_path}"
        raise IngestionError(msg)

    try:
        # ``utf-8-sig`` transparently strips a leading BOM if present.
        text = file_path.read_bytes().decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        msg = f"Cannot decode {file_path} as UTF-8"
        raise IngestionError(msg) from exc

    if not text.strip():
        return []

    try:
        dialect = csv.Sniffer().sniff(text[:_SNIFF_SAMPLE_BYTES], delimiters=_SNIFF_DELIMITERS)
        separator = dialect.delimiter
    except csv.Error:
        separator = ","

    try:
        frame = pl.read_csv(
            io.BytesIO(text.encode("utf-8")),
            separator=separator,
            infer_schema_length=0,  # read everything as Utf8
            has_header=True,
        )
    except Exception as exc:
        msg = f"Failed to parse CSV {file_path}: {exc}"
        raise IngestionError(msg) from exc

    frame = frame.rename({col: col.strip() for col in frame.columns})
    rows: list[dict[str, str]] = []
    for row in frame.iter_rows(named=True):
        rows.append({key: ("" if value is None else str(value)) for key, value in row.items()})
    return rows


class BaseIngestor[T](ABC):
    """Abstract base for CSV ingestors that yield typed product models."""

    @abstractmethod
    def ingest(self, file_path: Path) -> list[T]:
        """Read ``file_path`` and return a list of typed product models."""

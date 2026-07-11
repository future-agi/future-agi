import csv
import json
import os
from io import StringIO
from typing import Any

import pandas as pd
import structlog

logger = structlog.get_logger(__name__)


class FileProcessingError(Exception):
    """Custom exception for file processing errors"""

    pass


class FileProcessor:
    # Configuration constants
    SUPPORTED_EXTENSIONS = {".csv", ".xls", ".xlsx", ".json", ".jsonl"}

    @staticmethod
    def _deduplicate_columns(columns: list[str]) -> list[str]:
        """
        Rename duplicate column names by appending _1, _2, etc.
        E.g., ["id", "name", "id"] -> ["id", "name", "id_1"]
        """
        seen: dict[str, int] = {}
        result = []
        for col in columns:
            if col in seen:
                seen[col] += 1
                result.append(f"{col}_{seen[col]}")
            else:
                seen[col] = 0
                result.append(col)
        return result

    @staticmethod
    def process_file(file_obj: Any) -> tuple[pd.DataFrame, str | None]:
        """
        Process uploaded file and return DataFrame or error message.

        Args:
            file_obj: File object to process


        Returns:
            Tuple[pd.DataFrame, Optional[str]]: (DataFrame, error_message)
            If successful, error_message will be None
            If failed, DataFrame will be None and error_message will contain the error
        """
        try:
            # Get file extension
            file_name = getattr(file_obj, "name", None)
            if not file_name:
                raise FileProcessingError("File object must have a 'name' attribute")
            file_extension = os.path.splitext(file_name.lower())[1]
            if file_extension not in FileProcessor.SUPPORTED_EXTENSIONS:
                raise FileProcessingError("Unsupported file format")

            # Process file based on extension
            data = FileProcessor._read_file(file_obj, file_extension)

            # Validate DataFrame
            error_msg = FileProcessor._validate_dataframe(data)
            if error_msg:
                raise FileProcessingError(error_msg)

            return data, None

        except FileProcessingError as e:
            logger.warning(f"File processing error: {str(e)}")
            return None, str(e)
        except Exception:
            logger.exception("Unexpected error while processing file")
            return None, "An unexpected error occurred while processing the file"

    @staticmethod
    def _read_file(file_obj: Any, file_extension: str) -> pd.DataFrame:
        """Read file based on extension and return DataFrame"""
        file_obj.seek(0)  # Reset file pointer

        if file_extension == ".csv":
            return FileProcessor._read_csv_file(file_obj)
        elif file_extension in (".xls", ".xlsx"):
            return FileProcessor._read_excel_file(file_obj)
        elif file_extension == ".json":
            return FileProcessor._read_json_file(file_obj)
        elif file_extension == ".jsonl":
            return FileProcessor._read_json_file(file_obj, lines=True)

        raise FileProcessingError("Unsupported file format")

    @staticmethod
    def _normalize_smart_quotes(text: str) -> str:
        return (
            text.replace("\u201c", '"')
            .replace("\u201d", '"')
            .replace("\u201e", '"')
            .replace("\u201f", '"')
        )

    @staticmethod
    def _try_parse_csv_text(
        text: str, fallback_delimiters: list[str]
    ) -> tuple[pd.DataFrame | None, int, int | None]:
        """Attempt to parse CSV text; return (df, col_count, last_row_count).

        Picks the delimiter that yields the widest consistent parse rather
        than the first one that lines up — a wrong delimiter absent from the
        file produces "1 column per line" rows that look consistent but
        collapse every real column into a single string.
        """
        sample = text[:4096]
        try:
            # Restrict Sniffer to real CSV delimiters; otherwise it will
            # happily pick a letter (e.g. 'n' from "first_name") whenever
            # that character happens to split rows into equal counts.
            delimiter = csv.Sniffer().sniff(
                sample, delimiters="".join(fallback_delimiters)
            ).delimiter
        except csv.Error:
            delimiter = None

        delimiters_to_try = [delimiter] if delimiter else []
        for d in fallback_delimiters:
            if d not in delimiters_to_try:
                delimiters_to_try.append(d)

        best_df: pd.DataFrame | None = None
        best_cols = 0
        last_row_count: int | None = None
        for delim in delimiters_to_try:
            try:
                rows = list(
                    csv.reader(StringIO(text), delimiter=delim, quotechar='"')
                )
            except csv.Error:
                continue
            last_row_count = len(rows) if rows else 0
            if not rows or len(rows) < 2:
                continue
            header = rows[0]
            expected_cols = len(header)
            if any(len(r) != expected_cols for r in rows[1:]):
                continue
            if expected_cols <= best_cols:
                continue
            header = FileProcessor._deduplicate_columns(header)
            df = pd.DataFrame(rows[1:], columns=header)
            df.reset_index(drop=True, inplace=True)
            best_df = df
            best_cols = expected_cols

        return best_df, best_cols, last_row_count

    @staticmethod
    def _read_csv_file(file_obj: Any) -> pd.DataFrame:
        encodings = ["utf-8", "latin1", "cp1252", "iso-8859-1"]
        fallback_delimiters = [",", "\t", ";", "|"]

        for encoding in encodings:
            try:
                file_obj.seek(0)
                raw_data = file_obj.read()
                if isinstance(raw_data, bytes):
                    raw_data = raw_data.decode(encoding)

                # Try the file as-is first, then retry after rewriting curly
                # quotes to straight quotes. The rewrite rescues CSVs whose
                # fields are wrapped in typographic quotes (Excel/Google Sheets
                # exports) but corrupts CSVs where curly quotes appear as
                # literal text inside properly-quoted fields, so it must not
                # run unconditionally.
                candidates = [raw_data]
                normalized = FileProcessor._normalize_smart_quotes(raw_data)
                if normalized != raw_data:
                    candidates.append(normalized)

                best_df: pd.DataFrame | None = None
                best_cols = 0
                last_row_count: int | None = None
                for candidate in candidates:
                    df, cols, rows_seen = FileProcessor._try_parse_csv_text(
                        candidate, fallback_delimiters
                    )
                    if df is not None and cols > best_cols:
                        best_df = df
                        best_cols = cols
                    if rows_seen is not None:
                        last_row_count = rows_seen

                if best_df is not None:
                    return best_df

                if last_row_count == 1:
                    raise FileProcessingError(
                        "The file contains only a header row with no data."
                    )
                raise FileProcessingError(
                    "Unable to detect delimiter correctly; rows have inconsistent column counts."
                )
            except UnicodeDecodeError:
                continue
            except FileProcessingError:
                raise
            except Exception as e:
                logger.warning(f"Error reading CSV with encoding {encoding}: {str(e)}")
                continue

        raise FileProcessingError(
            "Unable to read CSV file with any supported encoding or delimiter."
        )

    @staticmethod
    def _read_excel_file(file_obj: Any) -> pd.DataFrame:
        """Read Excel file"""
        try:
            return pd.read_excel(file_obj)
        except Exception as e:
            logger.exception(f"Error reading Excel file: {str(e)}")
            if "Excel file format cannot be determined" in str(e):
                raise FileProcessingError("Invalid Excel file format") from e
            raise FileProcessingError(f"Error reading Excel file: {str(e)}") from e

    @staticmethod
    def _read_json_file(file_obj: Any, lines: bool = False) -> pd.DataFrame:
        """Read JSON/JSONL file"""
        try:
            return pd.read_json(file_obj, lines=lines)
        except ValueError as e:
            error_msg = str(e)

            # Check if error is due to mismatched array lengths (single object with mixed types)
            if "All arrays must be of the same length" in error_msg and not lines:
                logger.info(
                    "Detected mismatched array lengths, attempting to parse as single record"
                )
                try:
                    # Reset file pointer and parse JSON manually
                    file_obj.seek(0)
                    raw_data = file_obj.read()
                    if isinstance(raw_data, bytes):
                        raw_data = raw_data.decode("utf-8")

                    data = json.loads(raw_data)

                    # If it's a single dict (not list), treat as one row
                    # This handles JSON objects with mixed scalar/array/nested values
                    if isinstance(data, dict) and data:
                        logger.info("Treating JSON object as single record")
                        return pd.DataFrame([data])

                except Exception as parse_error:
                    logger.warning(
                        f"Failed to parse as single record: {str(parse_error)}"
                    )

            # If not the padding case or padding failed, raise original error
            logger.warning(f"Error reading JSON file: {error_msg}")
            file_type = "JSONL" if lines else "JSON"
            raise FileProcessingError(f"Invalid {file_type} format: {error_msg}") from e

    @staticmethod
    def _validate_dataframe(df: pd.DataFrame) -> str | None:
        """
        Validate DataFrame and return error message if invalid.
        Returns None if valid.
        """
        if df.empty:
            return "The file contains no data"

        return None

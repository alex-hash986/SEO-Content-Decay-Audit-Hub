import csv
import io
import json
import re
from collections.abc import Iterator
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import PurePath
from typing import Any

from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from app.models import ImportBatch, PageMetricSnapshot, QuarantineRecord, QueryPageMetric
from app.schemas import DatasetType, ImportPreset


GSC_ALIASES = {
    "url": ("top pages", "page", "pages", "url", "страницы", "страница"),
    "query": ("top queries", "query", "queries", "запросы", "запрос"),
    "curr_clicks": ("clicks", "clicks (current)", "last 3 months clicks", "клики", "клики (текущий период)"),
    "prev_clicks": ("previous clicks", "clicks (previous)", "previous 3 months clicks", "клики (предыдущий период)"),
    "curr_impressions": ("impressions", "impressions (current)", "last 3 months impressions", "показы", "показы (текущий период)"),
    "prev_impressions": ("previous impressions", "impressions (previous)", "previous 3 months impressions", "показы (предыдущий период)"),
    "curr_ctr": ("ctr", "ctr (current)", "last 3 months ctr", "ctr (текущий период)"),
    "prev_ctr": ("previous ctr", "ctr (previous)", "previous 3 months ctr", "ctr (предыдущий период)"),
    "curr_position": ("position", "position (current)", "last 3 months position", "позиция", "позиция (текущий период)"),
    "prev_position": ("previous position", "position (previous)", "previous 3 months position", "позиция (предыдущий период)"),
}
YANDEX_ALIASES = {
    "url": ("url", "страница", "адрес страницы"),
    "query": ("запрос", "поисковый запрос", "query"),
    "prev_clicks": ("клики (было)", "клики было", "prev_clicks"),
    "curr_clicks": ("клики (стало)", "клики стало", "клики", "curr_clicks"),
    "prev_impressions": ("показы (было)", "показы было", "prev_impressions"),
    "curr_impressions": ("показы (стало)", "показы стало", "показы", "curr_impressions"),
    "prev_ctr": ("ctr (было)", "ctr (%) было", "prev_ctr"),
    "curr_ctr": ("ctr (стало)", "ctr (%)", "ctr", "curr_ctr"),
    "prev_position": ("средняя позиция (было)", "позиция (было)", "prev_position"),
    "curr_position": ("средняя позиция (стало)", "средняя позиция", "позиция", "curr_position"),
}
METRIC_FIELDS = (
    "prev_clicks", "curr_clicks", "prev_impressions", "curr_impressions",
    "prev_ctr", "curr_ctr", "prev_position", "curr_position",
)
METRIC_MODELS = {"pages": PageMetricSnapshot, "queries": QueryPageMetric}
KEY_FIELDS = {"pages": ("url",), "queries": ("query", "url")}
MISSING = object()


class ImportFileError(ValueError):
    """The uploaded file cannot be read as a dataset."""


class InvalidRow(ValueError):
    """A data record cannot be stored as a metric."""


def column_mapping(dataset_type: DatasetType, preset: ImportPreset) -> dict[str, tuple[str, ...]]:
    fields = (*KEY_FIELDS[dataset_type], *METRIC_FIELDS)
    if dataset_type == "pages":
        fields += ("page_title", "content_group", "published_at", "last_updated_at")
    else:
        fields += ("intent",)

    aliases = {}
    if preset == "gsc":
        aliases = GSC_ALIASES
        if dataset_type == "queries":
            aliases = {**aliases, "url": ("page", "top pages", "url", "страница")}
    elif preset == "yandex_webmaster":
        aliases = YANDEX_ALIASES
    return {field: aliases.get(field, (field,)) for field in fields}


def read_csv(text: str, headers: set[str]) -> Iterator[tuple[dict[str, Any], str | None]]:
    # Sniff only the header so decimal commas in the data cannot sway detection.
    header_line = next(line for line in io.StringIO(text, newline="") if line.strip())
    try:
        delimiter = csv.Sniffer().sniff(header_line, delimiters=",;").delimiter
    except csv.Error:
        delimiter = ";" if header_line.count(";") > header_line.count(",") else ","

    reader = csv.reader(io.StringIO(text, newline=""), delimiter=delimiter, strict=True)
    try:
        header = next(row for row in reader if row and any(cell.strip() for cell in row))
        names = [cell.strip().casefold() for cell in header]
        if not headers.intersection(names):
            raise ImportFileError("CSV header must contain a recognized column for the selected preset")
        if len(set(names)) != len(names) or "" in names:
            raise ImportFileError("CSV header contains blank or duplicate column names")

        for values in reader:
            if not values:
                continue
            raw = dict.fromkeys(header)
            raw.update(zip(header, values))
            if len(values) > len(header):
                extra_key = "_extra_columns"
                while extra_key in raw:
                    extra_key += "_"
                raw[extra_key] = values[len(header):]
                yield raw, "CSV row has more values than the header"
            else:
                yield raw, None
    except (csv.Error, StopIteration) as error:
        raise ImportFileError("Invalid CSV or missing header") from error


def reject_json_constant(value: str) -> None:
    raise ValueError(f"Invalid JSON constant: {value}")


def read_rows(
    content: bytes, filename: str, mapping: dict[str, tuple[str, ...]],
) -> Iterator[tuple[dict[str, Any], str | None]]:
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        try:
            text = content.decode("cp1251")
        except UnicodeDecodeError as error:
            raise ImportFileError("File must use UTF-8 or Windows-1251 encoding") from error

    if not text.strip():
        raise ImportFileError("The uploaded file is empty")

    suffix = PurePath(filename).suffix.lower()
    if suffix == ".json" or (suffix != ".csv" and text.lstrip().startswith(("[", "{"))):
        try:
            rows = json.loads(text, parse_constant=reject_json_constant)
            # The raw payload must also be serializable as strict JSON in quarantine.
            json.dumps(rows, allow_nan=False, ensure_ascii=False).encode("utf-8")
        except (ValueError, RecursionError) as error:
            raise ImportFileError("Invalid JSON") from error
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise ImportFileError("JSON must be an array of objects")
        if not rows:
            raise ImportFileError("The uploaded file contains no data rows")
        for row in rows:
            yield row, None
        return

    headers = {alias for aliases in mapping.values() for alias in aliases}
    yield from read_csv(text, headers)


def mapped_values(raw: dict[str, Any], mapping: dict[str, tuple[str, ...]]) -> dict[str, Any]:
    columns = {key.strip().casefold(): value for key, value in raw.items()}
    return {
        field: next((columns[alias] for alias in aliases if alias in columns), None)
        for field, aliases in mapping.items()
    }


def is_blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def text_value(row: dict[str, Any], field: str, max_length: int, default: Any = MISSING) -> str | None:
    value = row[field]
    if is_blank(value):
        if default is MISSING:
            raise InvalidRow(f"{field} is required")
        return default
    if not isinstance(value, str) or "\x00" in value:
        raise InvalidRow(f"{field} must be valid text")
    value = value.strip()
    if len(value) > max_length:
        raise InvalidRow(f"{field} must be at most {max_length} characters")
    return value


def number(value: Any, field: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise InvalidRow(f"{field} must be a number")
    normalized = "".join(str(value).split()).replace(",", ".")
    try:
        result = Decimal(normalized)
    except InvalidOperation as error:
        raise InvalidRow(f"{field} must be a number") from error
    if not result.is_finite():
        raise InvalidRow(f"{field} must be a finite number")
    return result


def count(value: Any, field: str) -> int:
    if is_blank(value):
        return 0
    result = number(value, field)
    if not 0 <= result <= 2_147_483_647 or result != result.to_integral_value():
        raise InvalidRow(f"{field} must be a non-negative 32-bit integer")
    return int(result)


def ctr(value: Any, field: str, clicks: int, impressions: int) -> float:
    if is_blank(value):
        return clicks / impressions if impressions else 0.0
    percent = isinstance(value, str) and value.strip().endswith("%")
    result = number(value.strip()[:-1] if percent else value, field)
    maximum = 100 if percent else 1
    if not 0 <= result <= maximum:
        raise InvalidRow(f"{field} must be between 0 and 1 after normalization")
    return float(result) / maximum


def position(value: Any, field: str, default: float) -> float:
    if is_blank(value):
        return default
    result = number(value, field)
    if not 1 <= result <= 200:
        raise InvalidRow(f"{field} must be between 1 and 200 when supplied")
    return float(result)


def iso_date(value: Any, field: str) -> date | None:
    if is_blank(value):
        return None
    if isinstance(value, str) and re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value.strip()):
        try:
            return date.fromisoformat(value.strip())
        except ValueError:
            pass
    raise InvalidRow(f"{field} must be a valid date in YYYY-MM-DD format")


def normalize_row(row: dict[str, Any], dataset_type: DatasetType) -> dict[str, Any]:
    values = {"url": text_value(row, "url", 1024)}
    if dataset_type == "queries":
        values["query"] = text_value(row, "query", 500)

    for period in ("curr", "prev"):
        clicks_field = f"{period}_clicks"
        impressions_field = f"{period}_impressions"
        ctr_field = f"{period}_ctr"
        clicks = count(row[clicks_field], clicks_field)
        impressions = count(row[impressions_field], impressions_field)
        if impressions > 0 and clicks > impressions:
            raise InvalidRow(f"{clicks_field} cannot exceed {impressions_field}")
        values[clicks_field] = clicks
        values[impressions_field] = impressions
        values[ctr_field] = ctr(row[ctr_field], ctr_field, clicks, impressions)

    values["curr_position"] = position(row["curr_position"], "curr_position", 0.0)
    values["prev_position"] = position(row["prev_position"], "prev_position", values["curr_position"])
    if dataset_type == "pages":
        values.update(
            page_title=text_value(row, "page_title", 500, None),
            content_group=text_value(row, "content_group", 100, "general"),
            published_at=iso_date(row["published_at"], "published_at"),
            last_updated_at=iso_date(row["last_updated_at"], "last_updated_at"),
        )
    else:
        del values["prev_ctr"], values["prev_position"]
        values["intent"] = text_value(row, "intent", 50, "informational")
    return values


def upsert_metrics(
    db: Session, dataset_type: DatasetType, records: dict[tuple[str, ...], dict[str, Any]],
) -> None:
    if not records:
        return

    table = METRIC_MODELS[dataset_type].__table__
    key_fields = ("project_id", *KEY_FIELDS[dataset_type])
    insert = {"sqlite": sqlite_insert, "postgresql": postgres_insert}[db.get_bind().dialect.name]
    statement = insert(table)
    statement = statement.on_conflict_do_update(
        index_elements=key_fields,
        set_={
            field: statement.excluded[field]
            for field in next(iter(records.values()))
            if field not in key_fields
        },
    )
    # Use a consistent key order for overlapping PostgreSQL imports.
    db.execute(statement, [records[key] for key in sorted(records)])


def import_dataset(
    db: Session,
    project_id: int,
    dataset_type: DatasetType,
    preset: ImportPreset,
    filename: str,
    content: bytes,
) -> ImportBatch:
    if len(filename) > 255 or "\x00" in filename:
        raise ImportFileError("Invalid upload filename")
    mapping = column_mapping(dataset_type, preset)
    batch = ImportBatch(
        project_id=project_id, dataset_type=dataset_type, preset=preset, filename=filename,
    )
    records = {}
    total_rows = 0
    for total_rows, (raw, parse_error) in enumerate(read_rows(content, filename, mapping), start=1):
        try:
            if parse_error:
                raise InvalidRow(parse_error)
            values = normalize_row(mapped_values(raw, mapping), dataset_type)
        except InvalidRow as error:
            batch.quarantine_records.append(QuarantineRecord(
                project_id=project_id,
                dataset_type=dataset_type,
                row_number=total_rows,
                raw_payload=raw,
                error_reason=str(error),
            ))
        else:
            key = tuple(values[field] for field in KEY_FIELDS[dataset_type])
            records[key] = {"project_id": project_id, **values}

    if not total_rows:
        raise ImportFileError("The uploaded file contains no data rows")

    batch.total_rows = total_rows
    batch.quarantined_rows = len(batch.quarantine_records)
    batch.valid_rows = total_rows - batch.quarantined_rows
    db.add(batch)
    db.flush()
    upsert_metrics(db, dataset_type, records)
    return batch
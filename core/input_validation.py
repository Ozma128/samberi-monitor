"""Безопасная загрузка справочников, изображений и ZIP-архивов.

Все лимиты проверяются до передачи данных в pandas, Pillow и Vision API. Это
защищает приложение от ZIP-бомб, изображений с чрезмерным числом пикселей и
случайной загрузки слишком больших наборов данных.
"""

from __future__ import annotations

import io
import re
import unicodedata
import warnings
import zipfile
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any

import pandas as pd
from PIL import Image, ImageOps, UnidentifiedImageError

MIB = 1024 * 1024

MAX_CATALOG_BYTES = 20 * MIB
MAX_CATALOG_ROWS = 100_000
MAX_CATALOG_COLUMNS = 100
MAX_CATALOG_CELLS = 2_000_000
MAX_CATALOG_MEMORY_BYTES = 64 * MIB
MAX_XLSX_UNCOMPRESSED_BYTES = 75 * MIB
MAX_XLSX_MEMBERS = 10_000

MAX_ARCHIVE_BYTES = 75 * MIB
MAX_ARCHIVE_MEMBERS = 500
MAX_UPLOAD_FILES = 50
MAX_TOTAL_UPLOAD_BYTES = 75 * MIB
MAX_IMAGES = 50
MAX_IMAGE_BYTES = 12 * MIB
MAX_TOTAL_IMAGE_BYTES = 75 * MIB
MAX_IMAGE_PIXELS = 25_000_000
MAX_IMAGE_SIDE = 4096
MAX_PREVIEW_SIDE = 1280
MAX_PREVIEW_BYTES = 2 * MIB
MAX_TOTAL_PREVIEW_BYTES = 10 * MIB
MAX_COMPRESSION_RATIO = 200.0

ALLOWED_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png"}
ALLOWED_IMAGE_FORMATS = {"JPEG", "PNG"}


class InputValidationError(ValueError):
    """Ошибка безопасной валидации пользовательского файла."""


@dataclass(frozen=True, slots=True)
class _PreparedImageStamp:
    """Bind a private preparation marker to the exact validated payload."""

    data: bytes
    filename: str
    mime: str


def _is_prepared_image(item: Any) -> bool:
    """Return whether an item is the unchanged output of this module."""

    if not isinstance(item, dict):
        return False
    stamp = item.get("_prepared_image")
    return (
        isinstance(stamp, _PreparedImageStamp)
        and item.get("data") is stamp.data
        and item.get("filename") == stamp.filename
        and item.get("mime") == stamp.mime == "image/jpeg"
    )


def _read_all(file_or_bytes: Any) -> bytes:
    if isinstance(file_or_bytes, bytes):
        return file_or_bytes
    if isinstance(file_or_bytes, bytearray):
        return bytes(file_or_bytes)
    if hasattr(file_or_bytes, "getvalue"):
        return bytes(file_or_bytes.getvalue())
    if hasattr(file_or_bytes, "read"):
        position = None
        if hasattr(file_or_bytes, "tell"):
            try:
                position = file_or_bytes.tell()
            except (OSError, ValueError):
                position = None
        data = file_or_bytes.read()
        if position is not None and hasattr(file_or_bytes, "seek"):
            try:
                file_or_bytes.seek(position)
            except (OSError, ValueError):
                pass
        return bytes(data)
    raise InputValidationError("Неподдерживаемый тип загруженного файла.")


def _safe_basename(name: str, fallback: str) -> str:
    normalized = unicodedata.normalize("NFKC", str(name or ""))
    normalized = normalized.replace("\\", "/")
    normalized = PurePosixPath(normalized).name
    normalized = re.sub(r"[\x00-\x1f\x7f]", "", normalized).strip(" .")
    if not normalized:
        normalized = fallback
    stem = PurePosixPath(normalized).stem[:100].strip(" .") or "image"
    suffix = PurePosixPath(normalized).suffix.lower()
    return f"{stem}{suffix}"


def _validate_zip_metadata(
    archive: zipfile.ZipFile,
    *,
    max_members: int,
    max_uncompressed_bytes: int,
    max_ratio: float | None,
) -> list[zipfile.ZipInfo]:
    members = archive.infolist()
    if len(members) > max_members:
        raise InputValidationError(
            f"В архиве слишком много объектов: {len(members)} (максимум {max_members})."
        )

    total = 0
    for member in members:
        if member.flag_bits & 0x1:
            raise InputValidationError("Зашифрованные ZIP-архивы не поддерживаются.")
        if member.file_size < 0 or member.compress_size < 0:
            raise InputValidationError("ZIP-архив содержит некорректные размеры файлов.")
        total += member.file_size
        if total > max_uncompressed_bytes:
            raise InputValidationError("Распакованный размер архива превышает безопасный лимит.")
        if max_ratio and member.file_size > MIB:
            ratio = member.file_size / max(member.compress_size, 1)
            if ratio > max_ratio:
                raise InputValidationError(
                    "ZIP-архив имеет подозрительно высокий коэффициент сжатия."
                )
    return members


def _read_csv(data: bytes) -> pd.DataFrame:
    last_error: Exception | None = None
    for encoding in ("utf-8-sig", "cp1251"):
        try:
            return pd.read_csv(
                io.BytesIO(data),
                encoding=encoding,
                sep=None,
                engine="python",
                nrows=MAX_CATALOG_ROWS + 1,
                dtype=str,
            )
        except UnicodeDecodeError as exc:
            last_error = exc
        except pd.errors.ParserError as exc:
            last_error = exc
    raise InputValidationError(
        "Не удалось разобрать CSV (ожидается UTF-8 или Windows-1251)."
    ) from last_error


def load_catalog_file(file_or_bytes: Any, filename: str | None = None) -> pd.DataFrame:
    """Загрузить и ограничить каталог CSV/XLSX."""

    name = filename or getattr(file_or_bytes, "name", "catalog")
    safe_name = _safe_basename(name, "catalog")
    suffix = PurePosixPath(safe_name).suffix.lower()
    if suffix not in {".csv", ".xlsx"}:
        raise InputValidationError("Поддерживаются только файлы .csv и .xlsx.")

    data = _read_all(file_or_bytes)
    if not data:
        raise InputValidationError("Файл справочника пуст.")
    if len(data) > MAX_CATALOG_BYTES:
        raise InputValidationError(f"Справочник больше {MAX_CATALOG_BYTES // MIB} МБ.")

    try:
        if suffix == ".csv":
            frame = _read_csv(data)
        else:
            try:
                with zipfile.ZipFile(io.BytesIO(data)) as archive:
                    _validate_zip_metadata(
                        archive,
                        max_members=MAX_XLSX_MEMBERS,
                        max_uncompressed_bytes=MAX_XLSX_UNCOMPRESSED_BYTES,
                        max_ratio=None,
                    )
            except (zipfile.BadZipFile, OSError) as exc:
                raise InputValidationError(
                    "Файл XLSX повреждён или имеет неверный формат."
                ) from exc
            frame = pd.read_excel(io.BytesIO(data), nrows=MAX_CATALOG_ROWS + 1)
    except InputValidationError:
        raise
    except Exception as exc:
        raise InputValidationError("Не удалось прочитать справочник.") from exc

    frame = frame.dropna(axis=0, how="all").dropna(axis=1, how="all")
    if frame.empty:
        raise InputValidationError("В справочнике нет данных.")
    if len(frame) > MAX_CATALOG_ROWS:
        raise InputValidationError(f"В справочнике больше {MAX_CATALOG_ROWS:,} строк.")
    if len(frame.columns) > MAX_CATALOG_COLUMNS:
        raise InputValidationError(f"В справочнике больше {MAX_CATALOG_COLUMNS} колонок.")
    if len(frame) * len(frame.columns) > MAX_CATALOG_CELLS:
        raise InputValidationError("В справочнике слишком много ячеек.")
    memory_bytes = int(frame.memory_usage(index=True, deep=True).sum())
    if memory_bytes > MAX_CATALOG_MEMORY_BYTES:
        raise InputValidationError("Справочник занимает слишком много оперативной памяти.")
    return frame


def normalize_image(data: bytes, filename: str) -> tuple[bytes, str, str]:
    """Проверить изображение и вернуть безопасный JPEG без метаданных."""

    if not data:
        raise InputValidationError(f"Изображение «{filename}» пустое.")
    if len(data) > MAX_IMAGE_BYTES:
        raise InputValidationError(f"Изображение «{filename}» больше {MAX_IMAGE_BYTES // MIB} МБ.")

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as source:
                if source.format not in ALLOWED_IMAGE_FORMATS:
                    raise InputValidationError(f"«{filename}» не является JPEG или PNG.")
                width, height = source.size
                if width <= 0 or height <= 0 or width * height > MAX_IMAGE_PIXELS:
                    raise InputValidationError(
                        f"Изображение «{filename}» имеет слишком большое разрешение."
                    )
                source.load()
                image = ImageOps.exif_transpose(source).convert("RGB")
                image.thumbnail((MAX_IMAGE_SIDE, MAX_IMAGE_SIDE))
                buffer = io.BytesIO()
                image.save(buffer, format="JPEG", quality=90, optimize=True)
    except InputValidationError:
        raise
    except (Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise InputValidationError(
            f"Изображение «{filename}» имеет небезопасное разрешение."
        ) from exc
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise InputValidationError(f"Не удалось прочитать изображение «{filename}».") from exc

    safe_name = _safe_basename(filename, "image.jpg")
    output_name = f"{PurePosixPath(safe_name).stem}.jpg"
    return buffer.getvalue(), output_name, "image/jpeg"


def create_image_preview(data: bytes) -> bytes:
    """Create a bounded review image instead of retaining full OCR inputs in a session."""

    try:
        with Image.open(io.BytesIO(data)) as source:
            source.load()
            image = source.convert("RGB")
            image.thumbnail((MAX_PREVIEW_SIDE, MAX_PREVIEW_SIDE))
            buffer = io.BytesIO()
            image.save(buffer, format="JPEG", quality=75, optimize=True)
            preview = buffer.getvalue()
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise InputValidationError("Не удалось создать безопасный предпросмотр.") from exc
    if len(preview) > MAX_PREVIEW_BYTES:
        raise InputValidationError("Предпросмотр изображения превышает безопасный лимит.")
    return preview


def _unique_name(name: str, used: set[str]) -> str:
    candidate = name
    stem = PurePosixPath(name).stem
    suffix = PurePosixPath(name).suffix
    counter = 2
    while candidate.casefold() in used:
        candidate = f"{stem}_{counter}{suffix}"
        counter += 1
    used.add(candidate.casefold())
    return candidate


def validate_upload_manifest(uploaded_files: Iterable[Any]) -> tuple[int, int]:
    """Reject an oversized upload set before reading file bodies into Python memory."""

    count = 0
    total_declared = 0
    for index, uploaded in enumerate(uploaded_files):
        count += 1
        if count > MAX_UPLOAD_FILES:
            raise InputValidationError(f"Можно загрузить не более {MAX_UPLOAD_FILES} файлов.")

        name = _safe_basename(getattr(uploaded, "name", f"upload_{index}"), f"upload_{index}")
        suffix = PurePosixPath(name).suffix.lower()
        if suffix not in ALLOWED_IMAGE_SUFFIXES | {".zip"}:
            raise InputValidationError(f"Неподдерживаемый файл «{name}».")

        declared = getattr(uploaded, "size", None)
        if declared is None:
            continue
        try:
            declared_size = int(declared)
        except (TypeError, ValueError, OverflowError) as exc:
            raise InputValidationError(f"Файл «{name}» имеет некорректный размер.") from exc
        if declared_size < 0:
            raise InputValidationError(f"Файл «{name}» имеет некорректный размер.")
        per_file_limit = MAX_ARCHIVE_BYTES if suffix == ".zip" else MAX_IMAGE_BYTES
        if declared_size > per_file_limit:
            raise InputValidationError(f"Файл «{name}» больше {per_file_limit // MIB} МБ.")
        total_declared += declared_size
        if total_declared > MAX_TOTAL_UPLOAD_BYTES:
            raise InputValidationError("Общий размер загрузки превышает безопасный лимит.")
    return count, total_declared


def collect_uploaded_images(uploaded_files: Iterable[Any]) -> list[dict[str, Any]]:
    """Прочитать прямые загрузки и ZIP без извлечения на диск."""

    files = list(uploaded_files)
    validate_upload_manifest(files)

    images: list[dict[str, Any]] = []
    used_names: set[str] = set()
    total_uploaded = 0
    total_raw_images = 0
    total_normalized = 0
    total_archive_members = 0

    def add_image(data: bytes, name: str) -> None:
        nonlocal total_raw_images, total_normalized
        if len(images) >= MAX_IMAGES:
            raise InputValidationError(f"Можно обработать не более {MAX_IMAGES} изображений.")
        total_raw_images += len(data)
        if total_raw_images > MAX_TOTAL_IMAGE_BYTES:
            raise InputValidationError("Общий исходный размер изображений превышает лимит.")
        normalized, normalized_name, mime = normalize_image(data, name)
        total_normalized += len(normalized)
        if total_normalized > MAX_TOTAL_IMAGE_BYTES:
            raise InputValidationError("Общий размер изображений превышает безопасный лимит.")
        unique_name = _unique_name(normalized_name, used_names)
        stamp = _PreparedImageStamp(normalized, unique_name, mime)
        images.append(
            {
                "data": normalized,
                "filename": unique_name,
                "mime": mime,
                "_prepared_image": stamp,
            }
        )

    for index, uploaded in enumerate(files):
        name = _safe_basename(getattr(uploaded, "name", f"upload_{index}"), f"upload_{index}")
        data = _read_all(uploaded)
        suffix = PurePosixPath(name).suffix.lower()
        total_uploaded += len(data)
        if total_uploaded > MAX_TOTAL_UPLOAD_BYTES:
            raise InputValidationError("Общий размер загрузки превышает безопасный лимит.")

        if suffix == ".zip":
            if len(data) > MAX_ARCHIVE_BYTES:
                raise InputValidationError(
                    f"ZIP-архив «{name}» больше {MAX_ARCHIVE_BYTES // MIB} МБ."
                )
            try:
                with zipfile.ZipFile(io.BytesIO(data)) as archive:
                    members = _validate_zip_metadata(
                        archive,
                        max_members=MAX_ARCHIVE_MEMBERS,
                        max_uncompressed_bytes=MAX_TOTAL_IMAGE_BYTES,
                        max_ratio=MAX_COMPRESSION_RATIO,
                    )
                    total_archive_members += len(members)
                    if total_archive_members > MAX_ARCHIVE_MEMBERS:
                        raise InputValidationError(
                            "Во всех ZIP-архивах суммарно слишком много объектов."
                        )
                    for member in members:
                        if member.is_dir():
                            continue
                        member_name = _safe_basename(member.filename, "image")
                        if PurePosixPath(member_name).suffix.lower() not in ALLOWED_IMAGE_SUFFIXES:
                            continue
                        if member.file_size > MAX_IMAGE_BYTES:
                            raise InputValidationError(
                                f"Файл «{member_name}» в ZIP больше {MAX_IMAGE_BYTES // MIB} МБ."
                            )
                        if total_raw_images + member.file_size > MAX_TOTAL_IMAGE_BYTES:
                            raise InputValidationError(
                                "Общий исходный размер изображений превышает лимит."
                            )
                        with archive.open(member) as source:
                            member_data = source.read(MAX_IMAGE_BYTES + 1)
                        if len(member_data) > MAX_IMAGE_BYTES:
                            raise InputValidationError(
                                f"Файл «{member_name}» в ZIP превышает лимит."
                            )
                        add_image(member_data, member_name)
            except InputValidationError:
                raise
            except (zipfile.BadZipFile, OSError) as exc:
                raise InputValidationError(f"ZIP-архив «{name}» повреждён.") from exc
        elif suffix in ALLOWED_IMAGE_SUFFIXES:
            if len(data) > MAX_IMAGE_BYTES:
                raise InputValidationError(
                    f"Изображение «{name}» больше {MAX_IMAGE_BYTES // MIB} МБ."
                )
            add_image(data, name)
        else:
            raise InputValidationError(f"Неподдерживаемый файл «{name}».")

    if not images:
        raise InputValidationError("В загрузке не найдено изображений JPEG или PNG.")
    return images

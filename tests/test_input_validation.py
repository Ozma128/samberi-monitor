from __future__ import annotations

import io
import zipfile

import pandas as pd
import pytest
from PIL import Image

import core.input_validation as input_validation
from core.input_validation import (
    MAX_ARCHIVE_BYTES,
    MAX_CATALOG_MEMORY_BYTES,
    MAX_IMAGE_BYTES,
    MAX_IMAGES,
    MAX_TOTAL_IMAGE_BYTES,
    MAX_TOTAL_PREVIEW_BYTES,
    MAX_TOTAL_UPLOAD_BYTES,
    MAX_UPLOAD_FILES,
    InputValidationError,
    collect_uploaded_images,
    create_image_preview,
    load_catalog_file,
    normalize_image,
    validate_upload_manifest,
)
from core.matcher import CatalogMatcher


class Upload(io.BytesIO):
    def __init__(self, data: bytes, name: str):
        super().__init__(data)
        self.name = name

    def getvalue(self) -> bytes:
        return super().getvalue()

    @property
    def size(self) -> int:
        return len(super().getvalue())


def _png(color: str = "white") -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (32, 24), color=color).save(buffer, format="PNG")
    return buffer.getvalue()


def test_streamlit_cloud_resource_limits() -> None:
    mib = 1024 * 1024
    assert MAX_UPLOAD_FILES == MAX_IMAGES == 50
    assert MAX_ARCHIVE_BYTES == MAX_TOTAL_UPLOAD_BYTES == MAX_TOTAL_IMAGE_BYTES == 75 * mib
    assert MAX_CATALOG_MEMORY_BYTES == 64 * mib
    assert MAX_TOTAL_PREVIEW_BYTES == 10 * mib


def test_catalog_csv_supports_semicolon_and_cp1251() -> None:
    raw = "sku;наименование;цена\n001;Молоко;1 234,56\n".encode("cp1251")
    frame = load_catalog_file(raw, "catalog.csv")
    assert len(frame) == 1
    assert frame.iloc[0, 0] == "001"
    assert CatalogMatcher(frame).catalog_records[0]["цена_продажи"] == pytest.approx(1234.56)


def test_catalog_tsv_supports_utf8_and_preserves_sku() -> None:
    raw = "sku\tнаименование\tцена\n0007\tКефир\t89,90\n".encode()
    frame = load_catalog_file(raw, "catalog.tsv")
    assert frame.iloc[0, 0] == "0007"
    assert CatalogMatcher(frame).catalog_records[0]["цена_продажи"] == pytest.approx(89.9)


def test_catalog_xlsx_and_extension_validation() -> None:
    buffer = io.BytesIO()
    pd.DataFrame([{"sku": "0001", "name": "Товар", "price": 10}]).to_excel(buffer, index=False)
    for suffix in (".xlsx", ".xlsm", ".xltx", ".xltm"):
        frame = load_catalog_file(buffer.getvalue(), f"catalog{suffix}")
        assert frame.iloc[0, 0] == "0001"

    with pytest.raises(InputValidationError, match="Поддерживаются"):
        load_catalog_file(b"data", "catalog.xml")


def _zip_bytes(files: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    return buffer.getvalue()


@pytest.mark.parametrize(
    ("suffix", "data", "expected_engine"),
    [
        (".xls", input_validation.OLE_COMPOUND_FILE_MAGIC + b"test", "xlrd"),
        (".xlt", b"\x09\x08test", "xlrd"),
        (
            ".xlsb",
            _zip_bytes({"[Content_Types].xml": b"types", "xl/workbook.bin": b"book"}),
            "calamine",
        ),
        (
            ".ods",
            _zip_bytes(
                {
                    "mimetype": b"application/vnd.oasis.opendocument.spreadsheet",
                    "content.xml": b"content",
                }
            ),
            "calamine",
        ),
    ],
)
def test_catalog_binary_formats_use_explicit_safe_engine(
    monkeypatch: pytest.MonkeyPatch,
    suffix: str,
    data: bytes,
    expected_engine: str,
) -> None:
    calls: list[dict[str, object]] = []

    def fake_read_excel(*_args: object, **kwargs: object) -> pd.DataFrame:
        calls.append(kwargs)
        return pd.DataFrame([{"sku": "0001", "name": "Товар", "price": "10"}])

    monkeypatch.setattr(pd, "read_excel", fake_read_excel)
    frame = load_catalog_file(data, f"catalog{suffix}")
    assert frame.iloc[0, 0] == "0001"
    assert calls == [
        {
            "engine": expected_engine,
            "nrows": input_validation.MAX_CATALOG_ROWS + 1,
            "dtype": str,
        }
    ]


@pytest.mark.parametrize(
    ("filename", "data"),
    [
        ("fake.xls", b"<html><table><tr><td>not excel</td></tr></table></html>"),
        ("fake.xlsx", _zip_bytes({"payload.txt": b"not excel"})),
        (
            "fake.xlsb",
            _zip_bytes({"[Content_Types].xml": b"types", "xl/workbook.xml": b"wrong"}),
        ),
        (
            "fake.ods",
            _zip_bytes({"mimetype": b"text/plain", "content.xml": b"wrong"}),
        ),
    ],
)
def test_catalog_rejects_mislabeled_spreadsheets(filename: str, data: bytes) -> None:
    with pytest.raises(InputValidationError, match="формат"):
        load_catalog_file(data, filename)


def test_catalog_zip_expansion_budget_applies_to_excel_variants(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(input_validation, "MAX_SPREADSHEET_UNCOMPRESSED_BYTES", 20)
    data = _zip_bytes(
        {
            "[Content_Types].xml": b"types",
            "xl/workbook.xml": b"0" * 50,
        }
    )
    with pytest.raises(InputValidationError, match="Распакованный размер"):
        load_catalog_file(data, "catalog.xlsm")


def test_catalog_cell_budget_is_enforced(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(input_validation, "MAX_CATALOG_CELLS", 3)
    raw = b"sku,name,price\n1,one,10\n2,two,20\n"
    with pytest.raises(InputValidationError, match="ячеек"):
        load_catalog_file(raw, "catalog.csv")


def test_image_is_verified_and_normalized_without_metadata() -> None:
    normalized, filename, mime = normalize_image(_png(), "../photo.png")
    assert filename == "photo.jpg"
    assert mime == "image/jpeg"
    assert Image.open(io.BytesIO(normalized)).format == "JPEG"
    with pytest.raises(InputValidationError):
        normalize_image(b"not-an-image", "fake.jpg")


def test_review_preview_is_bounded_jpeg() -> None:
    normalized, _, _ = normalize_image(_png(), "photo.png")
    preview = create_image_preview(normalized)
    with Image.open(io.BytesIO(preview)) as image:
        assert image.format == "JPEG"
        assert max(image.size) <= 1280


def test_zip_paths_and_duplicate_names_are_safe() -> None:
    archive_data = io.BytesIO()
    with zipfile.ZipFile(archive_data, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("../../shop/photo.png", _png("red"))
        archive.writestr("other/photo.png", _png("blue"))
    images = collect_uploaded_images([Upload(archive_data.getvalue(), "photos.zip")])
    assert [image["filename"] for image in images] == ["photo.jpg", "photo_2.jpg"]


def test_suspicious_zip_compression_ratio_is_rejected() -> None:
    archive_data = io.BytesIO()
    with zipfile.ZipFile(archive_data, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("bomb.jpg", b"0" * (2 * 1024 * 1024))
    with pytest.raises(InputValidationError, match="сжатия"):
        collect_uploaded_images([Upload(archive_data.getvalue(), "bomb.zip")])


class DeclaredUpload:
    def __init__(self, name: str, size: int):
        self.name = name
        self.size = size

    def getvalue(self) -> bytes:  # pragma: no cover - must be rejected before reading
        raise AssertionError("body must not be read")


def test_manifest_rejects_oversized_body_before_reading() -> None:
    with pytest.raises(InputValidationError, match="больше"):
        validate_upload_manifest([DeclaredUpload("huge.jpg", MAX_IMAGE_BYTES + 1)])


def test_manifest_limits_file_count_and_aggregate_size() -> None:
    too_many = [DeclaredUpload(f"{index}.jpg", 1) for index in range(MAX_UPLOAD_FILES + 1)]
    with pytest.raises(InputValidationError, match="не более"):
        validate_upload_manifest(too_many)

    aggregate = [
        DeclaredUpload(f"archive-{index}.zip", MAX_TOTAL_UPLOAD_BYTES // 3 + 1)
        for index in range(3)
    ]
    with pytest.raises(InputValidationError, match="Общий размер"):
        validate_upload_manifest(aggregate)

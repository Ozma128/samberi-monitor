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


def test_catalog_xlsx_and_extension_validation() -> None:
    buffer = io.BytesIO()
    pd.DataFrame([{"sku": "1", "name": "Товар", "price": 10}]).to_excel(buffer, index=False)
    assert len(load_catalog_file(buffer.getvalue(), "catalog.xlsx")) == 1
    with pytest.raises(InputValidationError, match="только"):
        load_catalog_file(b"data", "catalog.xls")


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

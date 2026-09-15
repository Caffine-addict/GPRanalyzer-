"""Tests for reference/library.py — the confirmed-hyperbola signature library."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from reference import library


@pytest.fixture(autouse=True)
def _clear_caches() -> None:
    library.load_manifest.cache_clear()
    library.load_image.cache_clear()
    yield
    library.load_manifest.cache_clear()
    library.load_image.cache_clear()


def test_the_library_holds_both_deliverable_sheets() -> None:
    crops = library.load_manifest()
    assert len(crops) == 16
    assert len({crop.sheet for crop in crops}) == 2


def test_crops_are_ordered_by_sheet_then_call_out() -> None:
    crops = library.load_manifest()
    assert [(c.sheet, c.callout) for c in crops] == sorted((c.sheet, c.callout) for c in crops)


def test_every_crop_file_actually_exists() -> None:
    for crop in library.load_manifest():
        assert crop.path.exists(), f"missing crop file for {crop.id}"


def test_no_crop_claims_a_class_or_depth_it_was_not_given() -> None:
    # The plans carry both, but the crop-to-call-out mapping is unconfirmed —
    # a gap left visible rather than guessed at.
    for crop in library.load_manifest():
        assert crop.label_class is None
        assert crop.label_depth_m is None


def test_every_crop_records_who_confirmed_it() -> None:
    for crop in library.load_manifest():
        assert crop.confirmed_by


def test_a_crop_decodes_to_a_greyscale_image() -> None:
    image = library.load_image(library.load_manifest()[0].id)
    assert image.ndim == 2
    assert image.shape[0] > 50 and image.shape[1] > 50


def test_crops_are_cached_between_calls() -> None:
    crop_id = library.load_manifest()[0].id
    assert library.load_image(crop_id) is library.load_image(crop_id)


def test_an_unknown_crop_id_raises() -> None:
    with pytest.raises(KeyError, match="unknown reference crop"):
        library.get_crop("sheet9-99")


def test_sheet_lookup_returns_the_original_deliverable_page() -> None:
    sheet = library.load_manifest()[0].sheet
    assert library.sheet_path(sheet).exists()


def test_an_unknown_sheet_raises() -> None:
    with pytest.raises(KeyError, match="unknown reference sheet"):
        library.sheet_path("not-a-sheet.jpg")


def test_a_missing_manifest_says_how_to_rebuild_it(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(library, "_MANIFEST_PATH", tmp_path / "gone.json")
    library.load_manifest.cache_clear()
    with pytest.raises(library.ReferenceLibraryError, match="extract_reference_hyperbolas"):
        library.load_manifest()


def test_a_malformed_manifest_raises_rather_than_degrading_to_empty(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Quietly returning no crops would look identical to "the library is fine
    # but empty", hiding a broken install.
    broken = tmp_path / "manifest.json"
    broken.write_text(json.dumps({"crops": [{"id": "x"}]}))
    monkeypatch.setattr(library, "_MANIFEST_PATH", broken)
    library.load_manifest.cache_clear()
    with pytest.raises(library.ReferenceLibraryError, match="malformed manifest entry"):
        library.load_manifest()


def test_manifest_that_is_not_json_raises(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    broken = tmp_path / "manifest.json"
    broken.write_text("{ not json")
    monkeypatch.setattr(library, "_MANIFEST_PATH", broken)
    library.load_manifest.cache_clear()
    with pytest.raises(library.ReferenceLibraryError, match="malformed reference manifest"):
        library.load_manifest()

"""Tests for studio/imports.py and its endpoints — getting files into the dataset from the UI.

Radar lines use real delivered SPR files as fixtures (a line has to actually parse to be
accepted, so a synthetic byte string would only test the rejection path). Everything writes into
a temp dataset directory; the real Dataset/ is only ever read.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from studio import imports, session
from studio.server import app

_REAL_JOB = Path("Dataset/DSU_GPR_Files/Job_0703")

pytestmark = pytest.mark.skipif(not _REAL_JOB.exists(), reason="SPR dataset not present")


def _line_files() -> list[tuple[str, bytes]]:
    return [(f"Single-01.{ext}", (_REAL_JOB / f"Single-01.{ext}").read_bytes()) for ext in ("RAD", "RA1", "RA2")]


@pytest.fixture
def dataset(tmp_path: Path) -> Path:
    root = tmp_path / "Dataset" / "DSU_GPR_Files"
    root.mkdir(parents=True)
    return root


# ------------------------------------------------------------------------------ radar lines


def test_an_imported_line_becomes_a_job_studio_can_open(dataset: Path) -> None:
    stored = imports.import_line(dataset, "Job_9001", _line_files())

    assert stored == ["Single-01.RA1", "Single-01.RA2", "Single-01.RAD"]
    assert session.list_jobs(dataset) == ["Job_9001"]
    channels = [c.extension for c in session.available_channels(dataset / "Job_9001")]
    assert channels == ["RAD", "RA1", "RA2"]


def test_file_names_are_normalised_by_extension_not_trusted(dataset: Path) -> None:
    # Operators rename exports; only the extension says what a file is.
    files = [("survey_line_7.rad", _line_files()[0][1]), ("track.GPS", b"$GPGGA,\n"), ("site.MAP", b"x")]
    stored = imports.import_line(dataset, "Job_9002", files)

    assert stored == ["Job_9002.map", "Single-01.RAD", "Single-01.gps"]
    assert (dataset / "Job_9002" / "Single-01.RAD").read_bytes() == files[0][1]


def test_a_file_that_is_not_a_real_spr_channel_is_refused_and_nothing_is_left_behind(dataset: Path) -> None:
    files = [*_line_files()[:2], ("Single-01.RA2", b"this is not a radar file")]
    with pytest.raises(ValueError, match="Single-01.RA2 could not be read"):
        imports.import_line(dataset, "Job_9003", files)

    # All-or-nothing: a half-imported line would open with a channel silently missing.
    assert not (dataset / "Job_9003").exists()
    assert list(dataset.parent.glob(".gpr-import-*")) == []


def test_a_line_needs_at_least_one_radar_channel(dataset: Path) -> None:
    with pytest.raises(ValueError, match="no radar channel"):
        imports.import_line(dataset, "Job_9004", [("Single-01.gps", b"$GPGGA,\n")])


def test_an_unrelated_file_type_is_refused_by_name(dataset: Path) -> None:
    with pytest.raises(ValueError, match="drawing.pdf"):
        imports.import_line(dataset, "Job_9005", [*_line_files(), ("drawing.pdf", b"%PDF")])


def test_two_files_for_the_same_channel_are_refused(dataset: Path) -> None:
    rad = _line_files()[0][1]
    with pytest.raises(ValueError, match="both map to Single-01.RAD"):
        imports.import_line(dataset, "Job_9006", [("a.RAD", rad), ("b.rad", rad)])


def test_staging_happens_beside_the_dataset_not_inside_it(dataset: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # list_jobs treats any folder holding a Single-01.R* file as a job — staging inside
    # dataset_dir would briefly show the half-written line as a job.
    seen_dirs = []
    real_mkdtemp = __import__("tempfile").mkdtemp

    def spy_mkdtemp(*args, **kwargs):
        seen_dirs.append(kwargs.get("dir"))
        return real_mkdtemp(*args, **kwargs)

    monkeypatch.setattr("tempfile.mkdtemp", spy_mkdtemp)
    imports.import_line(dataset, "Job_9050", _line_files())

    assert seen_dirs == [dataset.parent]


def test_an_existing_job_is_never_overwritten(dataset: Path) -> None:
    imports.import_line(dataset, "Job_9007", _line_files())
    with pytest.raises(ValueError, match="already exists"):
        imports.import_line(dataset, "Job_9007", _line_files())


@pytest.mark.parametrize("name", ["../escape", "a/b", "..", ".hidden", "", "has space"])
def test_an_unsafe_job_name_is_refused(dataset: Path, name: str) -> None:
    with pytest.raises(ValueError):
        imports.import_line(dataset, name, _line_files())
    assert list(dataset.iterdir()) == []


# ------------------------------------------------------------------------ reference drawings


def test_drawings_are_stored_outside_the_job_list(dataset: Path) -> None:
    stored = imports.import_references(dataset, [("site plan.pdf", b"%PDF-1.4"), ("layout.dwg", b"AC1027")])

    assert stored == ["layout.dwg", "site plan.pdf"]
    assert session.list_jobs(dataset) == []  # drawings are not radar lines
    assert [r["name"] for r in imports.list_references(dataset)] == ["layout.dwg", "site plan.pdf"]


def test_a_drawing_with_the_same_name_does_not_overwrite_the_first(dataset: Path) -> None:
    imports.import_references(dataset, [("plan.pdf", b"first")])
    stored = imports.import_references(dataset, [("plan.pdf", b"second")])

    assert stored == ["plan (1).pdf"]
    assert imports.reference_path(dataset, "plan.pdf").read_bytes() == b"first"


def test_a_client_supplied_path_cannot_escape_the_reference_folder(dataset: Path) -> None:
    stored = imports.import_references(dataset, [("../../evil.pdf", b"%PDF"), ("C:\\temp\\x.pdf", b"%PDF")])
    assert stored == ["evil.pdf", "x.pdf"]
    assert not (dataset.parent.parent / "evil.pdf").exists()


def test_a_drawing_suffix_is_matched_case_insensitively(dataset: Path) -> None:
    stored = imports.import_references(dataset, [("SCAN.PDF", b"%PDF")])
    assert stored == ["SCAN.PDF"]


def test_an_unsupported_drawing_type_is_refused(dataset: Path) -> None:
    with pytest.raises(ValueError, match="script.exe"):
        imports.import_references(dataset, [("script.exe", b"MZ")])


def test_serving_a_reference_only_reaches_files_that_are_listed(dataset: Path) -> None:
    imports.import_references(dataset, [("plan.pdf", b"%PDF")])
    with pytest.raises(FileNotFoundError):
        imports.reference_path(dataset, "../DSU_GPR_Files")


# ------------------------------------------------------------------------------ endpoints


@pytest.fixture
def client(dataset: Path) -> TestClient:
    app.state.dataset_dir = dataset
    yield TestClient(app)
    app.state.dataset_dir = session.DEFAULT_DATASET_DIR


def test_the_line_upload_endpoint_creates_an_openable_job(client: TestClient) -> None:
    files = [("files", (name, data)) for name, data in _line_files()]
    response = client.post("/api/import/line", data={"job": "Job_9101"}, files=files)

    assert response.status_code == 200, response.text
    assert "Job_9101" in client.get("/api/jobs").json()
    assert client.get("/api/jobs/Job_9101/channels/RAD/image.png").status_code == 200


def test_a_bad_line_upload_says_why(client: TestClient) -> None:
    response = client.post(
        "/api/import/line", data={"job": "Job_9102"}, files=[("files", ("notes.txt", b"hello"))]
    )
    assert response.status_code == 422
    assert "notes.txt" in response.json()["detail"]


def test_a_bad_reference_upload_says_why(client: TestClient) -> None:
    response = client.post("/api/import/references", files=[("files", ("script.exe", b"MZ"))])
    assert response.status_code == 422
    assert "script.exe" in response.json()["detail"]


def test_reference_upload_list_and_download(client: TestClient) -> None:
    response = client.post("/api/import/references", files=[("files", ("plan.pdf", b"%PDF-1.4 x"))])
    assert response.status_code == 200, response.text

    listing = client.get("/api/import/references").json()
    assert [item["name"] for item in listing] == ["plan.pdf"]

    served = client.get("/api/import/references/plan.pdf")
    assert served.status_code == 200
    assert served.content == b"%PDF-1.4 x"
    assert served.headers["content-disposition"].startswith("inline")
    assert client.get("/api/import/references/missing.pdf").status_code == 404



# ------------------------------------------------------------- review follow-ups (2026-09-29)


def test_a_channel_missing_a_required_header_field_is_refused_cleanly(dataset: Path) -> None:
    # parse_spr raises KeyError, not ValueError, for a missing field; that must still come back
    # as a worded refusal rather than a raw server error.
    rad = _line_files()[0][1].replace(b"SPR_SAMPLES_PER_SCAN", b"SPR_XAMPLES_PER_SCAN", 1)
    with pytest.raises(ValueError, match="could not be read as an SPR channel"):
        imports.import_line(dataset, "Job_9201", [("Single-01.RAD", rad)])
    assert not (dataset / "Job_9201").exists()


def test_a_job_created_between_the_check_and_the_move_is_reported_as_taken(
    dataset: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Another import can take the name while this one is still parsing channels.
    real_parse = imports.parse_spr

    def parse_then_collide(path: Path):  # type: ignore[no-untyped-def]
        result = real_parse(path)
        (dataset / "Job_9202").mkdir(exist_ok=True)
        (dataset / "Job_9202" / "Single-01.RAD").write_bytes(b"theirs")
        return result

    monkeypatch.setattr(imports, "parse_spr", parse_then_collide)
    with pytest.raises(ValueError, match="already exists"):
        imports.import_line(dataset, "Job_9202", _line_files()[:1])
    assert (dataset / "Job_9202" / "Single-01.RAD").read_bytes() == b"theirs"


def test_a_cross_site_page_cannot_write_to_the_studio(client: TestClient) -> None:
    # A form or fetch POST from another origin skips CORS preflight entirely, so without this any
    # page open in the operator's browser could plant jobs or drawings on a loopback server.
    files = [("files", ("plan.pdf", b"%PDF"))]
    assert client.post("/api/import/references", files=files,
                       headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403
    assert client.post("/api/import/references", files=files,
                       headers={"Origin": "https://evil.example"}).status_code == 403
    assert client.get("/api/import/references").json() == []


def test_the_studios_own_page_can_still_write(client: TestClient) -> None:
    files = [("files", ("plan.pdf", b"%PDF"))]
    response = client.post("/api/import/references", files=files,
                           headers={"Sec-Fetch-Site": "same-origin", "Origin": "http://testserver"})
    assert response.status_code == 200, response.text


def test_reads_are_not_affected_by_the_cross_site_check(client: TestClient) -> None:
    assert client.get("/api/jobs", headers={"Sec-Fetch-Site": "cross-site"}).status_code == 200


# ------------------------------------- documents already in the dataset folder (2026-09-30)


def _seed_documents(dataset: Path) -> None:
    imports.import_line(dataset, "Job_9301", _line_files()[:1])
    site = dataset / "GPR_24AUG2026" / "Sky group" / "Ulsoor Road"
    site.mkdir(parents=True)
    for name in ("10.pdf", "2.pdf", "1.pdf", "Ulsoor Road.dwg"):
        (site / name).write_bytes(b"%PDF" if name.endswith(".pdf") else b"AC1027")
    (site / ".DS_Store").write_bytes(b"x")
    (site / "notes.exe").write_bytes(b"MZ")
    (dataset / "GPR_24AUG2026.zip").write_bytes(b"PK")


def test_documents_in_the_dataset_are_found_without_uploading(dataset: Path) -> None:
    _seed_documents(dataset)
    paths = [d["path"] for d in imports.list_documents(dataset)]

    assert paths == [
        "GPR_24AUG2026.zip",
        "GPR_24AUG2026/Sky group/Ulsoor Road/1.pdf",
        "GPR_24AUG2026/Sky group/Ulsoor Road/2.pdf",
        "GPR_24AUG2026/Sky group/Ulsoor Road/10.pdf",  # natural order: 10 after 2, as a reader expects
        "GPR_24AUG2026/Sky group/Ulsoor Road/Ulsoor Road.dwg",
    ]


def test_radar_job_folders_hidden_files_and_other_types_are_not_listed(dataset: Path) -> None:
    _seed_documents(dataset)
    paths = [d["path"] for d in imports.list_documents(dataset)]
    assert not any(p.startswith("Job_9301") for p in paths)
    assert not any(p.endswith((".DS_Store", ".exe")) for p in paths)


def test_a_document_is_served_only_if_it_is_listed(dataset: Path) -> None:
    _seed_documents(dataset)
    assert imports.document_path(dataset, "GPR_24AUG2026/Sky group/Ulsoor Road/1.pdf").read_bytes() == b"%PDF"
    for sneaky in ("../DSU_GPR_Files", "Job_9301/Single-01.RAD", "GPR_24AUG2026/Sky group/Ulsoor Road/notes.exe",
                   "/etc/passwd", "GPR_24AUG2026/../Job_9301/Single-01.RAD"):
        with pytest.raises(FileNotFoundError):
            imports.document_path(dataset, sneaky)


def test_the_documents_endpoints_list_and_open_a_drawing(client: TestClient, dataset: Path) -> None:
    _seed_documents(dataset)
    listing = client.get("/api/documents").json()
    assert "GPR_24AUG2026/Sky group/Ulsoor Road/1.pdf" in [d["path"] for d in listing]

    served = client.get("/api/documents/GPR_24AUG2026/Sky group/Ulsoor Road/1.pdf")
    assert served.status_code == 200
    assert served.content == b"%PDF"
    assert served.headers["content-disposition"].startswith("inline")
    assert client.get("/api/documents/Job_9301/Single-01.RAD").status_code == 404

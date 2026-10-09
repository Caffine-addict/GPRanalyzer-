"""studio/verify.py: verifying picker targets into training labels, against a real Postgres."""

from __future__ import annotations

import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from store.auth_store import AuthStore
from store.label_store import LabelStore
from studio import imports, verify
from studio.server import app
from tests.pg import fresh_url

sys.path.insert(0, str(Path(__file__).parent))
from test_studio_site_map import _line_with_gps


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[TestClient]:
    monkeypatch.chdir(tmp_path)
    dataset = tmp_path / "Lines"
    dataset.mkdir()
    for k in range(2):
        imports.import_line(dataset, f"Site_L{k}", _line_with_gps(east_m=1.0 * k, pipe_north_m=4.0))
    imports.import_line(dataset, "Held_L0", _line_with_gps(east_m=0.0, pipe_north_m=4.0))
    url = fresh_url()
    users = AuthStore(url)
    users.create_account("tester", "not-a-real-hash")  # user 1: the signed-in test user (tests/conftest.py)
    labels = LabelStore(url)
    monkeypatch.setattr(verify, "TEST_SITES", frozenset({"Held"}))
    monkeypatch.setattr(app.state, "dataset_dir", dataset, raising=False)
    monkeypatch.setattr(app.state, "labels", labels, raising=False)
    yield TestClient(app)
    labels.close()
    users.close()


def test_the_queue_offers_each_targets_place_on_every_channel(client: TestClient) -> None:
    assert client.get("/api/verify/sites").json() == {"sites": [{"site": "Site", "lines": 2}], "held_out": ["Held"]}
    items = client.get("/api/verify/queue", params={"site": "Site"}).json()["items"]
    assert items and {i["job"] for i in items} <= {"Site_L0", "Site_L1"}
    pipe = min(items, key=lambda i: abs(i["chainage_m"] - 4.0))
    assert pipe["chainage_m"] == pytest.approx(4.0, abs=0.1) and pipe["decision"] is None
    assert pipe["channels"] and all(c["mark_ns"] > pipe["time_ns"] for c in pipe["channels"])  # time zero added back


def test_a_decision_is_kept_under_the_signed_in_name_and_the_latest_one_counts(client: TestClient) -> None:
    item = client.get("/api/verify/queue", params={"site": "Site"}).json()["items"][0]
    body = {"job": item["job"], "trace": item["trace"], "time_ns": item["time_ns"]}
    assert client.post("/api/verify/labels", json=body | {"decision": "yes"}).status_code == 200
    assert client.post("/api/verify/labels", json=body | {"decision": "no"}).status_code == 200  # changed mind
    again = next(i for i in client.get("/api/verify/queue", params={"site": "Site"}).json()["items"]
                 if (i["job"], i["trace"], i["time_ns"]) == (item["job"], item["trace"], item["time_ns"]))
    assert (again["decision"], again["decided_by"]) == ("no", "tester")
    assert client.get("/api/verify/progress").json() == {"yes": 0, "no": 1, "unsure": 0, "goal": verify.GOAL}


def test_the_held_out_test_site_is_never_offered_nor_accepted(client: TestClient) -> None:
    assert client.get("/api/verify/queue", params={"site": "Held"}).status_code == 422
    assert client.post("/api/verify/labels", json={"job": "Held_L0", "trace": 160, "time_ns": 8.0,
                                                   "decision": "yes"}).status_code == 422


@pytest.mark.parametrize("change", [{"decision": "maybe"}, {"trace": 3, "time_ns": 0.5}, {"trace": "x"}])
def test_bad_labels_are_refused(client: TestClient, change: dict) -> None:
    item = client.get("/api/verify/queue", params={"site": "Site"}).json()["items"][0]
    body = {"job": item["job"], "trace": item["trace"], "time_ns": item["time_ns"], "decision": "yes"} | change
    assert client.post("/api/verify/labels", json=body).status_code == 422

"""Tests for studio/assistant.py, studio/reviews.py and studio/assistant_routes.py.

The model is always a fake: these tests pin what the Studio shows it, what it keeps of the
reply, and what a supervisor can do with the claims — never what a real model would say.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pytest
from fastapi.testclient import TestClient

from studio import assistant
from studio import picks as pick_store
from studio import reviews as review_store
from studio.candidates import Candidate
from studio.picks import Pick
from studio.server import app
from studio.session import ChannelInfo

INFO = ChannelInfo(extension="RAD", label="RAD", n_traces=400, n_samples=256, trace_spacing_m=0.025,
                   sample_interval_ns=0.1, dielectric_assumed=9.0, line_length_m=10.0, time_window_ns=25.6,
                   max_depth_m=1.28)


def candidate(cid: str, x: float, y: float = 60, channel: str = "RAD") -> Candidate:
    return Candidate(id=cid, channel=channel, x=x, y=y, w=20, h=30, note="", suggested_class="clear_point_reflector",
                     shape="point", rationale="", fit_r2=0.97, implied_dielectric=8.5)


def pick(pid: str, trace: float, label: str = "") -> Pick:
    return Pick(id=pid, channel="RAD", trace=trace, sample=80, time_ns=8.0, depth_m=0.4, velocity_m_per_ns=0.1,
                velocity_source="fitted", dielectric=9.0, label=label, note="", fit_r2=0.98,
                created_at="2026-09-30T00:00:00")


TRACES = np.random.default_rng(0).normal(0, 1, (400, 256))


class FakeClient:
    """Answers with a canned reply, or raises for the models listed in `failing`."""

    def __init__(self, reply: dict[str, Any] | None = None, failing: tuple[str, ...] = ()) -> None:
        self.reply = reply or {"answer": "ok", "mentions": [], "next_steps": []}
        self.failing = failing
        self.prompts: list[str] = []
        self.models: list[str] = []

    def complete_json(self, prompt: str, **kwargs: Any) -> dict[str, Any]:
        self.prompts.append(prompt)
        self.models.append(kwargs["model"])
        if kwargs["model"] in self.failing:
            raise RuntimeError("rate limited")
        return self.reply


def mention(target_id: str, material: str = "metallic") -> dict[str, str]:
    return {"target_id": target_id, "identity": "metallic pipe or cable", "material": material,
            "confidence": "medium", "why": "reversed polarity, strong ringing"}


# --- context --------------------------------------------------------------------------------------

def test_targets_get_short_labels_in_order_along_the_line_and_keep_their_real_id() -> None:
    targets = assistant.line_targets([candidate("ffe1e669", 300), candidate("c618a529", 100)],
                                     [pick("aa11bb22", 200)], INFO, TRACES)
    assert [(t["id"], t["ref"]) for t in targets] == [("C1", "c618a529"), ("P1", "aa11bb22"), ("C2", "ffe1e669")]


def test_other_channels_targets_are_left_out() -> None:
    targets = assistant.line_targets([candidate("a", 100), candidate("b", 200, channel="RA1")], [], INFO, TRACES)
    assert [t["ref"] for t in targets] == ["a"]


def test_a_candidates_depth_comes_from_the_header_dielectric_and_says_so() -> None:
    (t,) = assistant.line_targets([candidate("a", 100, y=100)], [], INFO, TRACES)
    # v = 0.2998 / 3 = 0.0999 m/ns; t = 100 * 0.1 ns = 10 ns two-way -> 0.50 m
    assert (t["depth_m"], t["depth_basis"]) == (0.5, "header dielectric (assumed)")


def test_no_header_dielectric_means_no_depth_rather_than_a_guess() -> None:
    info = ChannelInfo(**{**INFO.__dict__, "dielectric_assumed": None})
    (t,) = assistant.line_targets([candidate("a", 100)], [], info, TRACES)
    assert t["depth_m"] is None


def test_a_picks_free_text_label_never_reaches_the_model() -> None:
    targets = assistant.line_targets([], [pick("p1", 200, label="gas main")], INFO, TRACES)
    prompt, context_text = assistant.build_prompt("chat", assistant.line_context("J", INFO, targets, None), "what is P1?")
    assert "gas main" not in prompt and "gas main" not in context_text


def test_every_layer_puts_its_instructions_and_the_context_in_the_prompt() -> None:
    context = assistant.line_context("Job_1", INFO, [], None)
    for layer in assistant.LAYERS:
        prompt, context_text = assistant.build_prompt(layer, context)
        assert assistant.LAYER_INSTRUCTIONS[layer] in prompt and context_text in prompt


def test_an_unknown_layer_is_refused() -> None:
    with pytest.raises(ValueError, match="unknown layer"):
        assistant.build_prompt("poem", {})


def test_relative_amplitude_is_amplitude_over_the_median_not_scaled_by_it() -> None:
    (t,) = assistant.line_targets([candidate("a", 100)], [], INFO, TRACES)
    raw_amplitude = assistant._amplitude(TRACES, t["x"], t["y"], t["w"], t["h"])
    # one target: it is its own median, so amplitude / typical must be exactly 1.0.
    # amplitude * typical would instead be raw_amplitude**2, which is not 1.0 here.
    assert raw_amplitude != 1.0
    assert t["relative_amplitude"] == 1.0


def test_field_notes_are_in_the_context_the_model_sees() -> None:
    context = assistant.line_context("Job_1", INFO, [], None)
    assert context["field_notes"] == assistant.FIELD_NOTES
    assert "implied_dielectric" in context["field_notes"]


def test_vendor_priors_use_only_text_layer_rows(tmp_path: Path) -> None:
    path = tmp_path / "sue.csv"
    path.write_text("utility,depth_m,read_by\nUC,0.5,text\nUC,0.7,text\nUC,9.0,ocr\nWATER,,text\n")
    priors = assistant.vendor_priors(path)
    assert priors is not None and priors["by_type"] == {"UC": {"n": 2, "median_m": 0.6, "p10_m": 0.52, "p90_m": 0.68}}


# --- the call -------------------------------------------------------------------------------------

def _targets() -> list[dict[str, Any]]:
    return assistant.line_targets([candidate("c618a529", 100)], [], INFO, TRACES)


def test_a_claim_about_a_real_target_is_joined_to_its_box() -> None:
    reply = assistant.run(FakeClient({"answer": "a", "mentions": [mention("C1")], "next_steps": ["dig"]}),
                          ["m"], "what_is_what", {}, _targets())
    (m,) = reply.mentions
    assert (m["target_ref"], m["target_kind"], m["x"], m["w"]) == ("c618a529", "candidate", 100, 20)
    assert reply.next_steps == ["dig"] and reply.error is None


def test_a_claim_about_a_target_that_does_not_exist_is_dropped_not_drawn() -> None:
    reply = assistant.run(FakeClient({"answer": "a", "mentions": [mention("C9"), mention("C1")], "next_steps": []}),
                          ["m"], "chat", {}, _targets())
    assert [m["target_id"] for m in reply.mentions] == ["C1"] and reply.dropped == ["C9: no such target on this line"]


def test_a_claim_with_a_material_outside_the_vocabulary_is_dropped() -> None:
    reply = assistant.run(FakeClient({"answer": "a", "mentions": [mention("C1", "copper")], "next_steps": []}),
                          ["m"], "chat", {}, _targets())
    assert reply.mentions == [] and reply.dropped == ["C1: material 'copper' is not one the Studio records"]


def test_a_failing_model_falls_back_to_the_next_one() -> None:
    client = FakeClient(failing=("big",))
    reply = assistant.run(client, ["big", "small"], "chat", {}, [])
    assert client.models == ["big", "small"] and reply.model == "small" and reply.error is None


def test_when_every_model_fails_the_reply_says_so_instead_of_raising() -> None:
    reply = assistant.run(FakeClient(failing=("a", "b")), ["a", "b"], "chat", {}, [])
    assert reply.error is not None and "rate limited" in reply.error and reply.answer == ""


def test_the_fingerprint_changes_with_layer_question_and_history() -> None:
    base = assistant.fingerprint("chat", "ctx", "what is C1?", [{"role": "user", "text": "hi"}])
    assert base != assistant.fingerprint("line_summary", "ctx", "what is C1?", [{"role": "user", "text": "hi"}])
    assert base != assistant.fingerprint("chat", "ctx", "what is C2?", [{"role": "user", "text": "hi"}])
    assert base != assistant.fingerprint("chat", "ctx", "what is C1?", [{"role": "user", "text": "bye"}])
    assert base == assistant.fingerprint("chat", "ctx", "what is C1?", [{"role": "user", "text": "hi"}])


# --- reviews --------------------------------------------------------------------------------------

@pytest.fixture
def reviews_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(review_store, "_ANNOTATIONS_ROOT", tmp_path / "annotations")
    return tmp_path


def claim(target: str = "c618a529", identity: str = "metallic pipe") -> dict[str, Any]:
    return {"channel": "RAD", "target_id": target, "target_kind": "candidate", "x": 1.0, "y": 2.0, "w": 3.0,
            "h": 4.0, "identity": identity, "material": "metallic", "confidence": "low", "why": "w",
            "layer": "chat", "model": "m"}


def test_a_repeated_claim_is_not_stored_twice(reviews_root: Path) -> None:
    first = review_store.propose("Job_1", [claim()])
    second = review_store.propose("Job_1", [claim(identity="Metallic Pipe")])
    assert [r.id for r in first] == [r.id for r in second] and len(review_store.load_reviews("Job_1")) == 1


def test_a_decision_needs_a_reviewer_and_is_kept(reviews_root: Path) -> None:
    (review,) = review_store.propose("Job_1", [claim()])
    with pytest.raises(ValueError, match="reviewer"):
        review_store.decide("Job_1", review.id, "confirmed", "  ")
    decided = review_store.decide("Job_1", review.id, "confirmed", "A. Supervisor", "dug and seen")
    assert review_store.load_reviews("Job_1") == [decided]
    assert (decided.status, decided.reviewer, decided.note) == ("confirmed", "A. Supervisor", "dug and seen")


def test_only_confirm_or_reject_are_decisions(reviews_root: Path) -> None:
    (review,) = review_store.propose("Job_1", [claim()])
    with pytest.raises(ValueError, match="status"):
        review_store.decide("Job_1", review.id, "proposed", "A")
    with pytest.raises(KeyError):
        review_store.decide("Job_1", "R0000", "confirmed", "A")


def test_to_csv_rows_column_order(reviews_root: Path) -> None:
    (review,) = review_store.propose("Job_1", [claim(identity="metallic pipe")])
    header, row = review_store.to_csv_rows([review])
    assert header[:5] == ["review", "channel", "target", "identity", "material"]
    assert row[:5] == [review.id, "RAD", "c618a529", "metallic pipe", "metallic"]


def test_a_new_claim_on_a_decided_target_waits_for_its_own_decision(reviews_root: Path) -> None:
    (review,) = review_store.propose("Job_1", [claim()])
    review_store.decide("Job_1", review.id, "rejected", "A")
    (again,) = review_store.propose("Job_1", [claim()])
    assert again.id != review.id and again.status == "proposed"


# --- endpoints ------------------------------------------------------------------------------------

_DATASET = Path("Dataset/DSU_GPR_Files")
needs_data = pytest.mark.skipif(not _DATASET.exists(), reason="SPR dataset not present")
# A delivered line that has detector candidates. Pinned by name: the first job alphabetically can
# be a line someone imported later, with no candidates for the model to point at.
_JOB = "Job_0696"


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(pick_store, "_ANNOTATIONS_ROOT", tmp_path / "annotations")
    monkeypatch.setattr(review_store, "_ANNOTATIONS_ROOT", tmp_path / "annotations")
    app.state.dataset_dir = _DATASET
    app.state.assistant_cache = {}
    return TestClient(app)


@pytest.fixture
def fake(client: TestClient) -> Any:
    fake_client = FakeClient({"answer": "C1 looks metallic", "mentions": [mention("C1")], "next_steps": []})
    app.state.assistant_client = fake_client
    yield fake_client
    del app.state.assistant_client


@needs_data
def test_asking_about_a_line_proposes_a_review_on_the_named_target(client: TestClient, fake: FakeClient) -> None:
    job = _JOB
    body = client.post(f"/api/jobs/{job}/channels/RAD/assistant", json={"layer": "what_is_what"}).json()
    (review,) = body["reviews"]
    assert review["label"] == "C1" and review["status"] == "proposed" and review["layer"] == "what_is_what"
    assert body["context_text"] in fake.prompts[0]
    listed = client.get(f"/api/jobs/{job}/reviews").json()
    assert [r["id"] for r in listed] == [review["id"]]


@needs_data
def test_an_identical_question_is_answered_from_cache(client: TestClient, fake: FakeClient) -> None:
    job = _JOB
    url = f"/api/jobs/{job}/channels/RAD/assistant"
    client.post(url, json={"layer": "chat", "question": "what is C1?"})
    again = client.post(url, json={"layer": "chat", "question": "what is C1?"}).json()
    assert again["cached"] is True and len(fake.prompts) == 1


@needs_data
def test_chat_without_a_question_and_unknown_layers_are_refused(client: TestClient, fake: FakeClient) -> None:
    job = _JOB
    url = f"/api/jobs/{job}/channels/RAD/assistant"
    assert client.post(url, json={"layer": "chat", "question": " "}).status_code == 422
    assert client.post(url, json={"layer": "briefing"}).status_code == 422


@needs_data
def test_a_supervisor_decision_is_recorded_and_exported(client: TestClient, fake: FakeClient) -> None:
    job = _JOB
    (review,) = client.post(f"/api/jobs/{job}/channels/RAD/assistant", json={"layer": "live"}).json()["reviews"]
    url = f"/api/jobs/{job}/reviews/{review['id']}/decision"
    assert client.post(url, json={"status": "confirmed"}).status_code == 422  # no reviewer
    decided = client.post(url, json={"status": "confirmed", "reviewer": "S. Lead"}).json()
    assert decided["status"] == "confirmed"
    csv_text = client.get(f"/api/jobs/{job}/reviews.csv").text
    assert "S. Lead" in csv_text and "confirmed" in csv_text


@needs_data
def test_the_briefing_never_proposes_a_review(client: TestClient, fake: FakeClient) -> None:
    body = client.post("/api/assistant/briefing").json()
    assert body["mentions"] == [] and body["dropped"] == ["C1: no such target on this line"] and "radar_lines" in body["context_text"]


@needs_data
def test_without_a_key_the_studio_says_what_is_missing(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.setattr("studio.assistant_routes.load_dotenv", lambda: None)
    monkeypatch.setattr(app.state, "assistant_client", None, raising=False)
    job = _JOB
    response = client.post(f"/api/jobs/{job}/channels/RAD/assistant", json={"layer": "live"})
    assert response.status_code == 503 and "GROQ_API_KEY" in response.json()["detail"]


def test_a_material_claim_is_capped_at_low_confidence_and_says_why() -> None:
    reply = assistant.run(FakeClient({"answer": "a", "mentions": [{**mention("C1"), "confidence": "high"}],
                                      "next_steps": []}), ["m"], "live", {}, _targets())
    (m,) = reply.mentions
    assert m["confidence"] == "low" and assistant.MATERIAL_CAP_NOTE in m["why"]


def test_saying_a_target_is_not_a_utility_keeps_its_confidence() -> None:
    claim_ = {**mention("C1", "not a utility"), "confidence": "high"}
    reply = assistant.run(FakeClient({"answer": "a", "mentions": [claim_], "next_steps": []}), ["m"], "live", {},
                          _targets())
    assert reply.mentions[0]["confidence"] == "high"


# --- reports --------------------------------------------------------------------------------------

def _pdf_text(content: bytes) -> str:
    import io

    from pypdf import PdfReader

    return "\n".join(page.extract_text() for page in PdfReader(io.BytesIO(content)).pages)


def test_the_review_report_says_who_confirmed_what_and_survives_markup_in_claims(reviews_root: Path) -> None:
    from studio import review_report

    (_proposed, confirmed) = review_store.propose("Job_1", [claim("a", "pipe <b>bold</b> & co"), claim("b", "duct")])
    review_store.decide("Job_1", confirmed.id, "confirmed", "S. Lead", "seen in trench")
    rgb = np.zeros((256, 400, 3), np.uint8)
    text = _pdf_text(review_report.review_report_pdf("Job_1", INFO, rgb, review_store.load_reviews("Job_1")))
    assert "Only rows marked CONFIRMED were checked by a named person" in text
    assert "pipe <b>bold</b> & co" in text  # printed literally, not parsed as markup
    assert "S. Lead" in text and "seen in trench" in text and "1 confirmed" in text and "1 still waiting" in text


def test_status_colours_match_the_reports_amber_green_red_legend() -> None:
    from studio import review_report

    # pinned to literal RGB, not the module's own dict, so a status/colour swap is caught.
    assert review_report.STATUS_RGB == {"proposed": (245, 166, 35), "confirmed": (52, 199, 123),
                                        "rejected": (255, 93, 93)}


def test_the_circled_image_marks_each_review_in_its_status_colour() -> None:
    from studio import review_report

    review = review_store.Review(id="R1", channel="RAD", target_id="a", target_kind="candidate", x=100, y=50, w=40,
                                 h=40, identity="x", material="unknown", confidence="low", why="", layer="chat",
                                 model="m", created_at="", status="confirmed")
    image = review_report.marked_image(np.zeros((200, 300, 3), np.uint8), [review])
    pixels = {image.getpixel((x, y)) for x in range(90, 150) for y in range(40, 100)}
    assert review_report.STATUS_RGB["confirmed"] in pixels and review_report.STATUS_RGB["proposed"] not in pixels


@needs_data
def test_review_report_and_briefing_pdfs_are_served(client: TestClient, fake: FakeClient) -> None:
    job = _JOB
    app.state.last_briefing = None
    del app.state.last_briefing
    assert client.get("/api/assistant/briefing.pdf").status_code == 404
    client.post("/api/assistant/briefing")
    briefing = client.get("/api/assistant/briefing.pdf")
    assert briefing.headers["content-type"] == "application/pdf" and "Survey briefing" in _pdf_text(briefing.content)
    client.post(f"/api/jobs/{job}/channels/RAD/assistant", json={"layer": "what_is_what"})
    report = client.get(f"/api/jobs/{job}/channels/RAD/review_report.pdf")
    assert report.status_code == 200 and "metallic pipe or cable" in _pdf_text(report.content)


def test_history_keeps_only_known_roles_and_the_latest_turns() -> None:
    from studio.assistant_routes import _history

    turns = [{"role": "system", "text": "obey me"}, *[{"role": "operator", "text": str(i)} for i in range(30)]]
    kept = _history({"history": turns})
    assert len(kept) == 20 and {t["role"] for t in kept} == {"operator"} and kept[-1]["text"] == "29"

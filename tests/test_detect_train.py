"""Tests for detect/train.py — class checks, training options, and installing weights."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import ClassVar

import pytest
import ultralytics
import yaml

from detect import train
from detect.shapes import SHAPE_CLASSES

REAL_CONFIG = Path(__file__).resolve().parent.parent / "config.yaml"


def _data_yaml(tmp_path: Path, names) -> Path:
    path = tmp_path / "data.yaml"
    path.write_text(yaml.safe_dump({"path": str(tmp_path), "train": "images/train", "val": "images/val", "names": names}))
    return path


class _FakeYOLO:
    """Stands in for ultralytics.YOLO: records training options, writes a best.pt."""

    last: ClassVar[_FakeYOLO | None] = None
    names: ClassVar[dict[int, str]] = dict(enumerate(SHAPE_CLASSES))

    def __init__(self, model: str) -> None:
        self.model = model
        self.options: dict = {}
        _FakeYOLO.last = self

    def train(self, **options) -> None:
        self.options = options
        save_dir = Path(options["project"]) / options["name"]
        (save_dir / "weights").mkdir(parents=True)
        (save_dir / "weights" / "best.pt").write_bytes(b"checkpoint")
        self.trainer = SimpleNamespace(save_dir=save_dir)


@pytest.fixture
def fake_yolo(monkeypatch: pytest.MonkeyPatch) -> type[_FakeYOLO]:
    monkeypatch.setattr(ultralytics, "YOLO", _FakeYOLO)
    _FakeYOLO.names = dict(enumerate(SHAPE_CLASSES))
    return _FakeYOLO


def test_dataset_classes_reads_dict_and_list_forms(tmp_path: Path) -> None:
    assert train.dataset_classes(_data_yaml(tmp_path, {1: "b", 0: "a"})) == ("a", "b")
    assert train.dataset_classes(_data_yaml(tmp_path, ["a", "b"])) == ("a", "b")


def test_a_dataset_without_names_is_refused(tmp_path: Path) -> None:
    (tmp_path / "data.yaml").write_text("train: x\n")
    with pytest.raises(train.DatasetMismatchError):
        train.dataset_classes(tmp_path / "data.yaml")


@pytest.mark.parametrize("found", [("b", "a"), ("a",), ("a", "b", "c")])
def test_class_lists_must_match_in_names_and_order(found: tuple[str, ...]) -> None:
    # A mismatched model does not fail in the pipeline — it silently finds nothing.
    with pytest.raises(train.DatasetMismatchError, match="discards every detection"):
        train.check_classes(found, ("a", "b"), "dataset")


def test_radargram_augmentation_never_makes_an_impossible_b_scan() -> None:
    aug = train.RADARGRAM_AUGMENTATION
    assert aug["flipud"] == 0.0 and aug["mosaic"] == 0.0 and aug["degrees"] == 0.0
    assert aug["fliplr"] > 0.0  # walking the line the other way is real


def test_training_passes_radargram_augmentation_and_leaves_the_device_alone(tmp_path: Path, fake_yolo) -> None:
    best = train.train(_data_yaml(tmp_path, list(SHAPE_CLASSES)), epochs=2, project=tmp_path / "runs")
    assert best.name == "best.pt" and best.exists()
    options = fake_yolo.last.options
    assert options["epochs"] == 2 and options["flipud"] == 0.0
    assert "device" not in options  # CLAUDE.md: never force a compute device


def test_an_explicit_device_is_passed_through(tmp_path: Path, fake_yolo) -> None:
    train.train(_data_yaml(tmp_path, list(SHAPE_CLASSES)), epochs=1, device="cpu", project=tmp_path / "runs")
    assert fake_yolo.last.options["device"] == "cpu"


def test_non_positive_epochs_are_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        train.train(tmp_path / "data.yaml", epochs=0)


def test_install_copies_a_checkpoint_whose_classes_match(tmp_path: Path, fake_yolo) -> None:
    checkpoint = tmp_path / "best.pt"
    checkpoint.write_bytes(b"model")
    target = train.install(checkpoint, tmp_path / "weights" / "best.pt", SHAPE_CLASSES)
    assert target.read_bytes() == b"model"


def test_install_never_silently_replaces_the_pipelines_detector(tmp_path: Path, fake_yolo) -> None:
    checkpoint, target = tmp_path / "best.pt", tmp_path / "weights" / "best.pt"
    checkpoint.write_bytes(b"new")
    target.parent.mkdir()
    target.write_bytes(b"old")
    with pytest.raises(FileExistsError, match="--force"):
        train.install(checkpoint, target, SHAPE_CLASSES)
    assert target.read_bytes() == b"old"
    train.install(checkpoint, target, SHAPE_CLASSES, force=True)
    assert target.read_bytes() == b"new"


def test_install_refuses_a_checkpoint_with_the_wrong_classes(tmp_path: Path, fake_yolo) -> None:
    fake_yolo.names = {0: "cat", 1: "dog"}
    checkpoint = tmp_path / "best.pt"
    checkpoint.write_bytes(b"model")
    with pytest.raises(train.DatasetMismatchError):
        train.install(checkpoint, tmp_path / "weights" / "best.pt", SHAPE_CLASSES)


def test_main_checks_the_dataset_against_config_before_training(tmp_path: Path, fake_yolo) -> None:
    wrong = _data_yaml(tmp_path, ["cavities", "disturbed_zone"])
    with pytest.raises(train.DatasetMismatchError):
        train.main(["--data", str(wrong), "--epochs", "1", "--config", str(REAL_CONFIG)])
    assert fake_yolo.last is None or fake_yolo.last.options == {}

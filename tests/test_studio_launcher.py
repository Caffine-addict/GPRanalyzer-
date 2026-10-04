"""Tests for studio/launcher.py — running the Studio as an installed app."""

from __future__ import annotations

import socket
from pathlib import Path

from studio.launcher import LINES_DIR, bundled_file, free_port, home_dir, prepare_home


def test_the_data_folder_is_the_users_own_unless_overridden(tmp_path: Path) -> None:
    assert home_dir({}) == Path.home() / "GPR Studio"
    assert home_dir({"GPR_STUDIO_HOME": str(tmp_path)}) == tmp_path


def test_first_run_creates_the_folder_and_a_config(tmp_path: Path) -> None:
    lines = prepare_home(tmp_path / "home", bundled_file("config.yaml"))
    assert lines == tmp_path / "home" / LINES_DIR and lines.is_dir()
    assert (tmp_path / "home" / "config.yaml").read_text() == bundled_file("config.yaml").read_text()
    assert "GROQ_API_KEY=" in (tmp_path / "home" / ".env.example").read_text()


def test_a_later_run_never_overwrites_the_users_settings(tmp_path: Path) -> None:
    home = tmp_path / "home"
    prepare_home(home, bundled_file("config.yaml"))
    (home / "config.yaml").write_text("mine: true\n")
    prepare_home(home, bundled_file("config.yaml"))
    assert (home / "config.yaml").read_text() == "mine: true\n"


def test_a_busy_port_falls_back_to_a_free_one() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as held:
        held.bind(("127.0.0.1", 0))
        held.listen()
        busy = held.getsockname()[1]
        port = free_port(busy)
    assert port != busy and port > 0

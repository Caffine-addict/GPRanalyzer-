"""The Store interface. All persistence goes through this — no DuckDB (or any storage engine)
call may appear outside store/duckdb_store.py; that's the seam that lets the backend change
without touching the orchestrator or API layer.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from core.contracts import Finding, ScanFrame


class Store(ABC):
    @abstractmethod
    def save_frame(self, survey_id: str, line_id: str, frame: ScanFrame) -> int:
        """Persist frame metadata (position, provenance, antenna/dielectric metadata) —
        not the raw traces/image arrays. Returns a frame_id for save_finding to reference.
        """

    @abstractmethod
    def save_finding(self, survey_id: str, line_id: str, frame_id: int, finding: Finding) -> int:
        """Persist a Finding on the fast path, before reasoning has run. Returns a finding_id."""

    @abstractmethod
    def update_finding_reasoning(self, finding_id: int, finding: Finding) -> None:
        """Update a previously-saved Finding once its async reasoning pass completes."""

    @abstractmethod
    def get_findings_by_line(self, survey_id: str, line_id: str) -> list[Finding]:
        """All findings for one line of one survey, in the order they were saved."""

    @abstractmethod
    def get_findings_by_survey(self, survey_id: str) -> list[Finding]:
        """All findings for a survey, across every line, in the order they were saved."""

    @abstractmethod
    def get_findings_for_line_id(self, line_id: str) -> list[Finding]:
        """All findings for one physical line_id, across every survey that ever covered it.

        line_id is a persistent physical-location identifier, not scoped to
        a single survey run — this is what makes get_prior_passes meaningful
        (a survey re-run over the same ground looks up earlier surveys'
        findings by line_id, not by survey_id).
        """

    @abstractmethod
    def get_prior_passes(self, line_id: str, position: float, tolerance_m: float) -> list[dict[str, Any]]:
        """Findings from earlier surveys at approximately the same line and position.

        Empty list when none — never fabricated. Returned as plain dicts, not
        reconstructed Finding objects: Evidence.prior_passes is deliberately
        loosely typed (tuple[Any, ...]), and this is informational context
        for the reasoning prompt, not a full record to re-validate.
        """

    @abstractmethod
    def get_survey_summary(self, survey_id: str) -> dict[str, Any]:
        """Aggregate stats for a survey: total findings, counts by risk level and by class."""

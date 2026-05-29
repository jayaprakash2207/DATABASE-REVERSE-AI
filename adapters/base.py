"""
Abstract base adapter interfaces.

Every technology adapter (EF Core, Spring, Django, etc.) implements
BaseAdapter and returns Universal Semantic Model objects only.

Rule: adapters never return technology-specific structures to callers.
All technology details are preserved in the `raw` field of each USM object.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from models.semantic_model import SemanticModel, TechContext


class BaseAdapter(ABC):
    """
    Base class for all technology adapters.

    Each concrete adapter targets one or more Technology enum values
    (e.g., EF_CORE, SPRING_JPA, DJANGO_ORM).
    """

    # ------------------------------------------------------------------
    # Identity
    # ------------------------------------------------------------------

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable adapter name, e.g. 'EF Core / .NET'."""

    @property
    @abstractmethod
    def supported_technologies(self) -> list[str]:
        """Technology enum values this adapter handles."""

    # ------------------------------------------------------------------
    # Detection
    # ------------------------------------------------------------------

    @abstractmethod
    def can_handle(self, tech_context: "TechContext") -> bool:
        """
        Return True if this adapter can extract from the given tech context.
        Called by AdapterRegistry during adapter selection.
        """

    # ------------------------------------------------------------------
    # Extraction
    # ------------------------------------------------------------------

    @abstractmethod
    def extract(self, tech_context: "TechContext") -> "SemanticModel":
        """
        Full extraction pass: entities, relationships, endpoints, repositories,
        handlers. Returns a populated SemanticModel.

        Implementations should:
        - Set model.adapter_used = self.name
        - Append warnings to model.extraction_warnings
        - Preserve all raw parser data in each USM object's .raw field
        """

    # ------------------------------------------------------------------
    # Utility: safe file read
    # ------------------------------------------------------------------

    @staticmethod
    def _read(path: str) -> str:
        try:
            from pathlib import Path
            return Path(path).read_text(encoding="utf-8", errors="ignore")
        except OSError:
            return ""

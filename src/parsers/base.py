from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class ParsedChunk:
    """A single chunk of content extracted from a source document."""
    content: str
    title: str = ""
    metadata: dict = field(default_factory=dict)

    @property
    def is_empty(self) -> bool:
        return not self.content.strip()


class BaseParser(ABC):
    """Abstract base for all Synatyx content parsers."""

    @classmethod
    @abstractmethod
    def supports(cls, source: str) -> bool:
        """Return True if this parser can handle the given source path or URL."""
        ...

    @abstractmethod
    async def parse(self, source: str) -> list[ParsedChunk]:
        """
        Parse the source and return a list of chunks ready to be stored.

        Args:
            source: file path or URL to parse

        Returns:
            List of ParsedChunk objects, one per logical section/page/function
        """
        ...


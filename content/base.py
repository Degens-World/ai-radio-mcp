"""
content/base.py
===============
Base class for radio content sources.
Subclass this to add new data sources (blockchain, RSS, APIs, etc.).
"""


class ContentSource:
    """
    A content source provides event strings for the DJ to commentate on.

    Subclasses must implement:
      - fetch_events() -> list[str]   one-line descriptions of recent events
      - source_type   -> str          e.g. "blockchain", "rss", "freestyle"
    """

    source_type: str = "base"

    def fetch_events(self) -> list[str]:
        """Return a list of event strings for the DJ to riff on."""
        raise NotImplementedError

    def describe(self) -> str:
        """Human-readable description of this source (shown in logs)."""
        return f"[{self.source_type}] content source"

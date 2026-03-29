"""
content/freestyle.py
====================
A content source with no external data feed.

The DJ generates commentary from its own persona and topics list —
pure vibes, no live data required.
"""

import random
from .base import ContentSource


class FreestyleSource(ContentSource):
    source_type = "freestyle"

    def __init__(self, config: dict):
        """
        config keys:
          topics   list of topic strings the DJ should riff on
        """
        self.topics = config.get("topics", ["music", "the vibe", "the listeners"])

    def fetch_events(self) -> list[str]:
        topic = random.choice(self.topics)
        return [f"Talk about: {topic}"]

    def describe(self) -> str:
        return f"[freestyle] topics: {', '.join(self.topics[:3])}"

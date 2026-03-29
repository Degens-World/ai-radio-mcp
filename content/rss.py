"""
content/rss.py
==============
Fetches headlines from one or more RSS feeds for the DJ to commentate on.

Works with any standard RSS 2.0 / Atom feed URL.
The DJ receives recent headlines as event strings.
"""

import requests
import xml.etree.ElementTree as ET
from .base import ContentSource


class RSSSource(ContentSource):
    source_type = "rss"

    def __init__(self, config: dict):
        """
        config keys:
          feeds        list of RSS feed URLs
          max_items    max headlines to return per fetch (default 5)
        """
        self.feeds     = config.get("feeds", [])
        self.max_items = int(config.get("max_items", 5))
        self._seen     = set()

    def _parse_feed(self, url: str) -> list[str]:
        try:
            r = requests.get(url, timeout=10, headers={"User-Agent": "ai-radio/1.0"})
            r.raise_for_status()
            root = ET.fromstring(r.content)
        except Exception:
            return []

        items = []
        # RSS 2.0
        for item in root.findall(".//item"):
            title = item.findtext("title", "").strip()
            if title and title not in self._seen:
                items.append(title)
                self._seen.add(title)
        # Atom
        ns = {"atom": "http://www.w3.org/2005/Atom"}
        for entry in root.findall(".//atom:entry", ns):
            title_el = entry.find("atom:title", ns)
            if title_el is not None:
                title = (title_el.text or "").strip()
                if title and title not in self._seen:
                    items.append(title)
                    self._seen.add(title)
        return items

    def fetch_events(self) -> list[str]:
        all_items = []
        for feed_url in self.feeds:
            all_items.extend(self._parse_feed(feed_url))
            if len(all_items) >= self.max_items:
                break
        return [f'Breaking: "{h}"' for h in all_items[: self.max_items]]

    def describe(self) -> str:
        return f"[rss] {len(self.feeds)} feed(s): {', '.join(self.feeds[:2])}"

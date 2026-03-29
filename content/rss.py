"""
content/rss.py
==============
Fetches headlines from one or more RSS feeds for the DJ to commentate on.

Works with any standard RSS 2.0 / Atom feed URL.
The DJ receives recent headlines as event strings.
"""

import re
import time
import requests
import xml.etree.ElementTree as ET
from .base import ContentSource

# How long (seconds) before a headline can be repeated — default 1 hour
HEADLINE_TTL = 3600


class RSSSource(ContentSource):
    source_type = "rss"

    def __init__(self, config: dict):
        """
        config keys:
          feeds        list of RSS feed URLs
          max_items    max headlines to return per fetch (default 5)
          headline_ttl seconds before a headline can repeat (default 3600)
        """
        self.feeds        = config.get("feeds", [])
        self.max_items    = int(config.get("max_items", 5))
        self.headline_ttl = int(config.get("headline_ttl", HEADLINE_TTL))
        # {headline: first_seen_timestamp} — expires after headline_ttl
        self._seen: dict[str, float] = {}

    def _expire_seen(self):
        """Remove headlines older than headline_ttl so they can recycle."""
        cutoff = time.time() - self.headline_ttl
        self._seen = {h: t for h, t in self._seen.items() if t > cutoff}

    def _strip_html(self, text: str) -> str:
        return re.sub(r"<[^>]+>", "", text).strip()

    def _parse_feed(self, url: str) -> list[dict]:
        """Returns list of {title, description} dicts."""
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
            desc  = self._strip_html(item.findtext("description", ""))[:200]
            if title:
                items.append({"title": title, "description": desc})

        # Atom
        if not items:
            ns = {"atom": "http://www.w3.org/2005/Atom"}
            for entry in root.findall(".//atom:entry", ns):
                title_el   = entry.find("atom:title", ns)
                summary_el = entry.find("atom:summary", ns)
                title = (title_el.text   or "").strip() if title_el   is not None else ""
                desc  = (summary_el.text or "").strip() if summary_el is not None else ""
                desc  = self._strip_html(desc)[:200]
                if title:
                    items.append({"title": title, "description": desc})

        return items

    def fetch_events(self) -> list[str]:
        self._expire_seen()
        now = time.time()

        all_items = []
        for feed_url in self.feeds:
            all_items.extend(self._parse_feed(feed_url))

        # Prefer unseen headlines
        fresh = [i for i in all_items if i["title"] not in self._seen]

        # If everything has been seen (slow/small feeds), recycle the oldest ones
        if not fresh and all_items:
            oldest_titles = {h for h, _ in sorted(self._seen.items(), key=lambda x: x[1])[:self.max_items]}
            fresh = [i for i in all_items if i["title"] in oldest_titles]

        selected = fresh[: self.max_items]

        for item in selected:
            self._seen[item["title"]] = now

        # Include description for richer DJ context
        events = []
        for item in selected:
            if item["description"]:
                events.append(f'"{item["title"]}" — {item["description"]}')
            else:
                events.append(f'"{item["title"]}"')

        return events

    def describe(self) -> str:
        return f"[rss] {len(self.feeds)} feed(s): {', '.join(self.feeds[:2])}"

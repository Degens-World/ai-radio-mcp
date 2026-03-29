"""
content/blockchain.py
=====================
Fetches live blockchain events from a public explorer API.

Supports any chain that exposes a compatible REST API:
  - Ergo   (explorer.ergoplatform.com)
  - Custom (configure explorer_url + response mapping in station config)

Generates event strings like:
  "New block #1234567 mined. Network hashrate: 12.4 TH/s."
  "Whale alert: 50000 ERG moved in transaction abc123..."
  "Mempool has 412 unconfirmed transactions."
"""

import requests
from .base import ContentSource


class BlockchainSource(ContentSource):
    source_type = "blockchain"

    def __init__(self, config: dict):
        """
        config keys:
          explorer_url     Base URL of the explorer API
          coin_symbol      Ticker shown in DJ lines (e.g. "ERG", "BTC")
          coin_id          CoinGecko coin ID for price lookup (e.g. "ergo")
          whale_threshold  Coin value that triggers a whale alert (default 10000)
        """
        self.explorer_url    = config.get("explorer_url", "https://api.ergoplatform.com/api/v1")
        self.coin_symbol     = config.get("coin_symbol", "COIN")
        self.coin_id         = config.get("coin_id", "")
        self.whale_threshold = float(config.get("whale_threshold", 10000))
        self._last_block     = None

    def _get(self, path: str, params: dict = None) -> dict | None:
        try:
            r = requests.get(self.explorer_url + path, params=params, timeout=10)
            r.raise_for_status()
            return r.json()
        except Exception:
            return None

    def _fetch_price(self) -> str:
        if not self.coin_id:
            return ""
        try:
            r = requests.get(
                f"https://api.coingecko.com/api/v3/simple/price"
                f"?ids={self.coin_id}&vs_currencies=usd",
                timeout=10,
            )
            price = r.json().get(self.coin_id, {}).get("usd")
            if price:
                return f"{self.coin_symbol} is trading at ${price:.4f}."
        except Exception:
            pass
        return ""

    def fetch_events(self) -> list[str]:
        events = []

        # Latest block
        blocks = self._get("/blocks", {"limit": 1})
        if blocks:
            items = blocks.get("items", [])
            if items:
                blk = items[0]
                height = blk.get("height", "?")
                events.append(f"New block #{height} confirmed on the {self.coin_symbol} chain.")
                self._last_block = blk.get("id")

        # Mempool
        mempool = self._get("/transactions/unconfirmed", {"limit": 1})
        if mempool:
            total = mempool.get("total", 0)
            if total > 0:
                events.append(f"{total} unconfirmed transactions waiting in the mempool.")

        # Price
        price_str = self._fetch_price()
        if price_str:
            events.append(price_str)

        return events

    def describe(self) -> str:
        return f"[blockchain] {self.coin_symbol} via {self.explorer_url}"

from .blockchain import BlockchainSource
from .rss import RSSSource
from .freestyle import FreestyleSource


def build_source(config: dict):
    """Factory: return the right ContentSource for a station config dict."""
    source_type = config.get("content", {}).get("source", "freestyle")
    content_cfg = config.get("content", {}).get("params", {})

    if source_type == "blockchain":
        return BlockchainSource(content_cfg)
    elif source_type == "rss":
        return RSSSource(content_cfg)
    else:
        return FreestyleSource(content_cfg)

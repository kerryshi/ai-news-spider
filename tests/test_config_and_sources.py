"""Config loading + the source helpers that bit us before (domain parsing)."""

from engine.config import Config
from engine.sources import arxiv, hackernews
from engine.sources.base import domain_of


def test_config_loads_core_sections():
    cfg = Config.load()
    assert cfg.get("ollama", "host")
    assert int(cfg.get("general", "max_age_hours")) >= 1
    # the five sources we expect, all enabled
    for src in ("arxiv", "hackernews", "reddit", "github", "huggingface"):
        assert cfg.source_enabled(src), f"{src} should be enabled"


def test_ranking_weights_present():
    cfg = Config.load()
    for w in ("weight_velocity", "weight_novelty", "weight_relevance",
              "weight_earliness", "weight_query"):
        assert cfg.get("ranking", w) is not None


def test_domain_of_strips_www_and_path():
    assert domain_of("https://www.techcrunch.com/2026/01/01/x") == "techcrunch.com"
    assert domain_of("http://news.ycombinator.com/item?id=1") == "news.ycombinator.com"
    assert domain_of("not a url") == ""


def test_arxiv_and_hn_endpoints_use_https():
    # Cleartext HTTP let an on-path attacker rewrite these two feeds (both hosts
    # serve valid TLS). Guard against a regression back to http://.
    assert arxiv.API.startswith("https://")
    assert hackernews.API.startswith("https://")

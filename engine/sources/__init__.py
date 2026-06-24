"""Source adapters. Each exposes fetch(cfg, settings) -> list[Item]."""

from . import arxiv, hackernews, reddit, github, huggingface, lobsters

REGISTRY = {
    "arxiv": arxiv.fetch,
    "hackernews": hackernews.fetch,
    "reddit": reddit.fetch,
    "github": github.fetch,
    "huggingface": huggingface.fetch,
    "lobsters": lobsters.fetch,
}

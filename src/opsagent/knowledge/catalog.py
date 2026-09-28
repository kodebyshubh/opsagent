"""Product catalog JSON (knowledge source 3)."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from ..config import DATA_DIR
from ..money import rupee_str

CATALOG_PATH = DATA_DIR / "catalog.json"


@lru_cache(maxsize=1)
def _load(path: str) -> dict[str, dict]:
    items = json.loads(Path(path).read_text(encoding="utf-8"))
    return {p["sku"]: p for p in items}


def _public(p: dict) -> dict:
    out = {k: v for k, v in p.items() if k != "price_paise"}
    out["price"] = rupee_str(p["price_paise"])
    return out


def catalog() -> dict[str, dict]:
    return _load(str(CATALOG_PATH.resolve()))


def get_product(sku: str) -> dict | None:
    p = catalog().get(sku.strip().upper())
    return _public(p) if p else None


def find_products(query: str, limit: int = 5) -> list[dict]:
    terms = [t for t in query.lower().split() if len(t) > 1]
    scored = []
    for p in catalog().values():
        hay = f"{p['name']} {p['category']} {p['vendor']} {p['sku']}".lower()
        score = sum(1 for t in terms if t in hay)
        if score:
            scored.append((score, p["sku"], p))
    scored.sort(key=lambda x: (-x[0], x[1]))
    return [_public(p) for _, _, p in scored[:limit]]


def product_name(sku: str) -> str | None:
    p = catalog().get(sku)
    return p["name"] if p else None

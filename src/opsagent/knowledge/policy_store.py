"""Policy documents in ChromaDB (TASK-006, DEC-008)."""

from __future__ import annotations

import re
from pathlib import Path

from ..config import get_settings
from . import embeddings

COLLECTION = "policies"
MAX_CHARS = 800


def _client():
    import chromadb
    from chromadb.config import Settings as ChromaSettings

    path = get_settings().chroma_dir
    path.mkdir(parents=True, exist_ok=True)
    return chromadb.PersistentClient(
        path=str(path), settings=ChromaSettings(anonymized_telemetry=False)
    )


def parse_front_matter(text: str) -> tuple[dict[str, str], str]:
    meta: dict[str, str] = {}
    if text.startswith("---"):
        _, fm, body = text.split("---", 2)
        for line in fm.strip().splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                meta[k.strip()] = v.strip()
        return meta, body
    return meta, text


def chunk_markdown(body: str) -> list[tuple[str, str]]:
    """Split by ## / ### headings; long sections split on paragraphs. Returns (section, text)."""
    chunks: list[tuple[str, str]] = []
    section = "Introduction"
    buf: list[str] = []

    def flush() -> None:
        text = "\n".join(buf).strip()
        if not text:
            return
        paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
        cur = ""
        for p in paras:
            if cur and len(cur) + len(p) + 2 > MAX_CHARS:
                chunks.append((section, cur))
                cur = p
            else:
                cur = f"{cur}\n\n{p}" if cur else p
        if cur:
            chunks.append((section, cur))

    for line in body.splitlines():
        m = re.match(r"^(#{1,3})\s+(.*)", line)
        if m:
            flush()
            buf = []
            section = m.group(2).strip()
        else:
            buf.append(line)
    flush()
    return chunks


def build_index(policies_dir: Path) -> int:
    client = _client()
    try:
        client.delete_collection(COLLECTION)
    except Exception:  # collection may not exist yet
        pass
    backend, ef = embeddings.choose()
    col = client.create_collection(
        COLLECTION,
        embedding_function=None,
        metadata={"hnsw:space": "cosine", "embedding_backend": backend},
    )
    ids, docs, metas = [], [], []
    for path in sorted(policies_dir.glob("*.md")):
        meta, body = parse_front_matter(path.read_text(encoding="utf-8"))
        for i, (section, text) in enumerate(chunk_markdown(body)):
            ids.append(f"{path.name}#{i}")
            docs.append(f"[{meta.get('title', path.stem)} | {section}]\n{text}")
            metas.append({
                "source": path.name,
                "title": meta.get("title", path.stem),
                "section": section,
                "effective_date": meta.get("effective_date", "unknown"),
                "version": meta.get("version", "unknown"),
                "supersedes": meta.get("supersedes", ""),
            })
    col.add(ids=ids, documents=docs, metadatas=metas, embeddings=ef(docs))
    return len(ids)


def backend() -> str | None:
    try:
        col = _client().get_collection(COLLECTION, embedding_function=None)
        return (col.metadata or {}).get("embedding_backend")
    except Exception:
        return None


def _keyword_score(query: str, doc: str) -> float:
    q = {t for t in embeddings.word_tokens(query) if len(t) > 2}
    if not q:
        return 0.0
    d = set(embeddings.word_tokens(doc))
    return len(q & d) / len(q)


def search(query: str, k: int = 4) -> list[dict]:
    """Hybrid search: vector similarity + keyword overlap over the (small) policy corpus.

    Each hit is annotated with `superseded_by` when a newer document declares that it
    supersedes the hit's source, so the reasoner can see version conflicts explicitly.
    """
    col = _client().get_collection(COLLECTION, embedding_function=None)
    ef = embeddings.by_name((col.metadata or {}).get("embedding_backend", "onnx"))
    total = col.count()
    res = col.query(query_embeddings=ef([query]), n_results=max(1, total))
    rows = list(zip(res["ids"][0], res["documents"][0], res["metadatas"][0],
                    res["distances"][0], strict=False))
    newer: dict[str, str] = {}
    for _, _, meta, _ in rows:
        if meta.get("supersedes"):
            newer[meta["supersedes"]] = (
                f"{meta['source']} (version {meta['version']}, effective {meta['effective_date']})"
            )
    scored = []
    for cid, doc, meta, dist in rows:
        vec = 1 - float(dist)
        kw = _keyword_score(query, doc)
        scored.append((0.5 * vec + 0.5 * kw, cid, doc, meta))
    scored.sort(key=lambda x: -x[0])
    top = scored[: max(1, min(k, 8))]
    # Version-aware retrieval: if a hit comes from a document that supersedes an older one,
    # also return the older document's best-matching chunk so the reasoner can see (and
    # report) what changed between versions instead of silently missing the conflict.
    present = {m["source"] for _, _, _, m in top}
    for newer_row in [r for r in top if r[3].get("supersedes")]:
        older = newer_row[3]["supersedes"]
        if older in present:
            continue
        probe = f"{query} {newer_row[2]}"  # match the older chunk on the same topic
        candidates = [r for r in scored if r[3]["source"] == older]
        if candidates:
            best = max(candidates, key=lambda r: _keyword_score(probe, r[2]))
            top.append((newer_row[0] * 0.9, *best[1:]))
            present.add(older)
    out = []
    for score, cid, doc, meta in top:
        out.append({
            "chunk_id": cid,
            "source": meta["source"],
            "section": meta["section"],
            "effective_date": meta["effective_date"],
            "version": meta["version"],
            "supersedes": meta.get("supersedes") or None,
            "superseded_by": newer.get(meta["source"]),
            "relevance": round(score, 3),
            "text": doc,
        })
    return out


def index_ready() -> bool:
    try:
        return _client().get_collection(COLLECTION, embedding_function=None).count() > 0
    except Exception:
        return False

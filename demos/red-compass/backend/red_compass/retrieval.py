import re
from functools import lru_cache

from sqlalchemy import select, cast
from pgvector.sqlalchemy import Vector

from .config import settings
from .db import Policy, Session, initialize


@lru_cache
def embedder():
    from sentence_transformers import SentenceTransformer

    return SentenceTransformer(
        settings().embedding_model_id, device="cpu", local_files_only=True
    )


def ingest():
    initialize()
    sections = []
    for path in sorted(settings().policy_dir.glob("*.md")):
        content = path.read_text()
        title = content.splitlines()[0].removeprefix("# ")
        for number, text in re.findall(
            r"^## ([\d.]+) (.*?)(?=^## |\Z)", content, re.MULTILINE | re.DOTALL
        ):
            sections.append((path.stem, number, title, text.strip()))
    vectors = (
        embedder()
        .encode([x[2] + "\n" + x[3] for x in sections], normalize_embeddings=True)
        .tolist()
    )
    with Session.begin() as db:
        for (doc, section, title, body), vector in zip(sections, vectors):
            key = f"{settings().policy_version}:{doc}:{section}"
            db.merge(
                Policy(
                    key=key,
                    corpus=settings().policy_version,
                    document_id=doc,
                    section=section,
                    title=title,
                    text=body,
                    embedding=vector,
                )
            )
    return len(sections)


def public(row, score=None):
    data = {
        "document_id": row.document_id,
        "version": "1",
        "section": row.section,
        "title": row.title,
        "text": row.text,
    }
    if score is not None:
        data["score"] = round(float(score), 4)
    return data


def search(db, corpus, query, limit=6):
    vector = embedder().encode(query, normalize_embeddings=True).tolist()
    if db.bind.dialect.name == "postgresql":
        distance = cast(Policy.embedding, Vector(384)).cosine_distance(vector)
        rows = db.execute(
            select(Policy, distance)
            .where(Policy.corpus == corpus)
            .order_by(distance)
            .limit(limit)
        ).all()
        candidates = [(row, 1 - dist) for row, dist in rows]
    else:
        rows = db.scalars(select(Policy).where(Policy.corpus == corpus)).all()
        candidates = sorted(
            [(r, sum(a * b for a, b in zip(r.embedding, vector))) for r in rows],
            key=lambda x: x[1],
            reverse=True,
        )[:limit]
    exact_ids = set(re.findall(r"RC-[A-Z]+-\d+", query.upper()))
    exact = (
        db.scalars(
            select(Policy).where(
                Policy.corpus == corpus, Policy.document_id.in_(exact_ids)
            )
        ).all()
        if exact_ids
        else []
    )
    ordered = [(r, 1.0) for r in exact] + candidates
    seen = set()
    return [
        public(r, score)
        for r, score in ordered
        if not (r.key in seen or seen.add(r.key))
    ][:12]


if __name__ == "__main__":
    print(f"Indexed {ingest()} policy sections")

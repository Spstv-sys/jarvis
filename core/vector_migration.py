"""Re-embed saved text into a model-specific collection without deleting originals."""
from __future__ import annotations

import hashlib
import logging

logger = logging.getLogger(__name__)
SCHEMA = 1


def collection_name(model: str, dimension: int) -> str:
    digest = hashlib.sha256(model.encode("utf-8")).hexdigest()[:16]
    return f"smart_memory_{digest}_{dimension}"


def prepare_collection(client, embeddings, model: str, *, batch_size: int = 64,
                       on_progress=None):
    """Preserve IDs, text and metadata; resume partially completed copies by ID.

    The legacy collection and earlier model collections are kept intact.
    No old vectors are reused: equal dimensions do not imply equal models.
    """
    if batch_size < 1:
        raise ValueError("Migration batch size must be positive")
    dimension = len(embeddings.embed_query("Перевірка сумісності пам'яті"))
    if not dimension:
        raise ValueError("Embedding model returned an empty vector")
    name = collection_name(model, dimension)
    existing = client.list_collections()
    names = [item if isinstance(item, str) else item.name for item in existing]
    metadata = {"jarvis_memory_schema": SCHEMA, "embedding_model": model,
                "embedding_dimension": dimension}
    target = client.get_or_create_collection(name=name, embedding_function=None,
                                             metadata=metadata)
    actual = target.metadata or {}
    if any(actual.get(key) != value for key, value in metadata.items()):
        raise ValueError("Target collection metadata does not match the embedding model")

    for source_name in sorted(names):
        if source_name == name:
            continue
        if source_name != "smart_memory" and not source_name.startswith("smart_memory_"):
            continue
        source = client.get_collection(name=source_name, embedding_function=None)
        if source_name != "smart_memory" and (source.metadata or {}).get("jarvis_memory_schema") != SCHEMA:
            continue
        total = source.count()
        copied = 0
        for offset in range(0, total, batch_size):
            records = source.get(limit=batch_size, offset=offset,
                                 include=["documents", "metadatas"])
            ids = records["ids"]
            if not ids:
                raise RuntimeError("Source collection changed while migrating")
            present = set(target.get(ids=ids, include=["metadatas"])["ids"])
            positions = [i for i, record_id in enumerate(ids) if record_id not in present]
            if positions:
                documents = [records["documents"][i] for i in positions]
                if any(not isinstance(text, str) for text in documents):
                    raise ValueError("A saved record has no text to re-embed; originals are preserved")
                vectors = embeddings.embed_documents(documents)
                if len(vectors) != len(documents) or any(len(v) != dimension for v in vectors):
                    raise ValueError("Embedding model returned incompatible document vectors")
                metadatas = records.get("metadatas") or [None] * len(ids)
                target.upsert(ids=[ids[i] for i in positions], documents=documents,
                              embeddings=vectors,
                              metadatas=[metadatas[i] or None for i in positions])
                copied += len(positions)
            # Check every original ID, including when resuming an interrupted batch.
            if set(target.get(ids=ids, include=["metadatas"])["ids"]) != set(ids):
                raise RuntimeError("Migration verification failed; originals are preserved")
            processed = min(offset + len(ids), total)
            if on_progress is not None:
                on_progress(source_name, processed, total)
            if offset == 0 or processed == total or (offset // batch_size) % 10 == 0:
                logger.info("RAG migration %s: %s/%s checked (%s re-embedded)",
                            source_name, processed, total, copied)
        if source.count() != total:
            raise RuntimeError("Source collection changed while migrating; retry required")
        logger.info("RAG migration verified: %s, %s source records, %s copied",
                    source_name, total, copied)
    return name, dimension, target.count()

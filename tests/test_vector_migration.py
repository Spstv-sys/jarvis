"""Lossless, resumable re-embedding without models or external services."""
from types import SimpleNamespace

import pytest

from core.vector_migration import collection_name, prepare_collection


class Collection:
    def __init__(self, name, records=None, metadata=None):
        self.name, self.metadata = name, metadata
        self.records = dict(records or {})

    def count(self):
        return len(self.records)

    def get(self, *, ids=None, offset=0, limit=None, include=None):
        selected = list(self.records) if ids is None else [i for i in ids if i in self.records]
        selected = selected[offset:None if limit is None else offset + limit]
        return {"ids": selected, "documents": [self.records[i][0] for i in selected],
                "metadatas": [self.records[i][1] for i in selected]}

    def upsert(self, *, ids, documents, embeddings, metadatas):
        assert all(len(vector) == 3 for vector in embeddings)
        for record_id, text, meta in zip(ids, documents, metadatas):
            self.records[record_id] = (text, meta)


class Client:
    def __init__(self, sources):
        self.collections = {source.name: source for source in sources}

    def list_collections(self):
        return [SimpleNamespace(name=name) for name in self.collections]

    def get_collection(self, *, name, embedding_function=None):
        return self.collections[name]

    def get_or_create_collection(self, *, name, metadata, embedding_function=None):
        if name not in self.collections:
            self.collections[name] = Collection(name, metadata=metadata)
        return self.collections[name]


class Embedder:
    def __init__(self):
        self.embedded = []

    def embed_query(self, text):
        return [0.1, 0.2, 0.3]

    def embed_documents(self, documents):
        self.embedded.extend(documents)
        return [[0.1, 0.2, 0.3] for _ in documents]


def test_preserves_all_records_ids_metadata_and_legacy_collection():
    saved = {"first": ("Київ — столиця України", {"source": "book", "chapter": 3}),
             "second": ("Нотатка", None), "third": ("Інша нотатка", {"source": "dialogue"})}
    source = Collection("smart_memory", saved)
    client, embedder = Client([source]), Embedder()
    progress = []
    name, dim, count = prepare_collection(client, embedder, "new-model", batch_size=2,
                                          on_progress=lambda *args: progress.append(args))
    assert dim == 3 and count == 3
    assert source.records == saved and client.collections[name].records == saved
    assert len(embedder.embedded) == 3 and progress[-1] == ("smart_memory", 3, 3)
    assert client.collections[name].metadata["embedding_model"] == "new-model"


def test_restarts_skip_already_copied_ids_and_keep_new_dialogue():
    client = Client([Collection("smart_memory", {"a": ("one", None), "b": ("two", None)})])
    name, _, _ = prepare_collection(client, Embedder(), "model", batch_size=1)
    client.collections[name].records.pop("b")  # simulate a stopped, partially completed copy
    client.collections[name].records["new"] = ("new dialogue", None)
    embedder = Embedder()
    _, _, count = prepare_collection(client, embedder, "model", batch_size=1)
    assert embedder.embedded == ["two"] and count == 3
    assert client.collections[name].records["new"] == ("new dialogue", None)


def test_model_switch_preserves_records_from_previous_model_collection():
    client = Client([Collection("smart_memory", {"a": ("old", None)})])
    previous, _, _ = prepare_collection(client, Embedder(), "previous-model")
    client.collections[previous].records["recent"] = ("new after migration", None)
    current, _, count = prepare_collection(client, Embedder(), "current-model")
    assert current != previous and count == 2
    assert client.collections[current].records == client.collections[previous].records


def test_model_identity_is_checked_even_when_vector_dimensions_match():
    assert collection_name("model-a", 3) != collection_name("model-b", 3)
    assert collection_name("model-a", 3) != collection_name("model-a", 4)


def test_failed_embedding_keeps_legacy_and_resumes_from_completed_batch():
    source = Collection("smart_memory", {"a": ("one", None), "b": ("two", None)})
    client, embedder = Client([source]), Embedder()
    original = embedder.embed_documents

    def fail_second(documents):
        if documents == ["two"]:
            raise RuntimeError("interrupted")
        return original(documents)

    embedder.embed_documents = fail_second
    with pytest.raises(RuntimeError, match="interrupted"):
        prepare_collection(client, embedder, "model", batch_size=1)
    assert source.count() == 2
    resumed = Embedder()
    name, _, count = prepare_collection(client, resumed, "model", batch_size=1)
    assert count == 2 and resumed.embedded == ["two"]
    assert client.collections[name].records == source.records


def test_records_without_original_text_are_not_silently_dropped():
    source = Collection("smart_memory", {"missing": (None, None)})
    with pytest.raises(ValueError, match="no text"):
        prepare_collection(Client([source]), Embedder(), "model")
    assert source.count() == 1

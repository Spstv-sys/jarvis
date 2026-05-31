"""Інтеграційні тести core.knowledge_graph — шар сховища (реальний SQLite).

Сховище KG не потребує spacy (spacy вантажиться ліниво лише для NER),
тож ці тести виконуються будь-де. Тест NER-екстракції скіпається, якщо
spacy/укр.-модель не встановлено.
"""
import importlib.util

from core.knowledge_graph import KnowledgeGraph


def _kg(tmp_path):
    return KnowledgeGraph(str(tmp_path / "kg.sqlite"))


def test_upsert_creates_and_dedups(tmp_path):
    kg = _kg(tmp_path)
    id1 = kg.upsert_entity("Тарас Шевченко", "person", None)
    id2 = kg.upsert_entity("Тарас Шевченко", "person", None)
    assert id1 is not None and id1 == id2
    found = kg.find_entity("Тарас Шевченко")
    assert found and found[0]["mentions"] == 2   # лічильник згадок зріс


def test_short_name_rejected(tmp_path):
    assert _kg(tmp_path).upsert_entity("Я", "person", None) is None


def test_relation_canonical_and_neighbors(tmp_path):
    kg = _kg(tmp_path)
    a = kg.upsert_entity("Київ", "location", None)
    b = kg.upsert_entity("Україна", "location", None)
    kg.add_relation(a, b, None)
    kg.add_relation(b, a, None)   # зворотний порядок → той самий зв'язок
    nb = kg.get_neighbors(a)
    assert any(n["id"] == b for n in nb)
    assert nb[0]["weight"] == 2   # вага зросла, дублю не створено


def test_find_entity_prefix_and_like(tmp_path):
    kg = _kg(tmp_path)
    kg.upsert_entity("Шевченко", "person", None)
    assert kg.find_entity("шевч")      # префікс
    assert kg.find_entity("евченк")    # підрядок (LIKE)


def test_mentions_roundtrip(tmp_path):
    kg = _kg(tmp_path)
    eid = kg.upsert_entity("Леся Українка", "person", "Лісова пісня")
    kg.add_mention(eid, "Лісова пісня", 1, "Контекст про Лесю Українку")
    m = kg.get_mentions(eid)
    assert len(m) == 1 and "Контекст" in m[0]["context"]


def test_stats(tmp_path):
    kg = _kg(tmp_path)
    a = kg.upsert_entity("Київ", "location", None)
    b = kg.upsert_entity("Львів", "location", None)
    kg.add_relation(a, b, None)
    kg.add_mention(a, None, None, "ctx")
    s = kg.stats()
    assert s["entities"] == 2 and s["relations"] == 1 and s["mentions"] == 1


def test_extract_entities_spacy_guarded():
    """NER через spacy — лише якщо встановлено бібліотеку та укр. модель."""
    if importlib.util.find_spec("spacy") is None:
        return  # м'який скіп: spacy не встановлено
    from core.knowledge_graph import extract_entities, _load_spacy
    if _load_spacy() is None:
        return  # укр. модель не встановлена
    ents = extract_entities("Тарас Шевченко народився в селі Моринці.")
    assert isinstance(ents, list)

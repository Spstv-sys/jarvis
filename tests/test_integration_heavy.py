"""Інтеграційні тести важких підсистем: ChromaDB (RAG), spacy-NER, мережа (Ollama).

Усі вимагають реальних залежностей/сервісів, тож:
  • увесь модуль скіпається, якщо JARVIS_INTEGRATION не виставлено;
  • кожен тест додатково importorskip-ить свою залежність і скіпається,
    якщо потрібний сервіс/модель недоступні.

Локальний запуск:
    JARVIS_INTEGRATION=1 pytest tests/test_integration_heavy.py -v
"""
import os
import pytest

INTEGRATION = os.getenv("JARVIS_INTEGRATION", "").lower() in ("1", "true", "yes", "on")
pytestmark = pytest.mark.skipif(
    not INTEGRATION,
    reason="інтеграційні тести: задай JARVIS_INTEGRATION=1 (важкі залежності/мережа)",
)


def test_memory_rag_memorize_and_search(tmp_path):
    """RAG: реальний ChromaDB + ембединг-модель — зберегти факт і знайти його."""
    pytest.importorskip("chromadb")
    pytest.importorskip("langchain_chroma")
    pytest.importorskip("langchain_huggingface")
    from core.memory_rag import JarvisSmartMemory
    m = JarvisSmartMemory(db_path=str(tmp_path / "vdb"), docs_dir=str(tmp_path / "docs"))
    assert m._wait_ready(timeout=180), "ембединг-модель не готова"
    m.memorize("Столиця України — Київ.", source="test")
    res = m.search("яка столиця україни", k=3)
    assert "Київ" in res


def test_knowledge_graph_spacy_extract():
    """NER: реальна spacy-модель витягує сутності з тексту."""
    pytest.importorskip("spacy")
    from core.knowledge_graph import extract_entities, _load_spacy
    if _load_spacy() is None:
        pytest.skip("укр. spacy-модель не встановлена: python -m spacy download uk_core_news_md")
    ents = extract_entities("Тарас Шевченко жив у Києві.")
    assert isinstance(ents, list)


def test_ollama_generate():
    """Мережа: реальний Ollama-сервер генерує відповідь."""
    pytest.importorskip("openai")
    from core.providers.ollama import OllamaProvider
    p = OllamaProvider()
    if not p._list_local_models():
        pytest.skip("Ollama-сервер не запущено або немає локальних моделей")
    out = p.generate("Відповідай одним словом: яка столиця України?")
    assert isinstance(out, str) and out.strip()

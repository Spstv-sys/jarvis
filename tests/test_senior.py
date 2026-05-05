"""
Senior-level unit tests for Jarvis modules.

Covers edge cases, LRU cache contract, TTS sanitizer, sentence-split regex,
DB edge cases, utility robustness, skill-safety corner cases, and thread safety.
All tests run without microphone, speaker, or live LLM connections.
"""
import os
import threading
from unittest.mock import MagicMock, patch

import pytest

import config


# ===========================================================================
# Shared fixtures (re-use conftest.py's jarvis_instance & db_instance)
# ===========================================================================

@pytest.fixture
def llm_instance():
    """LLMHandler with all providers mocked and both API keys present."""
    from core.llm_handler import LLMHandler

    mock_db    = MagicMock()
    mock_brain = MagicMock()
    mock_brain.search.return_value = ""
    mock_brain.get_recent_facts.return_value = []
    mock_audio = MagicMock()

    with patch("core.llm_handler.DeepSeekProvider"), \
         patch("core.llm_handler.GeminiProvider"), \
         patch("core.llm_handler.OllamaProvider"), \
         patch("core.llm_handler.OpenAIProvider"), \
         patch.dict(os.environ, {"DEEPSEEK_API_KEY": "test", "GEMINI_API_KEY": "test"}):
        handler = LLMHandler(db=mock_db, brain=mock_brain, audio=mock_audio)

    return handler


@pytest.fixture
def db_instance():
    """Isolated in-memory JarvisDatabase for every test."""
    import tempfile
    from core.memory import JarvisDatabase

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    db = JarvisDatabase(db_path=path)
    yield db
    db.close()
    os.unlink(path)


# ===========================================================================
# 1. LLMHandler._sanitize_for_tts  (11 tests)
# ===========================================================================

@pytest.fixture
def llm_cls():
    """Return the LLMHandler class for classmethod access — no instance needed."""
    from core.llm_handler import LLMHandler
    return LLMHandler


def test_sanitize_empty_string_returns_empty(llm_cls):
    assert llm_cls._sanitize_for_tts("") == ""


def test_sanitize_code_fence_removed(llm_cls):
    text = "Дивись:\n```python\nprint('hello')\n```\nГарно!"
    result = llm_cls._sanitize_for_tts(text)
    assert "```" not in result
    assert "Гарно!" in result


def test_sanitize_inline_code_markers_removed_content_kept(llm_cls):
    result = llm_cls._sanitize_for_tts("Виклич функцію `len()` для рядка.")
    assert "`" not in result
    assert "len()" in result


def test_sanitize_bold_markers_removed_content_kept(llm_cls):
    result = llm_cls._sanitize_for_tts("Це **важливо** для коду.")
    assert "**" not in result
    assert "важливо" in result


def test_sanitize_italic_markers_removed_content_kept(llm_cls):
    result = llm_cls._sanitize_for_tts("Це _курсив_ у тексті.")
    assert "_" not in result
    assert "курсив" in result


def test_sanitize_heading_markers_removed(llm_cls):
    text = "## Розділ перший\nЯкийсь текст."
    result = llm_cls._sanitize_for_tts(text)
    assert "##" not in result
    assert "Розділ перший" in result


def test_sanitize_bullet_list_markers_removed(llm_cls):
    text = "Список:\n- перший елемент\n- другий елемент"
    result = llm_cls._sanitize_for_tts(text)
    assert not any(line.startswith("- ") for line in result.splitlines())
    assert "перший елемент" in result


def test_sanitize_numbered_list_markers_removed(llm_cls):
    text = "Кроки:\n1. перший\n2. другий"
    result = llm_cls._sanitize_for_tts(text)
    assert not any(line.lstrip().startswith(("1.", "2.")) for line in result.splitlines())
    assert "перший" in result


def test_sanitize_markdown_link_keeps_visible_text(llm_cls):
    result = llm_cls._sanitize_for_tts("Дивись [GitHub](https://github.com) для деталей.")
    assert "GitHub" in result
    assert "https://" not in result


def test_sanitize_emoji_stripped(llm_cls):
    result = llm_cls._sanitize_for_tts("Привіт 🤖 всі!")
    assert "🤖" not in result
    assert "Привіт" in result
    assert "всі" in result


def test_sanitize_plain_text_is_unchanged(llm_cls):
    plain = "Сьогодні гарна погода в Україні."
    assert llm_cls._sanitize_for_tts(plain) == plain


# ===========================================================================
# 2. LLMHandler._looks_like_code — edge cases  (3 tests)
# ===========================================================================

def test_looks_like_code_backtick_fence_is_definitive_signal(llm_instance):
    """A single triple-backtick fence is always classified as code."""
    assert llm_instance._looks_like_code("Ось рішення:\n```\nprint(1)\n```") is True


def test_looks_like_code_one_marker_without_fence_is_not_enough(llm_instance):
    """A single code marker that appears in prose must NOT trigger code detection."""
    # "def" appears as an abbreviation in natural language
    assert llm_instance._looks_like_code("def — скорочення слова 'definition'.") is False


def test_looks_like_code_two_marker_lines_is_enough(llm_instance):
    assert llm_instance._looks_like_code("def my_func():\n    return 42\nimport os") is True


# ===========================================================================
# 3. LRU cache contract  (4 tests)
# ===========================================================================

def test_cache_miss_returns_none(llm_instance):
    assert llm_instance._get_cached("nonexistent_key") is None


def test_cache_lru_hit_promotes_entry_to_mru(llm_instance):
    """After a get hit, the entry must survive to be the last evicted."""
    llm_instance._response_cache.clear()
    llm_instance._set_cached("a", "alpha")
    llm_instance._set_cached("b", "beta")

    # Access "a" — promotes it to MRU.  "b" becomes LRU.
    llm_instance._get_cached("a")

    # Fill the rest of the cache and add one extra to force eviction.
    for i in range(llm_instance.CACHE_MAX - 2):
        llm_instance._set_cached(f"fill_{i}", "x")
    llm_instance._set_cached("trigger_eviction", "y")

    # "b" was LRU — should have been evicted.
    assert llm_instance._get_cached("b") is None
    # "a" was promoted — must still be present.
    assert llm_instance._get_cached("a") == "alpha"


def test_cache_lru_evicts_oldest_when_full(llm_instance):
    """When cache reaches CACHE_MAX, the least-recently-inserted key is dropped first."""
    llm_instance._response_cache.clear()
    for i in range(llm_instance.CACHE_MAX):
        llm_instance._set_cached(f"key_{i}", f"val_{i}")

    # "key_0" is the oldest (LRU) — adding one more must evict it.
    llm_instance._set_cached("newest", "v")

    assert llm_instance._get_cached("key_0") is None
    assert llm_instance._get_cached("newest") == "v"


def test_cache_update_existing_key_moves_it_to_mru(llm_instance):
    """Re-setting an existing key should promote it to MRU, making key_1 the new LRU."""
    llm_instance._response_cache.clear()
    for i in range(llm_instance.CACHE_MAX):
        llm_instance._set_cached(f"key_{i}", f"val_{i}")

    # Re-set key_0 → becomes MRU.  key_1 is now LRU.
    llm_instance._set_cached("key_0", "updated")
    llm_instance._set_cached("trigger_eviction", "e")

    assert llm_instance._get_cached("key_0") == "updated"
    assert llm_instance._get_cached("key_1") is None


# ===========================================================================
# 4. LLMHandler.set_personality & get_status  (4 tests)
# ===========================================================================

def test_set_personality_programmer_mode(llm_instance):
    llm_instance.set_personality("режим програміст")
    assert llm_instance.personality_mode == "програміст"
    llm_instance.audio.speak.assert_called()


def test_set_personality_military_mode(llm_instance):
    llm_instance.set_personality("режим військовий")
    assert llm_instance.personality_mode == "військовий"


def test_set_personality_unknown_falls_back_to_standard(llm_instance):
    llm_instance.personality_mode = "програміст"
    llm_instance.set_personality("режим невідомий xyz")
    assert llm_instance.personality_mode == "стандартний"


def test_get_status_contains_active_provider_and_personality(llm_instance):
    llm_instance.active_provider = "gemini"
    llm_instance.personality_mode = "програміст"
    status = llm_instance.get_status()
    assert "gemini" in status
    assert "програміст" in status


# ===========================================================================
# 5. LLMHandler._normalize_ollama_model_name  (2 tests)
# ===========================================================================

def test_normalize_ollama_strips_latest_tag(llm_instance):
    assert llm_instance._normalize_ollama_model_name("qwen2.5:3b:latest") == "qwen2.5:3b"


def test_normalize_ollama_unchanged_without_latest_tag(llm_instance):
    assert llm_instance._normalize_ollama_model_name("qwen2.5:3b") == "qwen2.5:3b"


# ===========================================================================
# 6. LLMHandler.switch_provider — available providers  (3 tests)
# ===========================================================================

def test_switch_to_deepseek_when_available(llm_instance):
    llm_instance.active_provider = "gemini"
    assert "deepseek" in llm_instance._providers
    llm_instance.switch_provider("активуй deepseek")
    assert llm_instance.active_provider == "deepseek"


def test_switch_to_gemini_when_available(llm_instance):
    llm_instance.active_provider = "deepseek"
    assert "gemini" in llm_instance._providers
    llm_instance.switch_provider("використовуй gemini")
    assert llm_instance.active_provider == "gemini"


def test_switch_to_unavailable_deepseek_speaks_error(llm_instance):
    llm_instance._providers.pop("deepseek", None)
    llm_instance.switch_provider("активуй deepseek")
    spoken = llm_instance.audio.speak.call_args[0][0]
    assert "DeepSeek" in spoken or "недоступний" in spoken


# ===========================================================================
# 7. JarvisDatabase — edge cases  (8 tests)
# ===========================================================================

def test_db_search_facts_empty_query_returns_empty_list(db_instance):
    db_instance.add_fact("якийсь факт")
    assert db_instance.search_facts("") == []


def test_db_search_facts_all_short_tokens_falls_back_to_recent(db_instance):
    """All query tokens < 3 chars → fallback to get_recent_facts."""
    db_instance.add_fact("тест факт для перевірки")
    # "де", "як", "ти" are all ≤ 2 chars — raw_tokens is empty after filtering
    result = db_instance.search_facts("де як ти", limit=5)
    assert isinstance(result, list)
    # Fallback returns up to 5 most recent facts
    assert len(result) <= 5


def test_db_search_contacts_empty_query_returns_empty_list(db_instance):
    """search_contacts with empty/blank query should return [] immediately."""
    db_instance.upsert_contact("тест юзер", "+380000000001")
    assert db_instance.search_contacts("") == []
    assert db_instance.search_contacts("   ") == []


def test_db_upsert_contact_empty_name_is_silently_ignored(db_instance):
    db_instance.upsert_contact("", "+380000000001")
    count = db_instance._execute("SELECT COUNT(*) FROM contacts").fetchone()[0]
    assert count == 0


def test_db_upsert_contact_empty_phone_is_silently_ignored(db_instance):
    db_instance.upsert_contact("справжній контакт", "")
    count = db_instance._execute("SELECT COUNT(*) FROM contacts").fetchone()[0]
    assert count == 0


def test_db_set_setting_overwrites_previous_value(db_instance):
    db_instance.set_setting("color", "red")
    db_instance.set_setting("color", "blue")
    assert db_instance.get_setting("color") == "blue"


def test_db_get_tasks_returns_empty_list_for_date_with_no_entries(db_instance):
    assert db_instance.get_tasks("2000-01-01") == []


def test_db_facts_count_never_exceeds_max(db_instance):
    """Stress-add 2× MAX_FACTS and verify the hard cap is respected."""
    for i in range(db_instance.MAX_FACTS * 2):
        db_instance.add_fact(f"факт номер {i}")
    count = db_instance._execute("SELECT COUNT(*) FROM facts").fetchone()[0]
    assert count <= db_instance.MAX_FACTS


# ===========================================================================
# 8. utils.strip_triggers — edge cases  (5 tests)
# ===========================================================================

def test_strip_triggers_empty_text_returns_empty():
    from core.utils import strip_triggers
    assert strip_triggers("", ["навчись"]) == ""


def test_strip_triggers_no_matching_trigger_returns_original():
    from core.utils import strip_triggers
    assert strip_triggers("відкрий браузер", ["погода"]) == "відкрий браузер"


def test_strip_triggers_removes_trigger_at_end_of_string():
    from core.utils import strip_triggers
    result = strip_triggers("зроби щось навчись", ["навчись"])
    assert "навчись" not in result
    assert "зроби щось" in result


def test_strip_triggers_collapses_resulting_double_spaces():
    from core.utils import strip_triggers
    result = strip_triggers("навчись   грати на гітарі", ["навчись"])
    assert "  " not in result


def test_strip_triggers_strips_trailing_dot():
    from core.utils import strip_triggers
    # After removing the only word, strip(" .") should yield an empty string.
    result = strip_triggers("навчись.", ["навчись"])
    assert result == ""


# ===========================================================================
# 9. utils.normalize_filename — edge cases  (3 tests)
# ===========================================================================

def test_normalize_filename_voice_name_plus_extension():
    from core.utils import normalize_filename
    assert normalize_filename("тест пай") == "test.py"


def test_normalize_filename_main_py_from_voice():
    from core.utils import normalize_filename
    assert normalize_filename("мейн пай") == "main.py"


def test_normalize_filename_unknown_text_left_unchanged():
    from core.utils import normalize_filename
    text = "невідомий файл"
    assert normalize_filename(text) == text


# ===========================================================================
# 10. _check_skill_safety — additional corner cases  (3 tests)
# ===========================================================================

def test_skill_safety_dunder_import_blocked():
    from core.handlers.command_handlers import _check_skill_safety
    code = "result = __import__('os').system('id')"
    safe, _ = _check_skill_safety(code)
    assert safe is False


def test_skill_safety_exec_inside_function_blocked():
    from core.handlers.command_handlers import _check_skill_safety
    code = (
        "TRIGGERS = ['тест']\n"
        "def execute(cmd, jarvis):\n"
        "    payload = 'pass'\n"
        "    exec(payload)\n"
    )
    safe, _ = _check_skill_safety(code)
    assert safe is False


def test_skill_safety_eval_with_whitespace_still_blocked():
    """eval must be caught even when surrounded by extra whitespace."""
    from core.handlers.command_handlers import _check_skill_safety
    code = "  eval  (user_input)"
    safe, _ = _check_skill_safety(code)
    assert safe is False


# ===========================================================================
# 11. JarvisUltimate routing — edge cases  (3 tests)
# ===========================================================================

def test_enqueue_speech_empty_string_does_not_add_to_queue(jarvis_instance):
    before = jarvis_instance.output_queue.qsize()
    jarvis_instance.enqueue_speech("")
    assert jarvis_instance.output_queue.qsize() == before


def test_execute_command_no_matching_trigger_falls_through_to_ai_chat(jarvis_instance):
    """A command that matches no trigger must be routed to handle_ai_chat."""
    jarvis_instance.dynamic_skills = []
    jarvis_instance.handle_ai_chat = MagicMock(return_value="CONTINUE")

    result = jarvis_instance.execute_command("щось зовсім незрозуміле xyz123")

    jarvis_instance.handle_ai_chat.assert_called_once()
    assert result == "CONTINUE"


def test_execute_command_dynamic_skill_exception_is_handled_gracefully(jarvis_instance):
    """An exception raised by a dynamic skill must be caught; route returns CONTINUE."""
    def bad_skill(cmd, j):
        raise RuntimeError("навичка зламалась")

    jarvis_instance.dynamic_skills = [{
        "triggers": ["тест навичка"],
        "func":     bad_skill,
        "name":     "broken_skill",
    }]

    result = jarvis_instance.execute_command("тест навичка зараз")
    assert result == "CONTINUE"


# ===========================================================================
# 12. LLMHandler.SENTENCE_END regex  (3 tests)
# ===========================================================================

def test_sentence_end_splits_after_two_char_word_boundary(llm_cls):
    parts = llm_cls.SENTENCE_END.split("Привіт світе. Як справи?")
    assert len(parts) == 2
    assert parts[0].endswith(".")
    assert "Як справи?" in parts[1]


def test_sentence_end_does_not_split_single_char_abbreviation(llm_cls):
    """'м.' has only one letter before the period — must NOT trigger a split."""
    parts = llm_cls.SENTENCE_END.split("м. Київ — столиця.")
    # No split should happen at "м. "
    assert parts[0].startswith("м.")


def test_sentence_end_splits_on_bare_newline(llm_cls):
    parts = llm_cls.SENTENCE_END.split("Перший рядок\nДругий рядок")
    assert len(parts) == 2
    assert "Перший рядок" in parts[0]
    assert "Другий рядок" in parts[1]


# ===========================================================================
# 13. Thread safety — concurrent cache writes  (1 test)
# ===========================================================================

def test_cache_concurrent_writes_do_not_corrupt_state(llm_instance):
    """Multiple threads writing to the LRU cache concurrently must not raise."""
    errors: list = []

    def writer(start: int) -> None:
        try:
            for i in range(50):
                llm_instance._set_cached(f"k_{start}_{i}", f"v_{i}")
        except Exception as exc:  # broad catch required — any threading bug must be captured
            errors.append(exc)

    threads = [threading.Thread(target=writer, args=(t,)) for t in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"Thread-safety errors: {errors}"
    assert len(llm_instance._response_cache) <= llm_instance.CACHE_MAX

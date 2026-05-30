"""Тести fuzzy-зіставлення команд (core/fuzzy_match).

Перевіряють, що дрібні STT-помилки на коротких командах ловляться, а
неспоріднені/ризиковані короткі слова — ні. Поріг за замовчуванням —
config.FUZZY_WORD_THRESHOLD (82).
"""
import config
from core import fuzzy_match as fm

TH = config.FUZZY_WORD_THRESHOLD


# --- normalize -------------------------------------------------------------
def test_normalize_strips_punct_and_case():
    assert fm.normalize("  Погода, ЗАРАЗ!! ") == "погода зараз"


# --- single-word triggers: realistic Whisper typos should match -----------
def test_single_word_typo_pohoda():
    # "погода" -> "пагода" (1 заміна літери)
    assert fm.matches("пагода зараз", "погода", TH) is True


def test_single_word_typo_tyhishe():
    # "тихіше" -> "тихише" (і/и)
    assert fm.matches("зроби тихише", "тихіше", TH) is True


def test_single_word_typo_vidkryi():
    assert fm.matches("відкий браузер", "відкрий", TH) is True


def test_single_word_exact_still_matches():
    assert fm.matches("яка погода сьогодні", "погода", TH) is True


# --- single-word triggers: unrelated words must NOT match ------------------
def test_single_word_unrelated_no_match():
    assert fm.matches("відкрий браузер", "погода", TH) is False


def test_single_word_different_word_no_match():
    # "погана" — інше слово, не повинно матчити "погода"
    assert fm.matches("яка погана ідея", "погода", TH) is False


# --- multi-word triggers: token-aware, tolerant to extra/typo words --------
def test_multi_word_typo_in_one_token():
    # "свіжі новини" з помилкою в одному слові
    assert fm.trigger_score("покажи свіжі новени", "свіжі новини") >= 80


def test_multi_word_unrelated_low_score():
    assert fm.trigger_score("котра година", "свіжі новини") < 70


# --- safety: empty inputs --------------------------------------------------
def test_empty_inputs_zero():
    assert fm.trigger_score("", "погода") == 0.0
    assert fm.trigger_score("погода", "") == 0.0


# --- safety: short risky triggers are excluded from single-word fuzzy ------
def test_short_trigger_gated_out():
    """Роутер вмикає однослівний fuzzy лише для тригерів >= FUZZY_WORD_MIN_LEN.

    'стоп' (4 символи) має лишатись точним навіть попри високий сирий score,
    інакше 'стіл'/'стоп' хибно глушили б асистента.
    """
    assert len("стоп") < config.FUZZY_WORD_MIN_LEN
    # сирий score може бути високим — саме тому довжинний гейт критичний
    assert fm.trigger_score("стол увімкни", "стоп") >= 70


# --- split-word recovery via joined adjacent tokens (STT дробить слово) -----
def test_joined_tokens_recover_split_word():
    # Whisper розбив "запам'ятай" на "за пам'ятай" — склеєна пара має матчити
    assert fm.trigger_score("за пам'ятай мій номер", "запам'ятай") >= TH


def test_joined_tokens_no_false_positive():
    # неспоріднені слова навіть склеєні не повинні матчити
    assert fm.matches("відкрий браузер зараз", "погода", TH) is False

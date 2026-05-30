"""Command router — диспетчеризація голосових команд.

Винесено з main.py у межах рефакторингу (розвантаження god-file).
``dispatch(jarvis, cmd)`` повторює точну семантику колишнього
``JarvisUltimate.execute_command``:

  1. оновлення афекту з тексту користувача (опційно);
  2. динамічні навички (substring → word-boundary → fuzzy) — найвищий пріоритет;
  3. вбудовані маршрути ``ROUTES`` (точний тригер → fuzzy для багатослівних);
  4. фолбек через IntentRouter (якщо ввімкнено);
  5. фінальний фолбек у ``handle_ai_chat``.

Функція приймає інстанс ``jarvis`` і використовує його атрибути/методи
(``handlers``, ``dynamic_skills``, ``_trigger_in_cmd``, ``fuzzy_in_command``,
``intent_router``, ``handle_ai_chat``), тож поведінка ідентична методу.
"""
from __future__ import annotations

import logging

import config
from core import fuzzy_match

logger = logging.getLogger(__name__)


def dispatch(jarvis, cmd: str) -> str:
    """Маршрутизує команду ``cmd`` через підсистеми ``jarvis``.

    Повертає рядок-статус (``"CONTINUE"`` / ``"STOP"`` / результат handler-а).
    """
    logger.info(f"Команда: {cmd}")

    # Афект: оновлюємо емоційну оцінку з тексту користувача (без важких
    # моделей — лексикон). Не блокує помилками: модуль повністю опційний.
    try:
        from core.affect import get_affect
        get_affect().observe_text(cmd)
    except Exception as e:
        logger.debug(f"[Affect] observe_text помилка: {e}")

    # Dynamic skills take priority.
    # For multi-word triggers we also use fuzzy matching so that minor STT
    # transcription errors (e.g. "агентські" → "агентскі") still fire.
    cmd_lower = cmd.lower()
    for skill in jarvis.dynamic_skills:
        matched = False
        for t in skill['triggers']:
            # 1) fast substring check (handles most cases)
            if t.lower() in cmd_lower:
                matched = True
                break
            # 2) word-boundary regex (handles trailing punctuation etc.)
            if jarvis._trigger_in_cmd(t, cmd):
                matched = True
                break
            # 3) fuzzy fallback for multi-word triggers (handles STT errors)
            if len(t.split()) >= 2 and jarvis.fuzzy_in_command(cmd, t, threshold=82):
                matched = True
                break
        if matched:
            try:
                result = skill['func'](cmd, jarvis)
                if result:
                    jarvis.audio.speak(result)
            except Exception as e:
                logger.error(f"Помилка навички {skill['name']}: {e}")
            return "CONTINUE"

    ROUTES = [
        ("update_knowledge", jarvis.handlers.cmd_update_knowledge),
        ("activate_skill",   jarvis.handlers.cmd_activate_skill),
        ("learn_skill",      jarvis.handlers.cmd_learn_skill),
        ("weather_exact",    jarvis.handlers.cmd_scrape_weather),
        ("weather",          jarvis.handlers.cmd_weather),
        ("youtube_play",     jarvis.handlers.cmd_play_youtube),
        ("youtube_search",   jarvis.handlers.cmd_search_youtube),
        ("news",             jarvis.handlers.cmd_scrape_news),
        ("finance",          jarvis.handlers.cmd_finance),
        ("switch_ai",        jarvis.handlers.cmd_switch_ai),
        ("personality",      jarvis.handlers.cmd_personality),
        ("check_ai",         jarvis.handlers.cmd_check_ai),
        ("timer",            jarvis.handlers.cmd_timer),
        ("hardware",         jarvis.handlers.cmd_hardware_control),
        ("apps_os",          jarvis.handlers.cmd_apps_and_os),
        ("vision",           jarvis.handlers.cmd_vision),
        ("code_assistant",   jarvis.handlers.cmd_code_assistant),
        ("calendar",         jarvis.handlers.cmd_calendar),
        ("dictation",        jarvis.handlers.cmd_dictation),
        # ВАЖЛИВО: system_exit має перевірятися РАНІШЕ за stop_voice,
        # бо тригер "стоп" зі stop_voice матчить будь-яку фразу зі словом
        # "стоп" (напр. "нео стоп") і поверне CONTINUE замість завершення.
        ("system_exit",      jarvis.handlers.cmd_system_exit),
        ("stop_voice",       jarvis.handlers.cmd_stop_voice),
        ("memorize",         jarvis.handlers.cmd_memorize),
        ("web_search",       jarvis.handlers.cmd_web_search),
        ("find_file",        jarvis.handlers.cmd_find_file),
    ]

    for key, handler in ROUTES:
        triggers = config.TRIGGERS.get(key, [])
        for trigger in sorted(triggers, key=len, reverse=True):
            if jarvis._trigger_in_cmd(trigger, cmd):
                logger.info(f"execute_command: маршрут '{key}' для команди {cmd!r}")
                return handler(cmd)
            # Fuzzy fallback для багатослівних тригерів (STT-помилки).
            # Вимагаємо щоб і команда була багатослівною — інакше короткі
            # репліки на кшталт 'стоп' хибно матчать 'стоп система'.
            if (len(trigger.split()) >= 2
                    and len(cmd.split()) >= 2
                    and jarvis.fuzzy_in_command(cmd, trigger, threshold=80)):
                logger.info(f"execute_command: fuzzy маршрут '{key}' для команди {cmd!r} (тригер='{trigger}')")
                return handler(cmd)
            # Однослівний fuzzy-фолбек: ловить дрібні STT-помилки на коротких
            # командах ("погода"->"пагода"). Обмежено тригерами >= FUZZY_WORD_MIN_LEN
            # символів і короткими командами, тож ризиковані короткі слова
            # ("стоп"/"курс"/"диск") і довге вільне мовлення лишаються поза fuzzy.
            # Довжина команди гейтиться, АЛЕ дуже характерні тригери
            # (>= FUZZY_WORD_LONG_LEN символів, напр. "запам'ятай") матчаться
            # навіть у довгих командах — там випадковий збіг малоймовірний.
            if (len(trigger.split()) == 1
                    and len(trigger) >= config.FUZZY_WORD_MIN_LEN
                    and (len(cmd.split()) <= config.FUZZY_WORD_MAX_CMD_WORDS
                         or len(trigger) >= config.FUZZY_WORD_LONG_LEN)
                    and fuzzy_match.matches(cmd, trigger, config.FUZZY_WORD_THRESHOLD)):
                logger.info(f"execute_command: fuzzy(1w) маршрут '{key}' для команди {cmd!r} (тригер='{trigger}')")
                return handler(cmd)

    # ФОЛБЕК: якщо жоден тригер не зматчив и IntentRouter вмикнено —
    # питаємо LLM, який маршрут обрати. При впевненості → викликаємо відповідний
    # handler в ROUTES; інакше — розмова.
    if jarvis.intent_router is not None:
        try:
            guess = jarvis.intent_router.classify(cmd)
            logger.info(f"execute_command: IntentRouter → {guess}")
            if (guess.name in dict(ROUTES)
                    and guess.confidence >= getattr(config, "INTENT_ROUTER_MIN_CONF", 0.55)):
                handler = dict(ROUTES)[guess.name]
                logger.info(f"execute_command: LLM-маршрут '{guess.name}' (conf={guess.confidence:.2f}, src={guess.source})")
                return handler(cmd)
        except Exception as e:
            logger.warning(f"IntentRouter помилка: {e}")

    logger.info(f"execute_command: маршрут не знайдено, передаю в handle_ai_chat: {cmd!r}")
    return jarvis.handle_ai_chat(cmd)

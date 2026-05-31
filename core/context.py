"""Типізовані фасади над god-object ``jarvis`` (structural typing).

Історично всюди передавався цілий інстанс ``JarvisUltimate`` — і у скіли
(``execute(cmd, jarvis)``), і у хендлери, і у роутер/рантайм. Це god-object:
незрозуміло, на що саме код покладається, і будь-хто має доступ до всього.

Тут оголошено два ``typing.Protocol``-фасади, що ДОКУМЕНТУЮТЬ реальну
поверхню, не змінюючи рантайм (Protocol — структурна типізація: будь-який
обʼєкт із потрібними атрибутами/методами вже «є» ним, успадкування не
потрібне). ``JarvisUltimate`` автоматично задовольняє обидва, тож існуючий
код і контракт ``execute(cmd, jarvis)`` лишаються сумісними.

Цінність: статична перевірка (mypy/IDE) та явний контракт того, що
насправді потрібно кожному шару — фундамент для подальшого звуження.

  • :class:`SkillContext`  — мінімальна ПУБЛІЧНА поверхня для плагінів-скілів
    (фактично використовуються лише ``audio``, ``llm``, ``brain``).
  • :class:`JarvisContext` — повна ВНУТРІШНЯ поверхня, на яку покладаються
    хендлери, роутер і рантайм.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    import queue
    import threading
    from concurrent.futures import ThreadPoolExecutor

    from core.audio_manager import AudioManager
    from core.llm_handler import LLMHandler
    from core.memory import JarvisDatabase
    from core.memory_rag import JarvisSmartMemory
    from core.system_actions import SystemActions
    from core.working_memory import WorkingMemory


@runtime_checkable
class SkillContext(Protocol):
    """Контракт, який бачить динамічний скіл у ``execute(cmd, jarvis)``.

    Це і є ПУБЛІЧНИЙ API плагінів: гарантовано доступні лише ці три
    підсистеми. Скіли НЕ повинні залежати від внутрішніх атрибутів Jarvis
    поза цим переліком — інакше їх зламає майбутній рефакторинг.
    """

    audio: AudioManager
    llm: LLMHandler
    brain: JarvisSmartMemory


class JarvisContext(SkillContext, Protocol):
    """Повна внутрішня поверхня для хендлерів, роутера та рантайму.

    Розширює :class:`SkillContext` рештою підсистем, чергами, прапорцями
    життєвого циклу та методами оркестрації, якими реально користуються
    ``core/handlers/*``, ``core/command_router.py`` і ``core/runtime.py``.
    """

    # --- підсистеми ---
    db: JarvisDatabase
    scraper: Any
    youtube: Any
    sys_actions: SystemActions
    working_memory: WorkingMemory
    user_profile: Any
    episodic_memory: Any
    intent_router: Any
    handlers: Any
    dynamic_skills: list
    skills_dir: str
    ui_callback: Any

    # --- черги та життєвий цикл ---
    input_queue: queue.Queue
    output_queue: queue.Queue
    cancel_event: threading.Event
    is_running: bool
    _brain_pool: ThreadPoolExecutor

    # --- маршрутизація / оркестрація ---
    def execute_command(self, cmd: str) -> str: ...
    def handle_ai_chat(self, cmd: str) -> str: ...
    def load_skills(self) -> None: ...
    def fuzzy_in_command(self, command: str, trigger: str, threshold: int = ...) -> bool: ...
    def _trigger_in_cmd(self, trigger: str, cmd: str) -> bool: ...

    # --- цикл обробки команд (core/runtime) ---
    def _process_command(self, cmd: str) -> None: ...
    def _force_shutdown(self) -> None: ...
    def _persist_working_memory(self) -> None: ...

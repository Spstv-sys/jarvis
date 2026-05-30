"""
CommandHandlers — тонкий фасад над доменними міксинами.

Усі методи ``cmd_*`` винесено у доменні модулі-міксини
(``knowledge``, ``media``, ``memory``, ``system``, ``tools``, ``web_search``),
а спільні аксесори до підсистем Jarvis — у :class:`core.handlers._base.HandlerBase`.
Цей клас лише збирає їх докупи, лишаючи ``JarvisUltimate`` тонким оркестратором
(init + маршрутизація + життєвий цикл).

``HandlerBase`` стоїть останнім у списку баз: він надає ``__init__(jarvis)`` та
property-аксесори (``self.audio``, ``self.llm`` тощо), на які покладаються міксини.

``_check_skill_safety`` та ``_FORBIDDEN_SKILL_PATTERNS`` ре-експортуються тут
заради зворотної сумісності з наявними тестами, що імпортують їх із цього модуля.
"""
from __future__ import annotations

from core.handlers._base import HandlerBase
from core.handlers._safety import _check_skill_safety, _FORBIDDEN_SKILL_PATTERNS
from core.handlers.knowledge import KnowledgeMixin
from core.handlers.media import MediaMixin
from core.handlers.memory import MemoryMixin
from core.handlers.system import SystemMixin
from core.handlers.tools import ToolsMixin
from core.handlers.web_search import WebSearchMixin

__all__ = ["CommandHandlers", "_check_skill_safety", "_FORBIDDEN_SKILL_PATTERNS"]


class CommandHandlers(
    KnowledgeMixin,
    MediaMixin,
    MemoryMixin,
    SystemMixin,
    ToolsMixin,
    WebSearchMixin,
    HandlerBase,
):
    """Збірний клас усіх голосових команд Jarvis.

    Поведінка ідентична попередньому моноліту — методи лише фізично
    розкладені по доменних міксинах.
    """

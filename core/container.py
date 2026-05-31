"""ServiceContainer — єдине джерело правди для підсистем-singleton-ів.

Раніше кожна підсистема тримала власний модульний глобал (``_SINGLETON`` /
``_GLOBAL`` + lock) і свою ``get_X()``-фабрику. Прихований глобальний стан
ускладнював тестування (інстанси «протікали» між тестами) і унеможливлював
два паралельні інстанси Jarvis в одному процесі.

Тепер ``get_X()`` лишаються тонкими обгортками, але СХОВИЩЕ інстансів —
тут, у контейнері. Логіка конструювання кожної підсистеми лишається у її
модулі (передається у :meth:`ServiceContainer.get_or_create` як фабрика),
тож семантика ініціалізації не змінюється — централізується лише зберігання
плюс зʼявляються гачки ``set`` / ``reset`` для тестів і для перевизначення.

Приклад тесту::

    from core.container import get_container, reset_container
    get_container().set("working_memory", FakeWM())   # підмінити
    ...                                                # тест
    reset_container()                                  # прибрати все

Потокобезпечно: один ``RLock`` (реентрантний — фабрика може звертатися до
контейнера за залежністю, не блокуючи сама себе).
"""
from __future__ import annotations

import threading
from typing import Callable, Optional, TypeVar

T = TypeVar("T")


class ServiceContainer:
    """Лінива реєстрація + зберігання інстансів підсистем за іменем."""

    def __init__(self) -> None:
        self._instances: dict[str, object] = {}
        self._lock = threading.RLock()

    def get_or_create(self, name: str, factory: Callable[[], T]) -> T:
        """Повернути збережений інстанс ``name`` або створити через ``factory``.

        Перший виклик конструює інстанс і кешує його; наступні повертають той
        самий обʼєкт. ``factory`` викликається не більше одного разу на імʼя
        (поки не зроблено ``reset``).
        """
        with self._lock:
            if name not in self._instances:
                self._instances[name] = factory()
            return self._instances[name]  # type: ignore[return-value]

    def peek(self, name: str) -> Optional[object]:
        """Повернути збережений інстанс або ``None`` без створення нового.

        Для підсистем з умовною ініціалізацією (напр. ``wiki_offline``, що
        може лишатися ``None``, доки не сконфігуровано), які самі керують
        тим, коли кешувати результат через :meth:`set`.
        """
        with self._lock:
            return self._instances.get(name)

    def set(self, name: str, instance: object) -> None:
        """Зберегти/перевизначити інстанс ``name`` (підміна у тестах)."""
        with self._lock:
            self._instances[name] = instance

    def reset(self, name: Optional[str] = None) -> None:
        """Прибрати один інстанс (``name``) або всі (``name=None``)."""
        with self._lock:
            if name is None:
                self._instances.clear()
            else:
                self._instances.pop(name, None)


# ---------------------------------------------------------------------------
# Process-wide container
# ---------------------------------------------------------------------------
_CONTAINER: Optional[ServiceContainer] = None
_CONTAINER_LOCK = threading.RLock()


def get_container() -> ServiceContainer:
    """Повернути загальнопроцесний контейнер (створити при першому виклику)."""
    global _CONTAINER
    with _CONTAINER_LOCK:
        if _CONTAINER is None:
            _CONTAINER = ServiceContainer()
        return _CONTAINER


def reset_container() -> None:
    """Очистити всі збережені інстанси (зручно у фікстурах тестів)."""
    with _CONTAINER_LOCK:
        if _CONTAINER is not None:
            _CONTAINER.reset()

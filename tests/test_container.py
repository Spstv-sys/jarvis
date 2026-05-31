"""Тести ServiceContainer і shim-ів singleton-ів.

Перевіряють, що:
  • контейнер кешує інстанс (singleton-семантика get_or_create);
  • set() перевизначає інстанс (підміна у тестах);
  • reset() очищає сховище (ізоляція тестів);
  • peek() не створює інстанс;
  • get_working_memory()/get_goal_stack() делегують у контейнер і
    лишаються стабільними між викликами, а reset_container() їх скидає.
"""
import pytest

from core.container import ServiceContainer, get_container, reset_container


def test_get_or_create_caches_instance():
    c = ServiceContainer()
    calls = {"n": 0}

    def factory():
        calls["n"] += 1
        return object()

    a = c.get_or_create("x", factory)
    b = c.get_or_create("x", factory)
    assert a is b
    assert calls["n"] == 1


def test_set_overrides_instance():
    c = ServiceContainer()
    c.get_or_create("x", object)
    sentinel = object()
    c.set("x", sentinel)
    assert c.get_or_create("x", object) is sentinel


def test_peek_does_not_create():
    c = ServiceContainer()
    assert c.peek("missing") is None
    c.get_or_create("x", object)
    assert c.peek("x") is not None


def test_reset_one_and_all():
    c = ServiceContainer()
    c.get_or_create("a", object)
    c.get_or_create("b", object)
    c.reset("a")
    assert c.peek("a") is None
    assert c.peek("b") is not None
    c.reset()
    assert c.peek("b") is None


def test_shim_delegates_to_container_and_resets():
    from core.working_memory import get_working_memory

    reset_container()
    a = get_working_memory()
    b = get_working_memory()
    assert a is b, "повторний виклик має повертати той самий інстанс"
    assert get_container().peek("working_memory") is a, "інстанс має зберігатися у контейнері"

    reset_container()
    c = get_working_memory()
    assert c is not a, "після reset_container() має створитися новий інстанс"


def test_override_subsystem_via_container():
    """Демонструє підміну підсистеми у тесті без чіпання модульних глобалів."""
    from core.goal_stack import get_goal_stack

    reset_container()
    fake = object()
    get_container().set("goal_stack", fake)
    assert get_goal_stack() is fake
    reset_container()

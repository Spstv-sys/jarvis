"""Юніт-тести core.planner: Planner (LLM→Step), Executor (виконання), хелпери."""
import threading

from core.planner import Planner, Executor, Step, StepResult, build_route_descriptions


# ----------------------------------------------------------------- Planner
def _planner(llm):
    return Planner(llm_call=llm, route_descriptions={"weather": "погода", "news": "новини"}, max_steps=3)


def test_empty_goal_returns_empty():
    assert _planner(lambda p: "{}").plan("") == []


def test_parses_valid_plan():
    llm = lambda p: '{"plan":[{"n":1,"skill":"weather","input":"яка погода","reason":"r"}]}'
    steps = _planner(llm).plan("дізнайся погоду")
    assert len(steps) == 1
    assert steps[0].skill == "weather" and steps[0].input == "яка погода" and steps[0].n == 1


def test_unknown_skill_filtered():
    llm = lambda p: '{"plan":[{"n":1,"skill":"teleport","input":"кудись"},{"n":2,"skill":"news","input":"новини"}]}'
    steps = _planner(llm).plan("ціль")
    assert [s.skill for s in steps] == ["news"]


def test_missing_fields_skipped():
    llm = lambda p: '{"plan":[{"n":1,"skill":"weather"},{"n":2,"skill":"news","input":"новини"}]}'
    steps = _planner(llm).plan("ціль")
    assert [s.skill for s in steps] == ["news"]


def test_max_steps_cap():
    items = ",".join('{"n":%d,"skill":"weather","input":"к%d"}' % (i, i) for i in range(1, 8))
    llm = lambda p: '{"plan":[' + items + ']}'
    steps = _planner(llm).plan("ціль")
    assert len(steps) == 3  # max_steps=3


def test_llm_error_returns_empty():
    def boom(p): raise RuntimeError("down")
    assert _planner(boom).plan("ціль") == []


def test_bad_json_returns_empty():
    assert _planner(lambda p: "тут немає json").plan("ціль") == []


def test_single_quote_json():
    llm = lambda p: "{'plan': [{'n':1,'skill':'weather','input':'погода'}]}"
    steps = _planner(llm).plan("ціль")
    assert len(steps) == 1 and steps[0].skill == "weather"


# ----------------------------------------------------------------- Executor
class FakeJarvis:
    def __init__(self, speak_map=None, ret_map=None, raise_on=None):
        self.enqueue_speech = lambda t: None
        self.cancel_event = threading.Event()
        self.working_memory = None
        self.calls = []
        self._speak_map = speak_map or {}
        self._ret_map = ret_map or {}
        self._raise_on = raise_on
    def execute_command(self, cmd):
        self.calls.append(cmd)
        if self._raise_on and self._raise_on in cmd:
            raise RuntimeError("skill crashed")
        spoken = self._speak_map.get(cmd)
        if spoken:
            self.enqueue_speech(spoken)
        return self._ret_map.get(cmd, "CONTINUE")


def test_executor_empty_plan():
    assert Executor().run([], FakeJarvis()) == []


def test_executor_captures_spoken_output():
    plan = [Step(n=1, skill="weather", input="яка погода")]
    j = FakeJarvis(speak_map={"яка погода": "Сонячно, 20 градусів"})
    res = Executor().run(plan, j)
    assert len(res) == 1 and res[0].ok is True
    assert "Сонячно" in res[0].output


def test_executor_appends_return_value():
    plan = [Step(n=1, skill="news", input="новини")]
    j = FakeJarvis(ret_map={"новини": "Топ-новина дня"})
    res = Executor().run(plan, j)
    assert "Топ-новина дня" in res[0].output


def test_executor_placeholder_substitution():
    plan = [Step(n=1, skill="weather", input="яка погода"),
            Step(n=2, skill="news", input="підсумуй {1}")]
    j = FakeJarvis(speak_map={"яка погода": "Дощить"})
    Executor().run(plan, j)
    # другий крок отримав підставлений вивід першого
    assert j.calls[1] == "підсумуй Дощить"


def test_executor_cancellation():
    plan = [Step(n=1, skill="weather", input="к1")]
    j = FakeJarvis()
    j.cancel_event.set()
    res = Executor().run(plan, j)
    assert len(res) == 1 and res[0].ok is False and res[0].error == "cancelled"
    assert j.calls == []  # до виконання не дійшло


def test_executor_step_exception_marks_fail():
    plan = [Step(n=1, skill="weather", input="впади")]
    j = FakeJarvis(raise_on="впади")
    res = Executor().run(plan, j)
    assert res[0].ok is False and "skill crashed" in (res[0].error or "")


def test_substitute_static():
    sr = StepResult(step=Step(n=1, skill="x", input="i"), output="РЕЗУЛЬТАТ")
    out = Executor()._substitute("узагальни {1}", [sr])
    assert out == "узагальни РЕЗУЛЬТАТ"


# ------------------------------------------------------- build_route_descriptions
def test_build_route_descriptions():
    routes = build_route_descriptions(
        triggers={"weather": ["погода"], "news": ["новини"]},
        dynamic_skills=[{"name": "plan", "triggers": ["склади план"]}],
    )
    assert "weather" in routes and "news" in routes
    assert "skill:plan" in routes and "склади план" in routes["skill:plan"]

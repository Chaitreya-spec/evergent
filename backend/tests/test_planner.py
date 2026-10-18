"""Plan parsing and DAG validation."""

from __future__ import annotations

import pytest

from app.core.errors import ValidationError
from app.models.enums import TaskType
from app.services.orchestrator.planner import PlannedTask, parse_plan, validate_plan
from app.services.providers.base import extract_json


def task(key: str, *, depends: list[str] | None = None, owns: list[str] | None = None):
    return PlannedTask(
        key=key,
        title=f"Task {key}",
        description="do the thing",
        task_type=TaskType.BACKEND,
        depends_on=depends or [],
        owns=owns or [],
    )


class TestExtractJson:
    def test_bare_object(self) -> None:
        assert extract_json('{"a": 1}') == {"a": 1}

    def test_fenced_block(self) -> None:
        assert extract_json('```json\n{"a": 1}\n```') == {"a": 1}

    def test_wrapped_in_prose(self) -> None:
        assert extract_json('Sure:\n{"a": [1, 2]}\nHope that helps.') == {"a": [1, 2]}

    def test_raises_rather_than_half_parsing(self) -> None:
        with pytest.raises(Exception, match="parsable JSON"):
            extract_json("no json here at all")


class TestValidatePlan:
    def test_accepts_well_formed_dag(self) -> None:
        plan = [
            task("t1", owns=["index.html"]),
            task("t2", depends=["t1"], owns=["styles.css"]),
        ]
        assert validate_plan(plan) == []

    def test_rejects_two_tasks_owning_one_file(self) -> None:
        plan = [task("t1", owns=["app.py"]), task("t2", owns=["app.py"])]
        problems = validate_plan(plan)
        assert len(problems) == 1
        assert "claimed by both" in problems[0]

    def test_detects_direct_cycle(self) -> None:
        plan = [task("t1", depends=["t2"]), task("t2", depends=["t1"])]
        assert any("cycle" in p for p in validate_plan(plan))

    def test_detects_indirect_cycle(self) -> None:
        plan = [
            task("t1", depends=["t3"]),
            task("t2", depends=["t1"]),
            task("t3", depends=["t2"]),
        ]
        assert any("cycle" in p for p in validate_plan(plan))

    def test_detects_dangling_dependency(self) -> None:
        assert any("unknown task" in p for p in validate_plan([task("t1", depends=["ghost"])]))

    def test_detects_self_dependency(self) -> None:
        assert any("itself" in p for p in validate_plan([task("t1", depends=["t1"])]))

    def test_detects_duplicate_keys(self) -> None:
        assert any("duplicate" in p for p in validate_plan([task("t1"), task("t1")]))

    def test_unknown_agent_is_rejected(self) -> None:
        plan = [task("t1")]
        plan[0].agent = "wizard"
        problems = validate_plan(plan, {"frontend", "backend"})
        assert any('unknown agent "wizard"' in p for p in problems)

    def test_known_agent_passes(self) -> None:
        plan = [task("t1")]
        plan[0].agent = "backend"
        assert validate_plan(plan, {"frontend", "backend"}) == []

    def test_diamond_is_valid(self) -> None:
        plan = [
            task("t1", owns=["a"]),
            task("t2", depends=["t1"], owns=["b"]),
            task("t3", depends=["t1"], owns=["c"]),
            task("t4", depends=["t2", "t3"], owns=["d"]),
        ]
        assert validate_plan(plan) == []


class TestParsePlan:
    def test_parses_a_minimal_plan(self) -> None:
        tasks = parse_plan(
            {
                "tasks": [
                    {
                        "key": "t1",
                        "title": "Build it",
                        "description": "the details",
                        "task_type": "frontend",
                        "owns": ["index.html"],
                    }
                ]
            }
        )
        assert tasks[0].task_type is TaskType.FRONTEND
        assert tasks[0].owns == ["index.html"]

    def test_unknown_task_type_falls_back_rather_than_failing(self) -> None:
        tasks = parse_plan(
            {
                "tasks": [
                    {
                        "key": "t1",
                        "title": "t",
                        "description": "d",
                        "task_type": "interpretive_dance",
                    }
                ]
            }
        )
        # Unclassified work goes to the general engineer, not a specialist.
        assert tasks[0].task_type is TaskType.CODING

    def test_agent_assignment_is_parsed(self) -> None:
        tasks = parse_plan(
            {
                "tasks": [
                    {
                        "key": "t1",
                        "title": "Design the schema",
                        "description": "d",
                        "task_type": "database",
                        "agent": "database",
                    }
                ]
            }
        )
        assert tasks[0].agent == "database"

    def test_missing_agent_is_left_for_the_engine_to_resolve(self) -> None:
        tasks = parse_plan(
            {"tasks": [{"key": "t1", "title": "t", "description": "d"}]}
        )
        assert tasks[0].agent is None

    def test_rejects_missing_tasks_array(self) -> None:
        with pytest.raises(ValidationError):
            parse_plan({"nope": []})

    def test_rejects_empty_plan(self) -> None:
        with pytest.raises(ValidationError):
            parse_plan({"tasks": []})

    def test_rejects_incomplete_task(self) -> None:
        with pytest.raises(ValidationError):
            parse_plan({"tasks": [{"key": "t1", "title": "", "description": ""}]})

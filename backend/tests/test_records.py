"""Conversations, memories, reviews and tool calls through the API."""

from __future__ import annotations

import uuid

from httpx import AsyncClient

from app.models.entities import Review, Task, ToolCall


async def _project(client: AsyncClient, name: str = "Records") -> str:
    return (await client.post("/api/v1/projects", json={"name": name})).json()["id"]


async def _workflow(client: AsyncClient, project_id: str, goal: str, **extra) -> dict:
    response = await client.post(
        "/api/v1/workflows",
        json={"project_id": project_id, "goal": goal, "autostart": False, **extra},
    )
    assert response.status_code == 201, response.text
    return response.json()


class TestConversations:
    async def test_a_request_opens_a_thread_and_a_follow_up_joins_it(
        self, auth_client: AsyncClient
    ) -> None:
        project_id = await _project(auth_client)
        first = await _workflow(auth_client, project_id, "Build a booking site")
        assert first["conversation_id"]

        second = await _workflow(
            auth_client, project_id, "Now add email reminders",
            conversation_id=first["conversation_id"],
        )  # fmt: skip
        assert second["conversation_id"] == first["conversation_id"]

        thread = (
            await auth_client.get(f"/api/v1/conversations/{first['conversation_id']}")
        ).json()
        assert [m["content"] for m in thread["messages"]] == [
            "Build a booking site",
            "Now add email reminders",
        ]
        assert thread["messages"][1]["meta"]["workflow_id"] == second["id"]

        listed = (
            await auth_client.get(f"/api/v1/projects/{project_id}/conversations")
        ).json()
        assert [c["id"] for c in listed] == [first["conversation_id"]]

    async def test_a_thread_cannot_be_continued_from_another_project(
        self, auth_client: AsyncClient
    ) -> None:
        a = await _project(auth_client, "A")
        b = await _project(auth_client, "B")
        thread = (await _workflow(auth_client, a, "Build a booking site"))[
            "conversation_id"
        ]

        response = await auth_client.post(
            "/api/v1/workflows",
            json={"project_id": b, "goal": "Now add reminders", "conversation_id": thread,
                  "autostart": False},
        )  # fmt: skip
        assert response.status_code == 404

    async def test_someone_elses_thread_is_404(
        self, auth_client: AsyncClient, client: AsyncClient
    ) -> None:
        thread = (
            await _workflow(
                auth_client, await _project(auth_client), "Build a booking site"
            )
        )["conversation_id"]
        other = await client.post(
            "/api/v1/auth/register",
            json={"email": "other@example.com", "password": "long-enough-password"},
        )
        response = await client.get(
            f"/api/v1/conversations/{thread}",
            headers={"Authorization": f"Bearer {other.json()['access_token']}"},
        )
        assert response.status_code == 404

    async def test_deleting_a_thread_keeps_its_workflows(
        self, auth_client: AsyncClient
    ) -> None:
        workflow = await _workflow(
            auth_client, await _project(auth_client), "Build a booking site"
        )
        assert (
            await auth_client.delete(
                f"/api/v1/conversations/{workflow['conversation_id']}"
            )
        ).status_code == 204
        assert (
            await auth_client.get(f"/api/v1/workflows/{workflow['id']}")
        ).status_code == 200


class TestMemories:
    async def test_add_search_and_forget(self, auth_client: AsyncClient) -> None:
        project_id = await _project(auth_client)
        base = f"/api/v1/projects/{project_id}/memories"

        added = await auth_client.post(
            base, json={"content": "Always use Tailwind for styling"}
        )
        assert added.status_code == 201
        assert added.json()["scope"] == "preference"
        # No embedding provider in the test environment.
        assert added.json()["embedded"] is False

        hits = (
            await auth_client.get(
                base + "/search", params={"q": "which styling framework?"}
            )
        ).json()
        assert hits[0]["content"] == "Always use Tailwind for styling"
        assert hits[0]["method"] == "keyword"

        memory_id = added.json()["id"]
        assert (await auth_client.delete(f"{base}/{memory_id}")).status_code == 204
        assert (await auth_client.delete(f"{base}/{memory_id}")).status_code == 404
        assert (await auth_client.get(base)).json() == []

    async def test_reindex_says_why_when_it_cannot(
        self, auth_client: AsyncClient
    ) -> None:
        project_id = await _project(auth_client)
        body = (
            await auth_client.post(f"/api/v1/projects/{project_id}/memories/reindex")
        ).json()
        assert body["reindexed"] == 0 and "no embedding provider" in body["reason"]


class TestTaskRecords:
    async def test_reviews_and_tool_calls_are_readable(
        self, auth_client: AsyncClient, session
    ) -> None:
        workflow = await _workflow(
            auth_client, await _project(auth_client), "Build a booking site"
        )
        created = (
            await auth_client.post(
                "/api/v1/tasks",
                json={
                    "workflow_id": workflow["id"],
                    "key": "q1",
                    "title": "Test it",
                    "description": "Write tests",
                    "task_type": "testing",
                },
            )
        ).json()
        task_id = uuid.UUID(created["id"])
        session.add(
            Review(
                task_id=task_id,
                reviewer_agent="reviewer",
                status="needs_revision",
                score=4.0,
                feedback=[{"message": "missing edge case"}],
                summary="close",
            )
        )
        session.add(
            ToolCall(
                task_id=task_id,
                tool_name="run_tests",
                arguments={"command": "pytest"},
                result={"exit_code": 1, "output": "1 failed"},
                succeeded=False,
            )
        )
        await session.commit()
        assert await session.get(Task, task_id)

        reviews = (await auth_client.get(f"/api/v1/tasks/{task_id}/reviews")).json()
        calls = (await auth_client.get(f"/api/v1/tasks/{task_id}/tool-calls")).json()

        assert reviews[0]["status"] == "needs_revision"
        assert calls[0]["tool_name"] == "run_tests"
        assert calls[0]["result"]["output"] == "1 failed"

"""Tests for Cadence FastAPI HTTP endpoints."""

import pytest
from fastapi.testclient import TestClient

from cadence.api.main import app
from cadence.api.store import run_store


@pytest.fixture(autouse=True)
def clean_store():
    """Ensure in-memory run_store is empty before and after each test."""
    run_store.clear()
    yield
    run_store.clear()


@pytest.fixture
def client():
    """FastAPI test client instance."""
    return TestClient(app)


def test_health_check(client: TestClient):
    """GET /health returns 200 and {'status': 'ok'}."""
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_generate_network_success(client: TestClient):
    """POST /networks/generate returns 201 with valid run_id and metadata."""
    payload = {
        "profile_name": "metro",
        "seed": 42,
        "section_count": 10,
        "train_count": 10,
        "task_count": 5,
    }
    response = client.post("/networks/generate", json=payload)
    assert response.status_code == 201
    data = response.json()
    assert "run_id" in data
    assert len(data["run_id"]) > 0
    assert data["profile_name"] == "metro"
    assert data["seed"] == 42
    assert data["section_count"] == 10
    assert data["train_count"] == 10
    assert data["task_count"] == 5


def test_generate_network_unknown_profile(client: TestClient):
    """POST /networks/generate with unknown profile returns 400."""
    payload = {
        "profile_name": "hyperloop_invalid",
        "seed": 42,
    }
    response = client.post("/networks/generate", json=payload)
    assert response.status_code == 400
    data = response.json()
    assert "detail" in data
    assert "Unknown network profile" in data["detail"]


def test_get_network_success(client: TestClient):
    """GET /networks/{run_id} returns 200 with sections, adjacencies, trains, and tasks."""
    gen_resp = client.post(
        "/networks/generate",
        json={"profile_name": "local", "seed": 100, "section_count": 8, "train_count": 6, "task_count": 4},
    )
    assert gen_resp.status_code == 201
    run_id = gen_resp.json()["run_id"]

    response = client.get(f"/networks/{run_id}")
    assert response.status_code == 200
    data = response.json()
    assert data["run_id"] == run_id
    assert data["profile_name"] == "local"
    assert len(data["sections"]) == 8
    assert len(data["train_slots"]) == 6
    assert len(data["maintenance_tasks"]) == 4
    assert len(data["adjacencies"]) > 0


def test_get_network_not_found(client: TestClient):
    """GET /networks/{nonexistent_id} returns 404."""
    response = client.get("/networks/nonexistent-uuid-12345")
    assert response.status_code == 404
    assert "detail" in response.json()


def test_solve_not_found(client: TestClient):
    """POST /solve on nonexistent run returns 404."""
    response = client.post("/solve", json={"run_id": "nonexistent-uuid-12345", "time_limit_seconds": 5})
    assert response.status_code == 404
    assert "detail" in response.json()


def test_solve_success(client: TestClient):
    """POST /solve on valid run returns 200 with status in (OPTIMAL, FEASIBLE) and scheduled_blocks."""
    gen_resp = client.post(
        "/networks/generate",
        json={"profile_name": "metro", "seed": 42, "section_count": 8, "train_count": 5, "task_count": 3},
    )
    assert gen_resp.status_code == 201
    run_id = gen_resp.json()["run_id"]

    solve_resp = client.post("/solve", json={"run_id": run_id, "time_limit_seconds": 15})
    assert solve_resp.status_code == 200
    data = solve_resp.json()
    assert data["run_id"] == run_id
    assert data["status"] in ("OPTIMAL", "FEASIBLE")
    assert isinstance(data["scheduled_blocks"], list)
    assert "wall_time_seconds" in data
    assert "objective_value" in data


def test_ripple_before_solve(client: TestClient):
    """GET /ripple/{run_id} before solve returns 400."""
    gen_resp = client.post(
        "/networks/generate",
        json={"profile_name": "metro", "seed": 42, "section_count": 6, "train_count": 4, "task_count": 2},
    )
    run_id = gen_resp.json()["run_id"]

    response = client.get(f"/ripple/{run_id}")
    assert response.status_code == 400
    assert "detail" in response.json()
    assert "No solve has been executed" in response.json()["detail"]


def test_ripple_not_found(client: TestClient):
    """GET /ripple/{nonexistent_id} returns 404."""
    response = client.get("/ripple/nonexistent-uuid-12345")
    assert response.status_code == 404
    assert "detail" in response.json()


def test_ripple_after_solve(client: TestClient):
    """GET /ripple/{run_id} after solve returns 200 with total_trains_affected, delay, summary."""
    gen_resp = client.post(
        "/networks/generate",
        json={"profile_name": "metro", "seed": 42, "section_count": 8, "train_count": 8, "task_count": 4},
    )
    run_id = gen_resp.json()["run_id"]
    client.post("/solve", json={"run_id": run_id, "time_limit_seconds": 15})

    response = client.get(f"/ripple/{run_id}")
    assert response.status_code == 200
    data = response.json()
    assert data["run_id"] == run_id
    assert "total_trains_affected" in data
    assert "total_delay_minutes" in data
    assert "per_train_impacts" in data
    assert "summary" in data
    assert isinstance(data["per_train_impacts"], list)
    assert isinstance(data["summary"], str)


def test_reason_before_solve(client: TestClient):
    """GET /reason/{run_id} before solve returns 400."""
    gen_resp = client.post(
        "/networks/generate",
        json={"profile_name": "local", "seed": 42, "section_count": 6, "train_count": 4, "task_count": 2},
    )
    run_id = gen_resp.json()["run_id"]

    response = client.get(f"/reason/{run_id}")
    assert response.status_code == 400
    assert "detail" in response.json()
    assert "No solve has been executed" in response.json()["detail"]


def test_reason_not_found(client: TestClient):
    """GET /reason/{nonexistent_id} returns 404."""
    response = client.get("/reason/nonexistent-uuid-12345")
    assert response.status_code == 404
    assert "detail" in response.json()


def test_reason_after_solve(client: TestClient):
    """GET /reason/{run_id} after solve returns 200 with explanations list and summary."""
    gen_resp = client.post(
        "/networks/generate",
        json={"profile_name": "local", "seed": 7, "section_count": 8, "train_count": 6, "task_count": 3},
    )
    run_id = gen_resp.json()["run_id"]
    client.post("/solve", json={"run_id": run_id, "time_limit_seconds": 15})

    response = client.get(f"/reason/{run_id}")
    assert response.status_code == 200
    data = response.json()
    assert data["run_id"] == run_id
    assert "explanations" in data
    assert isinstance(data["explanations"], list)
    assert len(data["explanations"]) == 3
    for exp in data["explanations"]:
        assert "run_id" in exp
        assert "task_id" in exp
        assert "explanation" in exp
        assert "summary" in exp


def test_reason_single_task_success_and_not_found(client: TestClient):
    """GET /reason/{run_id}/{task_id} returns 200 for valid task and 404 for invalid."""
    gen_resp = client.post(
        "/networks/generate",
        json={"profile_name": "mainline", "seed": 12, "section_count": 8, "train_count": 6, "task_count": 3},
    )
    run_id = gen_resp.json()["run_id"]
    client.post("/solve", json={"run_id": run_id, "time_limit_seconds": 15})

    # Fetch network to get a valid task_id
    net_resp = client.get(f"/networks/{run_id}")
    tasks = net_resp.json()["maintenance_tasks"]
    assert len(tasks) > 0
    valid_task_id = tasks[0]["id"]

    # Valid task explanation
    resp = client.get(f"/reason/{run_id}/{valid_task_id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["run_id"] == run_id
    assert data["task_id"] == valid_task_id
    assert "explanation" in data
    assert "summary" in data

    # Nonexistent task_id on valid run
    resp_invalid = client.get(f"/reason/{run_id}/task-nonexistent-xyz")
    assert resp_invalid.status_code == 404
    assert "detail" in resp_invalid.json()


def test_full_end_to_end_chain(client: TestClient):
    """Full end-to-end chain: generate -> get_network -> solve -> ripple -> reason.
    
    Confirms data flows cleanly across all stages via run_id.
    """
    # 1. Generate
    gen_resp = client.post(
        "/networks/generate",
        json={
            "profile_name": "metro",
            "seed": 99,
            "section_count": 10,
            "train_count": 8,
            "task_count": 4,
        },
    )
    assert gen_resp.status_code == 201
    run_id = gen_resp.json()["run_id"]

    # 2. Get Network
    net_resp = client.get(f"/networks/{run_id}")
    assert net_resp.status_code == 200
    net_data = net_resp.json()
    assert len(net_data["sections"]) == 10
    assert len(net_data["maintenance_tasks"]) == 4

    # 3. Solve
    solve_resp = client.post("/solve", json={"run_id": run_id, "time_limit_seconds": 20})
    assert solve_resp.status_code == 200
    solve_data = solve_resp.json()
    assert solve_data["status"] in ("OPTIMAL", "FEASIBLE")

    # 4. Ripple
    ripple_resp = client.get(f"/ripple/{run_id}")
    assert ripple_resp.status_code == 200
    ripple_data = ripple_resp.json()
    assert "total_trains_affected" in ripple_data
    assert "total_delay_minutes" in ripple_data
    assert "summary" in ripple_data

    # 5. Reason
    reason_resp = client.get(f"/reason/{run_id}")
    assert reason_resp.status_code == 200
    reason_data = reason_resp.json()
    assert len(reason_data["explanations"]) == 4
    for exp in reason_data["explanations"]:
        assert len(exp["summary"]) > 0
        assert "task_id" in exp["explanation"]

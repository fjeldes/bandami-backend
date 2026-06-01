"""
API integration tests for writing evaluation flow.
Uses MockAIProvider from conftest (no real API calls).
"""

import pytest


class TestWritingExam:
    def test_create_writing_exam(self, client, auth_headers):
        res = client.post("/api/v1/evaluate/writing/exam", json={
            "exam_type": "writing",
            "task_type": "task2",
            "attempt_number": 1,
        }, headers=auth_headers)
        assert res.status_code == 200
        assert res.json()["status"] == "pending"
        assert res.json()["exam_type"] == "writing"
        assert res.json()["task_type"] == "task2"

    def test_create_exam_missing_field(self, client, auth_headers):
        res = client.post("/api/v1/evaluate/writing/exam", json={}, headers=auth_headers)
        assert res.status_code == 422

    def test_create_exam_requires_auth(self, client):
        res = client.post("/api/v1/evaluate/writing/exam", json={"exam_type": "writing"})
        assert res.status_code == 401


class TestWritingEvaluation:
    def test_full_evaluation_flow(self, client, auth_headers):
        exam_res = client.post("/api/v1/evaluate/writing/exam", json={
            "exam_type": "writing",
            "task_type": "task2",
        }, headers=auth_headers)
        assert exam_res.status_code == 200
        exam_id = exam_res.json()["id"]

        eval_res = client.post("/api/v1/evaluate/writing/", json={
            "exam_id": exam_id,
            "text": "Community service should be mandatory in high schools. I strongly agree with this statement for several reasons. First, it teaches students valuable life skills like responsibility and teamwork. Second, it benefits society by providing volunteer support to organizations that need it. In conclusion, mandatory community service is a positive policy.",
        }, headers=auth_headers)
        assert eval_res.status_code == 200
        data = eval_res.json()
        assert data["overall_band"] == 7.0
        assert data["provider_used"] == "gemini"
        assert "exam_id" in data

    def test_get_evaluation_result(self, client, auth_headers):
        exam_res = client.post("/api/v1/evaluate/writing/exam", json={
            "exam_type": "writing",
            "task_type": "task2",
        }, headers=auth_headers)
        exam_id = exam_res.json()["id"]

        client.post("/api/v1/evaluate/writing/", json={
            "exam_id": exam_id,
            "text": "Community service should be mandatory in high schools because it helps students learn valuable life skills and also benefits the local community in many important ways.",
        }, headers=auth_headers)

        get_res = client.get(f"/api/v1/evaluate/writing/{exam_id}/evaluation", headers=auth_headers)
        assert get_res.status_code == 200
        assert get_res.json()["overall_band"] == 7.0

    def test_already_processed_exam_rejected(self, client, auth_headers):
        exam_res = client.post("/api/v1/evaluate/writing/exam", json={
            "exam_type": "writing",
            "task_type": "task2",
        }, headers=auth_headers)
        exam_id = exam_res.json()["id"]

        r1 = client.post("/api/v1/evaluate/writing/", json={
            "exam_id": exam_id,
            "text": "Community service should be mandatory in high schools because it helps students learn valuable life skills and also benefits the local community in many important ways.",
        }, headers=auth_headers)
        assert r1.status_code == 200

        r2 = client.post("/api/v1/evaluate/writing/", json={
            "exam_id": exam_id,
            "text": "Community service should be mandatory in high schools because it helps students learn valuable life skills and also benefits the local community.",
        }, headers=auth_headers)
        assert r2.status_code == 400

    def test_evaluation_requires_auth(self, client):
        res = client.post("/api/v1/evaluate/writing/", json={
            "exam_id": "00000000-0000-0000-0000-000000000001",
            "text": "no auth",
        })
        assert res.status_code == 401

    def test_nonexistent_exam_returns_404(self, client, auth_headers):
        res = client.get("/api/v1/evaluate/writing/00000000-0000-0000-0000-000000000999/evaluation",
                         headers=auth_headers)
        assert res.status_code == 404

    def test_other_users_exam_returns_404(self, client, auth_headers):
        exam_res = client.post("/api/v1/evaluate/writing/exam", json={
            "exam_type": "writing",
            "task_type": "task2",
        }, headers=auth_headers)
        exam_id = exam_res.json()["id"]

        no_auth_res = client.get(f"/api/v1/evaluate/writing/{exam_id}/evaluation")
        assert no_auth_res.status_code == 401

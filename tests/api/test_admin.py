"""
API integration tests for admin endpoints.
"""

import pytest


class TestAdminAuth:
    def test_admin_routes_require_admin_role(self, client, auth_headers):
        res = client.get("/api/v1/admin/stats", headers=auth_headers)
        assert res.status_code == 403

    def test_admin_routes_without_auth(self, client):
        res = client.get("/api/v1/admin/stats")
        assert res.status_code == 401

    def test_admin_stats_with_admin_role(self, client, admin_headers):
        res = client.get("/api/v1/admin/stats", headers=admin_headers)
        assert res.status_code == 200
        data = res.json()
        assert "total_users" in data
        assert "total_exams" in data

    def test_admin_users_list(self, client, admin_headers):
        res = client.get("/api/v1/admin/users", headers=admin_headers)
        assert res.status_code == 200
        assert "users" in res.json()
        assert "total" in res.json()

    def test_admin_user_search(self, client, admin_headers):
        res = client.get("/api/v1/admin/users?search=testuser", headers=admin_headers)
        assert res.status_code == 200

    def test_admin_plans_list(self, client, admin_headers):
        res = client.get("/api/v1/admin/plans", headers=admin_headers)
        assert res.status_code == 200
        plans = res.json()
        assert len(plans) >= 2

    def test_admin_exams_list(self, client, admin_headers):
        res = client.get("/api/v1/admin/exams", headers=admin_headers)
        assert res.status_code == 200
        assert "exams" in res.json()
        assert "total" in res.json()


class TestQuestionManagement:
    def test_admin_list_questions(self, client, admin_headers):
        res = client.get("/api/v1/admin/questions", headers=admin_headers)
        assert res.status_code == 200
        data = res.json()
        assert isinstance(data, dict)
        assert "questions" in data
        assert "total" in data
        assert "page" in data
        assert "per_page" in data
        assert "total_pages" in data
        assert "counts" in data
        assert isinstance(data["questions"], list)

    def test_admin_create_delete_question(self, client, admin_headers):
        create_res = client.post("/api/v1/admin/questions", json={
            "exam_type": "writing",
            "task_type": "task2",
            "difficulty": 3,
            "prompt_text": "Discuss the impact of technology on education.",
            "title": "Test Question",
        }, headers=admin_headers)
        assert create_res.status_code == 200
        q_id = create_res.json()["id"]

        patch_res = client.patch(f"/api/v1/admin/questions/{q_id}", json={
            "difficulty": 4,
        }, headers=admin_headers)
        assert patch_res.status_code == 200

        delete_res = client.delete(f"/api/v1/admin/questions/{q_id}", headers=admin_headers)
        assert delete_res.status_code == 200

    def test_non_admin_cannot_create_question(self, client, auth_headers):
        res = client.post("/api/v1/admin/questions", json={
            "exam_type": "writing",
            "difficulty": 1,
            "prompt_text": "Should not work.",
        }, headers=auth_headers)
        assert res.status_code == 403


class TestUserManagement:
    def test_admin_update_user_role(self, client, admin_headers, auth_headers, db_session):
        from app.models.user import UserProfile
        user = db_session.query(UserProfile).filter(UserProfile.role != "admin").first()
        res = client.patch(f"/api/v1/admin/users/{user.id}", json={
            "subscription_tier": "premium",
        }, headers=admin_headers)
        assert res.status_code == 200
        assert res.json()["status"] == "ok"

    def test_admin_update_nonexistent_user(self, client, admin_headers):
        res = client.patch("/api/v1/admin/users/00000000-0000-0000-0000-000000000999", json={
            "role": "admin",
        }, headers=admin_headers)
        assert res.status_code == 404

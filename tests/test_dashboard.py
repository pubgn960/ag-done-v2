"""
Unit tests for Web Dashboard, Telegram Mini-App Authentication, and Database Metrics.
"""

import unittest
import pytest
from datetime import datetime, timezone
from starlette.testclient import TestClient

from config import Config
from database import (
    init_db,
    create_order,
    update_order_status,
    update_order_price,
    delete_orders_by_email,
    get_dashboard_overview_metrics,
    get_dashboard_orders_paginated,
    get_dashboard_groups_summary,
    get_dashboard_loaders_summary,
    get_dashboard_daily_trends
)
from web.auth import (
    verify_telegram_webapp_data,
    create_session_token,
    verify_session_token
)
from web.app import app


class TestDashboardAuthAndSession(unittest.TestCase):
    """Tests for Session Token creation, signature verification, and Telegram WebApp HMAC."""

    def test_session_token_lifecycle(self):
        # 1. Create valid session token
        token = create_session_token("admin_user", max_age_seconds=3600)
        self.assertIsNotNone(token)
        self.assertIn("admin_user", token)

        # 2. Verify valid token
        self.assertTrue(verify_session_token(token))

        # 3. Corrupt signature -> should fail
        corrupted = token[:-4] + "abcd"
        self.assertFalse(verify_session_token(corrupted))

        # 4. None or empty -> should fail
        self.assertFalse(verify_session_token(None))
        self.assertFalse(verify_session_token(""))

    def test_telegram_webapp_hmac_verification(self):
        import hmac
        import hashlib
        import urllib.parse
        import json
        import time

        bot_token = "123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11"
        user_data = json.dumps({"id": 1573531032, "first_name": "Admin", "username": "superadmin"})
        auth_date = str(int(time.time()))

        params = {
            "auth_date": auth_date,
            "query_id": "AAHdF6IQAAAAAN0XohDhrOrc",
            "user": user_data
        }

        data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(params.items()))
        secret_key = hmac.new(b"WebAppData", bot_token.encode("utf-8"), hashlib.sha256).digest()
        calculated_hash = hmac.new(secret_key, data_check_string.encode("utf-8"), hashlib.sha256).hexdigest()

        params["hash"] = calculated_hash
        init_data_raw = urllib.parse.urlencode(params)

        # Valid test
        result = verify_telegram_webapp_data(init_data_raw, bot_token=bot_token)
        self.assertIsNotNone(result)
        self.assertEqual(result.get("id"), 1573531032)
        self.assertTrue(result.get("is_admin"))

        # Invalid token test -> should return None
        bad_result = verify_telegram_webapp_data(init_data_raw, bot_token="wrong_token_here")
        self.assertIsNone(bad_result)


@pytest.mark.asyncio
class TestDashboardDatabaseMetrics:
    """Tests for dashboard metrics queries."""

    async def test_dashboard_metrics_aggregation(self):
        await init_db()
        email = "dash_metric_test@example.com"
        await delete_orders_by_email(email)

        # Create orders in different statuses
        ord1 = await create_order(email=email, client_chat_id=-100111222, package="5040")
        await update_order_price(ord1.id, "32.5")
        await update_order_status(ord1.id, "Delivered")

        ord2 = await create_order(email=email, client_chat_id=-100111222, package="10800")
        await update_order_price(ord2.id, "65")
        await update_order_status(ord2.id, "Pending")

        ord3 = await create_order(email=email, client_chat_id=-100111222, package="21600")
        await update_order_price(ord3.id, "130")
        await update_order_status(ord3.id, "CANCELLED")

        metrics = await get_dashboard_overview_metrics()
        assert metrics["total_orders"] >= 3
        assert metrics["delivered_orders"] >= 1
        assert metrics["pending_orders"] >= 1
        assert metrics["cancelled_orders"] >= 1
        assert metrics["total_delivered_volume"] >= 32.5

        # Test paginated query
        p_orders = await get_dashboard_orders_paginated(page=1, page_size=10, search="dash_metric_test")
        assert p_orders["total"] >= 3
        assert len(p_orders["items"]) >= 3

        # Test daily trends
        trends = await get_dashboard_daily_trends(days=7)
        assert len(trends["labels"]) == 7
        assert len(trends["delivered"]) == 7
        assert len(trends["cancelled"]) == 7

        await delete_orders_by_email(email)


class TestDashboardWebEndpoints(unittest.TestCase):
    """Tests for FastAPI HTTP endpoints."""

    def setUp(self):
        self.client = TestClient(app)

    def test_health_check_endpoint(self):
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json().get("status"), "ok")

    def test_login_page_renders(self):
        response = self.client.get("/login")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Admin Authorization", response.text)

    def test_unauthenticated_dashboard_redirects_to_login(self):
        response = self.client.get("/", follow_redirects=False)
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.headers["location"].endswith("/login"))

    def test_login_with_valid_password(self):
        valid_password = Config.DASHBOARD_ADMIN_PASSWORD
        response = self.client.post("/login", data={"password": valid_password}, follow_redirects=False)
        self.assertEqual(response.status_code, 302)
        self.assertIn("admin_session", response.cookies)

        # Access dashboard with session cookie
        auth_cookie = response.cookies.get("admin_session")
        self.client.cookies.set("admin_session", auth_cookie)
        dash_response = self.client.get("/")
        self.assertEqual(dash_response.status_code, 200)
        self.assertIn("SmartBot V2", dash_response.text)

    def test_api_endpoints_with_auth(self):
        token = create_session_token("admin")
        self.client.cookies.set("admin_session", token)

        # 1. /api/metrics
        m_res = self.client.get("/api/metrics")
        self.assertEqual(m_res.status_code, 200)
        self.assertIn("total_orders", m_res.json())

        # 2. /api/orders
        o_res = self.client.get("/api/orders?page=1&page_size=5")
        self.assertEqual(o_res.status_code, 200)
        self.assertIn("items", o_res.json())

        # 3. /api/groups
        g_res = self.client.get("/api/groups")
        self.assertEqual(g_res.status_code, 200)

        # 4. /api/loaders
        l_res = self.client.get("/api/loaders")
        self.assertEqual(l_res.status_code, 200)

        # 5. /api/trends
        t_res = self.client.get("/api/trends?days=7")
        self.assertEqual(t_res.status_code, 200)
        self.assertIn("labels", t_res.json())


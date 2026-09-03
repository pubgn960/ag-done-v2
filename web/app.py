"""
FastAPI Web Application for Telegram Email Delivery Bot.
Serves Web Dashboard, Telegram Mini-App, and JSON REST API.
"""

import os
import logging
from typing import Optional
from fastapi import FastAPI, Request, Response, Form, Depends, HTTPException, Query, status
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles

from config import Config
from database import (
    get_dashboard_overview_metrics,
    get_dashboard_orders_paginated,
    get_dashboard_groups_summary,
    get_dashboard_loaders_summary,
    get_dashboard_daily_trends
)
from web.auth import verify_telegram_webapp_data, create_session_token, verify_session_token

logger = logging.getLogger(__name__)

app = FastAPI(
    title="Telegram Delivery Bot Live Dashboard",
    version="2.0.0",
    description="Real-Time Analytics, Order Tracking, and Management"
)

# Jinja2 Templates directory
templates_path = os.path.join(os.path.dirname(__file__), "templates")
templates = Jinja2Templates(directory=templates_path)


def is_authenticated(request: Request) -> bool:
    """Checks if request has valid admin session cookie."""
    token = request.cookies.get("admin_session")
    return verify_session_token(token)


@app.get("/health")
async def health_check():
    """Health check endpoint for monitoring."""
    return {"status": "ok", "app": "Telegram Email Delivery Bot Dashboard"}


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, error: Optional[str] = None):
    """Renders dashboard login form."""
    if is_authenticated(request):
        return RedirectResponse(url="/", status_code=status.HTTP_302_FOUND)
    return templates.TemplateResponse(request=request, name="login.html", context={"error": error})


@app.post("/login")
async def login_submit(request: Request, password: str = Form(...)):
    """Processes admin login password submission."""
    expected_password = Config.DASHBOARD_ADMIN_PASSWORD
    if password.strip() == expected_password.strip():
        token = create_session_token("admin")
        response = RedirectResponse(url="/", status_code=status.HTTP_302_FOUND)
        response.set_cookie(
            key="admin_session",
            value=token,
            max_age=86400 * 30, # 30 days
            httponly=True,
            samesite="lax",
            secure=False # Set to False for local dev, Railway handles SSL termination
        )
        return response
    return templates.TemplateResponse(request=request, name="login.html", context={"error": "Invalid Admin Password. Please try again."})


@app.get("/logout")
async def logout():
    """Clears admin session and redirects to login."""
    response = RedirectResponse(url="/login", status_code=status.HTTP_302_FOUND)
    response.delete_cookie("admin_session")
    return response


@app.get("/", response_class=HTMLResponse)
async def dashboard_home(request: Request):
    """Main Dashboard View (Renders index.html)."""
    if not is_authenticated(request):
        return RedirectResponse(url="/login", status_code=status.HTTP_302_FOUND)

    metrics = await get_dashboard_overview_metrics()
    groups = await get_dashboard_groups_summary()
    loaders = await get_dashboard_loaders_summary()
    initial_orders = await get_dashboard_orders_paginated(page=1, page_size=15)
    trends = await get_dashboard_daily_trends(days=7)

    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "metrics": metrics,
            "groups": groups,
            "loaders": loaders,
            "orders": initial_orders,
            "trends": trends,
            "is_mini_app": False
        }
    )


@app.get("/webapp", response_class=HTMLResponse)
async def telegram_webapp_entry(request: Request):
    """
    Entry point for Telegram Mini-App (opened via /dashboard in Telegram).
    Frontend JavaScript sends initData for automatic cryptographic verification.
    """
    metrics = await get_dashboard_overview_metrics()
    groups = await get_dashboard_groups_summary()
    loaders = await get_dashboard_loaders_summary()
    initial_orders = await get_dashboard_orders_paginated(page=1, page_size=15)
    trends = await get_dashboard_daily_trends(days=7)

    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "metrics": metrics,
            "groups": groups,
            "loaders": loaders,
            "orders": initial_orders,
            "trends": trends,
            "is_mini_app": True
        }
    )


# ==========================================
# REST API ENDPOINTS (FOR LIVE UPDATES)
# ==========================================

@app.post("/api/auth/webapp")
async def api_auth_webapp(request: Request):
    """Validates Telegram WebApp initData and sets session cookie."""
    try:
        body = await request.json()
        init_data = body.get("initData", "")
        user = verify_telegram_webapp_data(init_data)
        if user and user.get("is_admin"):
            token = create_session_token(str(user.get("id", "telegram_admin")))
            response = JSONResponse({"success": True, "user": user})
            response.set_cookie(
                key="admin_session",
                value=token,
                max_age=86400 * 30,
                httponly=True,
                samesite="lax"
            )
            return response
        return JSONResponse({"success": False, "error": "Unauthorized Telegram User"}, status_code=401)
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)}, status_code=400)


@app.get("/api/metrics")
async def api_get_metrics(request: Request):
    """Returns real-time overview metrics JSON."""
    if not is_authenticated(request):
        raise HTTPException(status_code=401, detail="Unauthorized")
    metrics = await get_dashboard_overview_metrics()
    return metrics


@app.get("/api/orders")
async def api_get_orders(
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(15, ge=1, le=100),
    search: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    client_chat_id: Optional[int] = Query(None)
):
    """Returns paginated, searchable orders JSON."""
    if not is_authenticated(request):
        raise HTTPException(status_code=401, detail="Unauthorized")
    orders = await get_dashboard_orders_paginated(
        page=page,
        page_size=page_size,
        search=search,
        status=status,
        client_chat_id=client_chat_id
    )
    return orders


@app.get("/api/groups")
async def api_get_groups(request: Request):
    """Returns client groups & wallet summary JSON."""
    if not is_authenticated(request):
        raise HTTPException(status_code=401, detail="Unauthorized")
    groups = await get_dashboard_groups_summary()
    return groups


@app.get("/api/loaders")
async def api_get_loaders(request: Request):
    """Returns loaders delivery summary JSON."""
    if not is_authenticated(request):
        raise HTTPException(status_code=401, detail="Unauthorized")
    loaders = await get_dashboard_loaders_summary()
    return loaders


@app.get("/api/trends")
async def api_get_trends(request: Request, days: int = Query(7, ge=1, le=30)):
    """Returns daily order trends for Chart.js."""
    if not is_authenticated(request):
        raise HTTPException(status_code=401, detail="Unauthorized")
    trends = await get_dashboard_daily_trends(days=days)
    return trends

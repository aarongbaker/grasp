#!/usr/bin/env python3
"""Basic smoke test for a deployed GRASP API.

Usage:
  GRASP_BASE_URL=https://your-api.up.railway.app \
  python scripts/smoke_test_deploy.py

Optional env vars:
  GRASP_TEST_EMAIL=smoke+123@example.com
  GRASP_TEST_PASSWORD=StrongPass123!
"""

from __future__ import annotations

import os
import secrets
import sys
from typing import Any

import requests

BASE_URL = os.environ.get("GRASP_BASE_URL", "").rstrip("/")
EMAIL = os.environ.get("GRASP_TEST_EMAIL", f"smoke+{secrets.token_hex(4)}@example.com")
PASSWORD = os.environ.get("GRASP_TEST_PASSWORD", "StrongPass123!")
TIMEOUT = 20


def fail(message: str, response: requests.Response | None = None) -> None:
    print(f"FAIL: {message}")
    if response is not None:
        print(f"Status: {response.status_code}")
        try:
            print(response.json())
        except Exception:
            print(response.text)
    sys.exit(1)


def expect(response: requests.Response, expected: int, step: str) -> dict[str, Any]:
    if response.status_code != expected:
        fail(step, response)
    try:
        return response.json()
    except Exception:
        return {}


def main() -> None:
    if not BASE_URL:
        print("Set GRASP_BASE_URL to your deployed API base URL.")
        sys.exit(1)

    print(f"Base URL: {BASE_URL}")

    health = requests.get(f"{BASE_URL}/api/v1/health", timeout=TIMEOUT)
    expect(health, 200, "health check failed")
    print("OK health")

    register_payload = {
        "name": "Smoke Test User",
        "email": EMAIL,
        "password": PASSWORD,
        "max_burners": 4,
        "max_oven_racks": 2,
        "has_second_oven": False,
        "dietary_defaults": [],
    }
    register = requests.post(f"{BASE_URL}/api/v1/users", json=register_payload, timeout=TIMEOUT)
    if register.status_code not in (201, 409):
        fail("user registration failed", register)
    print("OK register/exists")

    token = requests.post(
        f"{BASE_URL}/api/v1/auth/token",
        json={"email": EMAIL, "password": PASSWORD},
        timeout=TIMEOUT,
    )
    token_data = expect(token, 200, "token request failed")
    access_token = token_data["access_token"]
    headers = {"Authorization": f"Bearer {access_token}"}
    print("OK token")

    session = requests.post(
        f"{BASE_URL}/api/v1/sessions",
        headers=headers,
        json={
            "free_text": "A simple dinner with roast chicken, potatoes, and a green salad.",
            "guest_count": 4,
            "meal_type": "dinner",
            "occasion": "dinner_party",
            "dietary_restrictions": [],
        },
        timeout=TIMEOUT,
    )
    session_data = expect(session, 201, "session creation failed")
    session_id = session_data["session_id"]
    print(f"OK create session {session_id}")

    run = requests.post(f"{BASE_URL}/api/v1/sessions/{session_id}/run", headers=headers, timeout=TIMEOUT)
    if run.status_code not in (202, 409):
        fail("session run enqueue failed", run)
    print("OK run enqueue")

    status = requests.get(f"{BASE_URL}/api/v1/sessions/{session_id}", headers=headers, timeout=TIMEOUT)
    if status.status_code != 200:
        fail("session status check failed", status)
    print("OK status fetch")

    print("Smoke test passed.")


if __name__ == "__main__":
    main()

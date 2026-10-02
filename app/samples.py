"""The 10 sample tasks: used by "Load sample tasks" and to seed the read-only demo account."""
from datetime import date, timedelta


def sample_tasks() -> list[dict]:
    today = date.today()
    d = lambda days: (today + timedelta(days=days)).isoformat()  # noqa: E731
    return [
        {"title": "Provision the Azure VM", "note": "Ubuntu 24.04, B2pts_v2 (Arm64), SSH key only. Check the student policy for allowed regions first.", "priority": "high", "due_date": d(0)},
        {"title": "Lock down the NSG", "note": "Port 22 only from my IP /32, ports 80 and 443 open to the world.", "priority": "high", "due_date": d(1)},
        {"title": "Write the Dockerfile", "priority": "medium", "due_date": d(2)},
        {"title": "Put Caddy in front for HTTPS", "note": "Automatic Let's Encrypt certificate, HTTP redirects to HTTPS. The app stays on the internal Docker network.", "priority": "medium"},
        {"title": "Water the plants", "priority": "low", "due_date": d(-1)},
        {"title": "Read about cloud-init", "note": "First-boot scripts: install Docker before I even SSH in.", "priority": "low", "due_date": d(5)},
        {"title": "Add invite-only logins", "note": "argon2id password hashes, HttpOnly session cookies, CSRF tokens, a read-only demo account.", "priority": "medium", "due_date": d(1)},
        {"title": "Screenshot the portal for the report", "priority": "medium", "due_date": d(4)},
        {"title": "Buy oat milk", "note": "And maybe a croissant.", "priority": "low"},
        {"title": "Prepare PBL viva slides", "note": "Architecture diagram, cost model, security choices, live demo, what I learned.", "priority": "high", "due_date": d(7)},
    ]


# Titles marked done when the demo account is seeded, so the board shows the done state too.
DEMO_DONE = {"Write the Dockerfile", "Put Caddy in front for HTTPS", "Prepare PBL viva slides"}

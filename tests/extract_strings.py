"""List translatable strings (used by tests to check the Slovak dictionary is complete)."""
import ast
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent / "app"
TEMPLATE_PATTERNS = [
    re.compile(r"""(?<![\w.])_\(\s*'((?:[^'\\]|\\.)*)'"""),
    re.compile(r'''(?<![\w.])_\(\s*"((?:[^"\\]|\\.)*)"'''),
]
# Values shown through _(variable): statuses, conditions, roles, audit actions/entities, fields.
DYNAMIC = [
    "pending", "approved", "rejected", "checked_out", "returned", "cancelled",
    "good", "fair", "damaged", "broken", "missing",
    "admin", "manager", "member", "auditor",
    "create", "update", "archive", "restore", "login", "login_failed", "password_change",
    "password_reset", "calendar_link_reset", "photo_add", "photo_hide", "approve", "reject",
    "checkout", "return", "cancel", "verify", "export", "backup", "download",
    "item", "event", "reservation", "user", "event_type", "audit_log", "system",
    "name", "category", "location", "quantity", "serial_number", "purchase_date", "purchase_value",
    "condition", "description", "reservable", "archived", "title", "type_id", "event_date",
    "start_time", "end_time", "other_participants", "participants", "item_id", "user_id",
    "event_id", "start_at", "end_at", "purpose", "status", "decision_note", "return_condition",
    "return_note", "full_name", "email", "phone", "role", "active", "language", "username",
    "notify_email", "notify_whatsapp", "photo", "sha256", "name_sk", "name_en", "color",
    "yes", "NO", "Reservation approved", "Reservation rejected",
    # v0.2: rooms, maintenance, asset value
    "purchase", "donation", "other", "ok", "issues", "failed", "maintenance_task", "maintenance",
    "report", "kind", "acquisition", "donor", "unit_value", "useful_life_years", "interval_days",
    "next_due", "responsible_id", "contractor", "notes", "done_on", "performed_by", "result",
    "cost", "task_id",
    # v0.3: rosters, galleries
    "roster", "roster_role", "assign", "signup", "swap", "swap_request", "swap_cancel", "delete",
    "gallery", "role_id", "swap_requested", "swap_note", "off", "members", "public",
    "You were added to the roster", "You were taken off the roster", "Your replacement is found",
    "Roster swap", "Replacement needed",
]


def strings():
    found = set(DYNAMIC)
    for path in ROOT.rglob("*.html"):
        text = path.read_text(encoding="utf-8")
        for pat in TEMPLATE_PATTERNS:
            found.update(m.group(1) for m in pat.finditer(text))
    for path in ROOT.rglob("*.py"):
        if path.name == "translations_sk.py":
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id in ("_", "tr") and node.args
                    and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str)):
                found.add(node.args[0].value)
    return found


if __name__ == "__main__":
    for s in sorted(strings()):
        print(repr(s))

"""Security primitives: TOTP (RFC 6238), CSRF, and RBAC. Standard library only."""
import base64, hashlib, hmac, secrets, struct, time
from functools import wraps
from flask import abort, g, redirect, request, session, url_for


# ---- TOTP (BR-6 / Account Security Policy) ----
def new_totp_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode()


def _hotp(secret: str, counter: int, digits: int = 6) -> str:
    key = base64.b32decode(secret.upper())
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    o = digest[-1] & 0x0F
    code = (struct.unpack(">I", digest[o:o + 4])[0] & 0x7FFFFFFF) % (10 ** digits)
    return str(code).zfill(digits)


def totp_now(secret: str, at: float | None = None) -> str:
    return _hotp(secret, int((at or time.time()) // 30))


def verify_totp(secret: str, code: str, window: int = 1) -> bool:
    code = (code or "").strip()
    if not (code.isdigit() and len(code) == 6):
        return False
    step = int(time.time() // 30)
    return any(hmac.compare_digest(_hotp(secret, step + d), code) for d in range(-window, window + 1))


def provisioning_uri(email: str, secret: str, issuer: str = "IS ACCREDISYS") -> str:
    return f"otpauth://totp/{issuer}:{email}?secret={secret}&issuer={issuer}&digits=6&period=30"


# ---- CSRF ----
def csrf_token() -> str:
    if "_csrf" not in session:
        session["_csrf"] = secrets.token_urlsafe(32)
    return session["_csrf"]


def check_csrf():
    if request.method in ("POST", "PUT", "PATCH", "DELETE"):
        sent = request.headers.get("X-CSRF-Token") or request.form.get("_csrf", "")
        if not hmac.compare_digest(sent, session.get("_csrf", "")):
            abort(400, "Invalid CSRF token")


# ---- RBAC (FR-02, BR-1, BR-10) ----
PERMISSIONS = {
    "dept_chair": {
        "repo.view", "repo.upload", "repo.review", "repo.request_revision",
        "requirements.view", "requirements.manage", "faculty.view", "faculty.manage",
        "dashboard.view", "reports.generate", "admin.users", "admin.backup",
        "access_requests.decide",
    },
    "faculty": {
        "repo.view", "repo.upload", "requirements.view",
        "faculty.view_own", "faculty.manage_own", "dashboard.view",
    },
    "external_evaluator": {"repo.view_approved_only"},
}


def can(user, perm: str) -> bool:
    return bool(user) and perm in PERMISSIONS.get(user["role"], set())


def login_required(view):
    @wraps(view)
    def wrapped(*a, **kw):
        if g.user is None:
            abort(401)
        return view(*a, **kw)
    return wrapped


def permission_required(perm: str):
    def deco(view):
        @wraps(view)
        def wrapped(*a, **kw):
            if g.user is None:
                abort(401)
            if not can(g.user, perm):
                abort(403)
            return view(*a, **kw)
        return wrapped
    return deco

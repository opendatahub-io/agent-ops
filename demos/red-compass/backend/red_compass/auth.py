from datetime import timedelta

import jwt
from fastapi import Cookie, Header, HTTPException

from .config import settings
from .db import now


def secret(name):
    value = getattr(settings(), name)
    if not value:
        raise RuntimeError(f"{name.upper()} must be configured")
    return value


def issue(subject, purpose, minutes=60, **claims):
    return jwt.encode(
        {
            "sub": subject,
            "purpose": purpose,
            "exp": now() + timedelta(minutes=minutes),
            **claims,
        },
        secret(
            "session_secret" if purpose == "customer" else "agent_token_signing_secret"
        ),
        algorithm="HS256",
    )


def decode(token, purpose):
    try:
        result = jwt.decode(
            token,
            secret(
                "session_secret"
                if purpose == "customer"
                else "agent_token_signing_secret"
            ),
            algorithms=["HS256"],
        )
        if result["purpose"] != purpose:
            raise ValueError()
        return result
    except (jwt.PyJWTError, ValueError, KeyError):
        raise HTTPException(401, "Authentication required")


def customer(rc_session: str | None = Cookie(default=None)):
    return decode(rc_session or "", "customer")["sub"]


def agent(authorization: str = Header(default="")):
    return decode(authorization.removeprefix("Bearer "), "agent")

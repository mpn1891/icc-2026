"""JWT mint and verify.

The vendor documents one credential pair and one header:

    mutation authenticate(username, password) -> a JWT string
    Authorization: Bearer <jwt>   on everything else

Two properties of this implementation are deliberate rather than incidental.

**Tokens expire in five minutes.** The poller does not track expiry; it caches
the token and re-authenticates when it is handed a 401. A token that lived for a
day would make that branch dead code, and dead code in an auth path is code that
fails on stage a year later. Five minutes means every rehearsal exercises it.

**An expired or missing token is an HTTP 401, not a GraphQL error.** GraphQL
services conventionally answer 200 with an `errors` array, but a client cannot
cheaply tell "your token expired" from "your query had a typo" that way -- and
`system.net.httpClient` in Ignition surfaces the status code, not the body. The
401 is what makes the re-auth branch reachable at all. `server.py` enforces it in
front of the schema; this module only mints and reads.
"""

from __future__ import annotations

import time

import jwt

ALGORITHM = "HS256"


class AuthError(Exception):
    """Raised for a missing, malformed, expired or wrongly signed token."""


def mint(secret: str, username: str, role: str, ttl_s: int) -> str:
    now = int(time.time())
    claims = {
        "sub": username,
        "role": role,
        "iat": now,
        "exp": now + int(ttl_s),
    }
    token = jwt.encode(claims, secret, algorithm=ALGORITHM)
    # PyJWT 1.x returned bytes; 2.x returns str. Normalise so callers never
    # have to care which one the image resolved.
    return token.decode("utf-8") if isinstance(token, bytes) else token


def verify(secret: str, token: str) -> dict:
    try:
        return jwt.decode(token, secret, algorithms=[ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise AuthError("token expired")
    except Exception:
        raise AuthError("invalid token")


def bearer(header_value) -> str:
    """The token out of an `Authorization: Bearer <jwt>` header value."""
    if not header_value:
        raise AuthError("no Authorization header")
    text = header_value.decode("latin-1") if isinstance(header_value, bytes) \
        else str(header_value)
    parts = text.split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise AuthError("Authorization header is not a Bearer token")
    return parts[1].strip()


def check_credentials(cfg, username: str, password: str) -> bool:
    return username == cfg.username and password == cfg.password

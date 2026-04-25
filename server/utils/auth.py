"""JWT and password utilities."""

from datetime import datetime, timedelta
from typing import Any, Optional

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import JWTError, jwt
from passlib.context import CryptContext

from config import get_settings

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
_bearer_optional = HTTPBearer(auto_error=False)


def hash_password(plain: str) -> str:
    return pwd_context.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


def create_token(data: dict[str, Any], expires_minutes: int = 1440) -> str:
    s = get_settings()
    payload = {**data, "exp": datetime.utcnow() + timedelta(minutes=expires_minutes)}
    return jwt.encode(payload, s.JWT_SECRET, algorithm=s.JWT_ALGORITHM)


def decode_token(token: str) -> dict:
    s = get_settings()
    try:
        return jwt.decode(token, s.JWT_SECRET, algorithms=[s.JWT_ALGORITHM])
    except JWTError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token")


async def _get_payload(
    request: Request,
    creds: Optional[HTTPAuthorizationCredentials] = Depends(_bearer_optional),
) -> dict:
    if creds:
        return decode_token(creds.credentials)
    token = request.cookies.get("auth_token")
    if token:
        return decode_token(token)
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")


def require_admin(payload: dict = Depends(_get_payload)) -> dict:
    if payload.get("role") != "admin":
        raise HTTPException(403, "Admin access required")
    return payload


def require_supervisor(payload: dict = Depends(_get_payload)) -> dict:
    if payload.get("role") != "supervisor":
        raise HTTPException(403, "Supervisor access required")
    return payload


def require_operator(payload: dict = Depends(_get_payload)) -> dict:
    if payload.get("role") != "operator":
        raise HTTPException(403, "Operator access required")
    return payload


def require_operator_session(payload: dict = Depends(_get_payload)) -> dict:
    if payload.get("role") != "operator_session":
        raise HTTPException(403, "Operator session required")
    return payload

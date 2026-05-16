from fastapi import Request
from slowapi import Limiter
from slowapi.util import get_remote_address


def _sync_key_or_ip(request: Request) -> str:
    return request.headers.get("x-sync-key") or get_remote_address(request)


limiter = Limiter(key_func=_sync_key_or_ip)

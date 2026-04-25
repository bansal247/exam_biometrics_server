"""Row-level Fernet encryption for SQLAlchemy columns."""

from cryptography.fernet import Fernet
from sqlalchemy import TypeDecorator, String, LargeBinary
from config import get_settings

_fernet: Fernet | None = None


def _get_fernet() -> Fernet:
    global _fernet
    if _fernet is None:
        _fernet = Fernet(get_settings().DB_ENCRYPTION_KEY.encode())
    return _fernet


class EncryptedString(TypeDecorator):
    impl = String
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        return _get_fernet().encrypt(value.encode()).decode()

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        return _get_fernet().decrypt(value.encode()).decode()


class EncryptedBytes(TypeDecorator):
    impl = LargeBinary
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        return _get_fernet().encrypt(value)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        return _get_fernet().decrypt(value)

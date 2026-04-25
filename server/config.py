from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    DB_USER: str
    DB_PASSWORD: str
    DB_NAME: str
    DB_HOST: str = "db"
    DB_PORT: int = 5432
    DB_ENCRYPTION_KEY: str
    JWT_SECRET: str
    JWT_ALGORITHM: str = "HS256"
    ADMIN_ID: str
    ADMIN_PASSWORD: str
    AWS_ACCESS_KEY_ID: str = ""
    AWS_SECRET_ACCESS_KEY: str = ""
    AWS_REGION: str = "ap-south-1"
    SOURCEAFIS_URL: str = "http://sourceafis:8080"
    IRIS_ENDPOINT: str = "http://iris:8080"

    # CORS — comma-separated list of allowed browser origins for the admin panel
    # e.g. "http://192.168.1.10,https://admin.example.com"
    ALLOWED_ORIGINS: str = ""

    # Biometric file storage (Docker volume mount point)
    BIOMETRICS_DIR: str = "/biometrics"

    # Biometric matching thresholds
    FACE_MATCH_THRESHOLD: float = 80.0          # AWS Rekognition similarity %
    FINGERPRINT_MATCH_THRESHOLD: float = 40.0   # SourceAFIS score
    IRIS_MATCH_THRESHOLD: int = 500             # iris service score

    # Load testing — set to a non-empty secret to enable /load endpoints
    LOAD_TOKEN: str = ""

    @property
    def database_url(self) -> str:
        return (
            f"postgresql+asyncpg://{self.DB_USER}:{self.DB_PASSWORD}"
            f"@{self.DB_HOST}:{self.DB_PORT}/{self.DB_NAME}"
        )

    class Config:
        env_file = ".env"


@lru_cache
def get_settings() -> Settings:
    return Settings()

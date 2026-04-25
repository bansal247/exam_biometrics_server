from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from config import get_settings

settings = get_settings()
engine = create_async_engine(
    settings.database_url,
    echo=False,
    pool_size=20,
    max_overflow=10,
    pool_pre_ping=True,   # discard stale connections before use
    pool_recycle=3600,    # recycle connections after 1 h to avoid server-side idle timeouts
)
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def get_db() -> AsyncSession:
    async with async_session() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise

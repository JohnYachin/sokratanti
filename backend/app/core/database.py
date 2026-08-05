from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from supabase import create_client, Client
from app.core.config import settings

# Supabase Client
def get_supabase_client() -> Client:
    """Returns a synchronous Supabase client."""
    return create_client(settings.SUPABASE_URL, settings.SUPABASE_KEY)

# SQLAlchemy Async Engine
engine = create_async_engine(
    settings.DATABASE_URL,
    echo=settings.DEBUG,
    future=True,
    pool_pre_ping=True
)

AsyncSessionLocal = sessionmaker(
    engine, class_=AsyncSession, expire_on_commit=False
)

async def get_db():
    """FastAPI dependency for yielding DB sessions."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()

async def init_db():
    """Initialize DB connection."""
    async with engine.begin() as conn:
        pass
    print("Database connection initialized.")

import os

from sqlalchemy import create_engine

DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql+psycopg://masks:masks@localhost:5432/masks"
)
# Базовый URL Martin для шаблонов тайлов, отдаваемых фронту.
TILES_BASE_URL = os.environ.get("TILES_BASE_URL", "http://localhost:3000")

engine = create_engine(DATABASE_URL, pool_pre_ping=True)

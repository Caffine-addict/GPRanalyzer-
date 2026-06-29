from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database.schema import Base

DATABASE_URL = "sqlite:///factory.db"

engine = create_engine(
    DATABASE_URL,
    echo=False
)

SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine
)


def create_database():
    Base.metadata.create_all(bind=engine)
    print("Database created successfully.")
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from entity import Base
from config import DATABASE_URL

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(bind=engine)
Base.metadata.create_all(engine)

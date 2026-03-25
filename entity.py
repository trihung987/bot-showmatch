from sqlalchemy import Column, Integer, String, JSON, DateTime 
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import declarative_base
import uuid
from sqlalchemy.sql import func

Base = declarative_base()

class Player(Base):
    __tablename__ = "players"
    discord_id = Column(String, primary_key=True)
    in_game_name = Column(String, nullable=False)
    elo = Column(Integer, default=1000)
    wins = Column(Integer, default=0)
    losses = Column(Integer, default=0)
    streak = Column(Integer, default=0)
    phieu = Column(Integer, default=5)

class Match(Base):
    __tablename__ = "matches"
    match_id = Column(Integer, primary_key=True, autoincrement=True)
    registration_msg_id = Column(String, nullable=True)
    checkin_msg_id = Column(String, nullable=True)
    team_msg_id = Column(String, nullable=True)
    team_size = Column(Integer)
    match_time = Column(DateTime)
    prize = Column(Integer)
    elo_requirement = Column(String) 
    
    participants = Column(JSON, default=list) 
    checked_in = Column(JSON, default=list)  
    team1 = Column(JSON, default=list) # Lưu list discord_id
    team2 = Column(JSON, default=list) # Lưu list discord_id
    status = Column(String, default="waiting") 
    created_by = Column(String)
    created_date = Column(DateTime(timezone=True), server_default=func.now())
    result = Column(String, nullable=True)     # Ví dụ: "Team 1 thắng", "Hủy"
    elo_bonus = Column(Integer, default=0)


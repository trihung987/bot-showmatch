from sqlalchemy import Column, Integer, String, JSON, DateTime, Boolean
from sqlalchemy.orm import declarative_base
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
    start_match_message_id = Column(String, nullable=True)  # Message ID in START_SHOWMATCH channel
    team_size = Column(Integer)
    match_time = Column(DateTime)
    prize = Column(Integer)
    elo_requirement = Column(String)
    bo = Column(Integer, nullable=True)  # Best Of (e.g. 1, 3, 5)

    participants = Column(JSON, default=list) 
    checked_in = Column(JSON, default=list)  
    team1 = Column(JSON, default=list) # Lưu list discord_id
    team2 = Column(JSON, default=list) # Lưu list discord_id
    status = Column(String, default="waiting") 
    created_by = Column(String)
    created_date = Column(DateTime(timezone=True), server_default=func.now())
    result = Column(String, nullable=True)     # Ví dụ: "Team 1 thắng", "Hủy"
    elo_bonus = Column(Integer, default=0)
    messages_deleted = Column(Boolean, default=False, nullable=True)  # True after cleanup task removes all Discord messages


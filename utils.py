import itertools
import discord
from entity import Player, Match

def balance_teams(players_list):
    n = len(players_list)
    team_size = n // 2
    best_diff = float('inf')
    best_teams = ([], [])

    for team1_indices in itertools.combinations(range(n), team_size):
        team1 = [players_list[i] for i in team1_indices]
        team2 = [players_list[i] for i in range(n) if i not in team1_indices]
        
        sum1 = sum(p[2] for p in team1)
        sum2 = sum(p[2] for p in team2)
        diff = abs(sum1 - sum2)
        
        if diff < best_diff:
            best_diff = diff
            best_teams = (team1, team2)
            if diff == 0: break 
            
    return best_teams[0], best_teams[1], best_diff

async def auto_split_teams(match_id, session):
    match = session.query(Match).filter_by(match_id=match_id).first()
    if not match: return None

    players = session.query(Player).filter(Player.discord_id.in_(match.checked_in)).all()
    player_data = [(p.discord_id, p.in_game_name, p.elo) for p in players]
    
    if len(player_data) < match.team_size * 2:
        return None

    team1, team2, diff = balance_teams(player_data)
    
    # Lưu vào Database
    match.team1 = [p[0] for p in team1]
    match.team2 = [p[0] for p in team2]
    
    embed = discord.Embed(title=f"🎮 CHIA TEAM TRẬN #{str(match_id)[:8]}", color=discord.Color.purple())
    
    t1_str = "\n".join([f"• `{p[2]}` - {p[1]} (<@{p[0]}>)" for p in team1])
    t2_str = "\n".join([f"• `{p[2]}` - {p[1]} (<@{p[0]}>)" for p in team2])
    
    sum1 = sum(p[2] for p in team1)
    sum2 = sum(p[2] for p in team2)

    embed.add_field(name=f"🔵 Team 1 (Tổng Elo: {sum1})", value=t1_str, inline=False)
    embed.add_field(name=f"🔴 Team 2 (Tổng Elo: {sum2})", value=t2_str, inline=False)
    embed.set_footer(text=f"Độ lệch Elo giữa 2 đội: {diff}")
    
    return embed
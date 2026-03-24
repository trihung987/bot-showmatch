import itertools
import discord
from entity import Player, Match
import random
import discord

def balance_teams_heuristic(players_list, team_size, max_iter=5000):
    """
    Chia players_list thành 2 đội mỗi đội team_size, tối thiểu chênh lệch Elo.
    Dùng heuristic, nhanh cho n lớn.
    """
    n = len(players_list)
    if n < team_size * 2:
        return None, None, None

    # Sắp xếp Elo giảm dần
    sorted_players = sorted(players_list, key=lambda x: x[2], reverse=True)

    # Gán đội xen kẽ: mạnh nhất -> team1, thứ 2 -> team2, ...
    team1 = []
    team2 = []
    for p in sorted_players[:team_size*2]:
        if len(team1) < team_size:
            team1.append(p)
        else:
            team2.append(p)

    sum1 = sum(p[2] for p in team1)
    sum2 = sum(p[2] for p in team2)
    best_diff = abs(sum1 - sum2)
    best_team1, best_team2 = team1[:], team2[:]

    # Local search: thử hoán đổi 1 người từ mỗi đội để giảm diff
    stale = 0
    for _ in range(max_iter):
        i = random.randint(0, team_size-1)
        j = random.randint(0, team_size-1)
        # hoán đổi
        team1[i], team2[j] = team2[j], team1[i]

        sum1 = sum(p[2] for p in team1)
        sum2 = sum(p[2] for p in team2)
        diff = abs(sum1 - sum2)

        if diff < best_diff:
            best_diff = diff
            best_team1 = team1[:]
            best_team2 = team2[:]
            stale = 0
            if diff == 0:
                break
        else:
            # hoàn tác hoán đổi nếu không cải thiện
            team1[i], team2[j] = team2[j], team1[i]
            stale += 1
            if stale >= 300:
                break

    return best_team1, best_team2, best_diff


async def auto_split_teams(match_id, session):
    match = session.query(Match).filter_by(match_id=match_id).first()
    if not match: return None

    players = session.query(Player).filter(Player.discord_id.in_(match.checked_in)).all()
    player_data = [(p.discord_id, p.in_game_name, p.elo) for p in players]

    team1, team2, diff = balance_teams_heuristic(player_data, match.team_size)
    if not team1:
        return None

    # Lưu vào DB
    match.team1 = [p[0] for p in team1]
    match.team2 = [p[0] for p in team2]

    # Tạo Embed
    embed = discord.Embed(title=f"🎮 CHIA TEAM TRẬN #{str(match_id)[:8]}", color=discord.Color.purple())
    t1_str = "\n".join([f"• `{p[2]}` - {p[1]} (<@{p[0]}>)" for p in team1])
    t2_str = "\n".join([f"• `{p[2]}` - {p[1]} (<@{p[0]}>)" for p in team2])
    sum1 = sum(p[2] for p in team1)
    sum2 = sum(p[2] for p in team2)
    embed.add_field(name=f"🔵 Team 1 (Tổng Elo: {sum1})", value=t1_str, inline=False)
    embed.add_field(name=f"🔴 Team 2 (Tổng Elo: {sum2})", value=t2_str, inline=False)
    embed.set_footer(text=f"Độ lệch Elo giữa 2 đội: {diff}")

    return embed

def calculate_elo_fixed_gap(team_a, team_b, winner='a'):
    sum_a = sum(team_a)
    sum_b = sum(team_b)
    gap = abs(sum_a - sum_b)
    
    # Giới hạn gap tối đa là 150 để tính toán
    clamped_gap = min(gap, 150)
    
    # Mốc điểm
    base_points = 18.5  
    max_bonus = 17.5    
    
    # Tính toán biến thiên dựa trên độ lệch (0 đến 17.5)
    bonus = (clamped_gap / 150) * max_bonus
    
    if winner == 'a':
        if sum_a <= sum_b:
            # Đội yếu thắng: Cộng nhiều hơn
            final_points = base_points + bonus
        else:
            # Đội mạnh thắng: Cộng ít hơn
            final_points = base_points - bonus
    else: # winner == 'b'
        if sum_b <= sum_a:
            # Đội yếu thắng
            final_points = base_points + bonus
        else:
            # Đội mạnh thắng
            final_points = base_points - bonus

    # Làm tròn để điểm đẹp
    final_points = round(final_points)
    
    return {
        "win_team_points": f"+{final_points}",
        "lose_team_points": f"-{final_points}",
        "gap": gap,
        "team_a_new": [r + (final_points if winner == 'a' else -final_points) for r in team_a],
        "team_b_new": [r + (final_points if winner == 'b' else -final_points) for r in team_b]
    }

# players_list = [
#     (1, "Alice", 1500),
#     (2, "Bob", 1450),
#     (3, "Charlie", 1600),
#     (4, "David", 1550),
#     (5, "Eve", 1400),
#     (6, "Frank", 1500),
#     (7, "Grace", 1350),
#     (8, "Heidi", 1550),
#     (9, "Ivan", 1450),
#     (10, "Judy", 1500),
#     (11, "Daten", 1200)
# ]

# team_size = 3

# team1, team2, diff = balance_teams_heuristic(players_list, team_size)
# print("=== Team 1 ===")
# for p in team1:
#     print(f"{p[1]} (Elo: {p[2]})")
# print("Tổng Elo:", sum(p[2] for p in team1))

# print("\n=== Team 2 ===")
# for p in team2:
#     print(f"{p[1]} (Elo: {p[2]})")
# print("Tổng Elo:", sum(p[2] for p in team2))

# print("\nChênh lệch Elo:", diff)

import itertools
import discord
from entity import Player, Match
import random
import discord
from helpers import format_vn_time

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


def generate_team_combinations(players_list, team_size, max_options=10):
    """
    Generate up to *max_options* distinct balanced team splits for *players_list*.

    For team_size <= 7 (≤14 total players) every possible split is enumerated
    exactly and the best ones by Elo difference are returned.  For larger
    rosters the existing heuristic is run repeatedly to gather diverse options.

    Each entry in the returned list is (team1, team2, diff) where every player
    element is a (discord_id, in_game_name, elo) tuple.
    """
    total = team_size * 2
    if len(players_list) < total:
        return []

    pool = players_list[:total]
    seen: set = set()
    results = []

    if team_size <= 7:
        # Exhaustive: Combinations(total, team_size) / 2 unique splits
        for combo in itertools.combinations(range(total), team_size):
            combo_set = frozenset(combo)
            rest_set  = frozenset(i for i in range(total) if i not in combo_set)
            key = frozenset([combo_set, rest_set])
            if key in seen:
                continue
            seen.add(key)
            t1   = [pool[i] for i in sorted(combo_set)]
            t2   = [pool[i] for i in sorted(rest_set)]
            diff = abs(sum(p[2] for p in t1) - sum(p[2] for p in t2))
            results.append((t1, t2, diff))
        results.sort(key=lambda x: x[2])
        return results[:max_options]
    else:
        # Heuristic: run many times with different random seeds
        for _ in range(max_options * 20):
            t1, t2, d = balance_teams_heuristic(pool, team_size)
            if not t1:
                break
            key = frozenset([frozenset(p[0] for p in t1), frozenset(p[0] for p in t2)])
            if key not in seen:
                seen.add(key)
                results.append((t1, t2, d))
            if len(results) >= max_options:
                break
        results.sort(key=lambda x: x[2])
        return results


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
    embed = discord.Embed(title=f"🎮 CHIA TEAM TRẬN `#{match_id}`", color=discord.Color.purple())
    t1_str = "\n".join([f"• `{p[2]}` - {p[1]} (<@{p[0]}>)" for p in team1])
    t2_str = "\n".join([f"• `{p[2]}` - {p[1]} (<@{p[0]}>)" for p in team2])
    sum1 = sum(p[2] for p in team1)
    sum2 = sum(p[2] for p in team2)

    embed.add_field(name=f"**Giờ thi đấu:** {format_vn_time(match.match_time)}\n", value="")
    embed.add_field(name=f"🔵 Team 1 (Tổng Elo: {sum1})", value=t1_str, inline=False)
    embed.add_field(name=f"🔴 Team 2 (Tổng Elo: {sum2})", value=t2_str, inline=False)
    embed.set_footer(text=f"Độ lệch Elo ít nhất có thể giữa 2 đội: {diff}")

    return embed

def calculate_elo_fixed_gap(team_a, team_b, winner='a', wins_a=0, wins_b=0):
    sum_a = sum(team_a)
    sum_b = sum(team_b)
    gap = abs(sum_a - sum_b)

    clamped_gap = min(gap, 150)

    base_points = 24
    max_bonus = 18.5

    if winner == 'a':
        dominant = wins_b == 0
        stronger = sum_a > sum_b
    else:
        dominant = wins_a == 0
        stronger = sum_b > sum_a

    # clamp bonus không vượt base_points (vì sau khi test xảy ra tình trạng bị âm bonus với đội thắng do gap quá nhìu)
    raw_bonus = (clamped_gap / 100) * max_bonus
    bonus = min(raw_bonus, base_points)

    if stronger:
        final_points = base_points - bonus
    else:
        final_points = base_points + bonus

    if dominant:
        final_points *= 0.85  

    final_points = max(1, round(final_points))  # 👈 đảm bảo luôn ≥ 1

    return {
        "win_team_points": f"+{final_points}",
        "lose_team_points": f"-{final_points}",
        "gap": gap,
        "score": f"{wins_a}-{wins_b}",
        "dominant": dominant,
        "team_a_new": [r + (final_points if winner == 'a' else -final_points) for r in team_a],
        "team_b_new": [r + (final_points if winner == 'b' else -final_points) for r in team_b]
    }

# players_list = [
#     (1, "Cơn mê", 1400),
#     (2, "Đức Tiến", 1000),
#     (3, "Trí Đức", 1000),
#     (4, "Linh Phan", 1000),
#     # (3, "Charlie", 1100),
#     # (4, "David", 1550),
#     # (5, "Eve", 1400),
#     # (6, "Frank", 1500),
#     # (7, "Grace", 1350),
#     # (8, "Heidi", 1550),
#     # (9, "Ivan", 1450),
#     # (10, "Judy", 1500),
#     # (11, "Daten", 1200)
# ]

# team_size = 2

# team1, team2, diff = balance_teams_heuristic(players_list, team_size)

# team1elo = [p[2] for p in team1]
# team2elo = [p[2] for p in team2]
# print("=== Team 1 ===")
# for p in team1:
#     print(f"{p[1]} (Elo: {p[2]})")
# print("Tổng Elo:", sum(p[2] for p in team1))

# print("\n=== Team 2 ===")
# for p in team2:
#     print(f"{p[1]} (Elo: {p[2]})")
# print("Tổng Elo:", sum(p[2] for p in team2))

# print("\nChênh lệch Elo:", diff)
# gap = calculate_elo_fixed_gap(team1elo, team2elo, "b", 0, 2)
# print("Bonus elo", gap)
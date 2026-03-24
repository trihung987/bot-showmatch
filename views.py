import discord
from discord.ui import View
from entity import Player, Match
from helpers import format_vnd, format_vn_time
from utils import calculate_elo_fixed_gap, auto_split_teams


# ──────────────────────────────────────────────
#  MatchView – Registration embed buttons
# ──────────────────────────────────────────────

class MatchView(discord.ui.View):
    def __init__(self, match_id, session_factory):
        super().__init__(timeout=None)
        self.match_id = match_id
        self.Session = session_factory

    def disable_all(self):
        for item in self.children:
            item.disabled = True

    @discord.ui.button(label="Tham gia ⚔️", style=discord.ButtonStyle.success)
    async def join(self, interaction: discord.Interaction, button: discord.ui.Button):
        session = self.Session()
        try:
            match = session.query(Match).filter_by(match_id=self.match_id).first()
            if not match or match.status not in ["waiting", "checkin", "notified_low"]:
                return await interaction.response.send_message("Trận đấu đã khóa!", ephemeral=True)

            player = session.query(Player).filter_by(discord_id=str(interaction.user.id)).first()
            if not player:
                return await interaction.response.send_message(
                    "Vui lòng được xét elo trước, hỏi admin nếu chưa có!", ephemeral=True
                )

            # Elo gate-check
            req = match.elo_requirement.split(":")
            e_type, e_min, e_max = req[0], int(req[1]), int(req[2])
            if e_type == "range" and not (e_min <= player.elo <= e_max):
                return await interaction.response.send_message(
                    f"Elo {player.elo} không hợp lệ ({e_min}-{e_max})", ephemeral=True
                )
            elif e_type == "under" and player.elo > e_min:
                return await interaction.response.send_message(
                    f"Elo {player.elo} vượt mức tối đa {e_min}", ephemeral=True
                )
            elif e_type == "above" and player.elo < e_min:
                return await interaction.response.send_message(
                    f"Elo {player.elo} không đạt tối thiểu {e_min}", ephemeral=True
                )

            parts = list(match.participants)
            if str(interaction.user.id) in parts:
                return await interaction.response.send_message("Bạn đã đăng ký rồi!", ephemeral=True)

            parts.append(str(interaction.user.id))
            match.participants = parts
            session.commit()

            embed = interaction.message.embeds[0]
            mentions = "\n".join([f"<@{u}>" for u in parts]) if parts else "Chưa có ai"
            embed.set_field_at(0, name=f"Người tham gia ({len(parts)})", value=mentions, inline=False)
            await interaction.response.edit_message(embed=embed)
        finally:
            session.close()

    @discord.ui.button(label="Hủy đăng ký ❌", style=discord.ButtonStyle.danger)
    async def leave(self, interaction: discord.Interaction, button: discord.ui.Button):
        session = self.Session()
        try:
            match = session.query(Match).filter_by(match_id=self.match_id).first()
            if not match or match.status != "waiting":
                return await interaction.response.send_message(
                    "Không thể hủy khi đã đến giờ check-in!", ephemeral=True
                )

            parts = list(match.participants)
            uid = str(interaction.user.id)
            if uid not in parts:
                return await interaction.response.send_message("Bạn chưa đăng ký!", ephemeral=True)

            parts.remove(uid)
            match.participants = parts
            session.commit()

            embed = interaction.message.embeds[0]
            mentions = "\n".join([f"<@{u}>" for u in parts]) if parts else "Chưa có ai"
            embed.set_field_at(
                0,
                name=f"Người tham gia ({len(parts)}/{match.team_size * 2})",
                value=mentions,
                inline=False,
            )
            await interaction.response.edit_message(embed=embed)
        finally:
            session.close()


# ──────────────────────────────────────────────
#  CheckInView – Check-in embed button
# ──────────────────────────────────────────────

class CheckInView(discord.ui.View):
    def __init__(self, match_id, session_factory):
        super().__init__(timeout=None)
        self.match_id = match_id
        self.Session = session_factory

    @discord.ui.button(label="Sẵn sàng ✅", style=discord.ButtonStyle.primary)
    async def checkin(self, interaction: discord.Interaction, button: discord.ui.Button):
        session = self.Session()
        try:
            match = session.query(Match).filter_by(match_id=self.match_id).first()
            if not match or match.status != "checkin":
                return await interaction.response.send_message("Cổng check-in đã đóng!", ephemeral=True)

            uid = str(interaction.user.id)
            if uid not in match.participants:
                return await interaction.response.send_message(
                    "Bạn chưa đăng ký tham gia trận này!", ephemeral=True
                )

            checked = list(match.checked_in)
            if uid in checked:
                return await interaction.response.send_message("Bạn đã check-in rồi!", ephemeral=True)

            checked.append(uid)
            match.checked_in = checked
            session.commit()

            total_slots = match.team_size * 2
            players = session.query(Player).filter(Player.discord_id.in_(checked)).all()
            p_map = {p.discord_id: p.in_game_name for p in players}
            checkin_list_str = "\n".join([f"- {p_map.get(u, 'Unknown')} ✅" for u in checked])

            embed = interaction.message.embeds[0]
            embed.set_field_at(
                0,
                name=f"Danh sách đã check-in ({len(checked)}/{total_slots})",
                value=checkin_list_str,
                inline=False,
            )
            await interaction.response.edit_message(embed=embed)
        finally:
            session.close()


# ──────────────────────────────────────────────
#  AdminControlView – Match result buttons
# ──────────────────────────────────────────────

class AdminControlView(discord.ui.View):
    def __init__(self, match_id, session_factory):
        super().__init__(timeout=None)
        self.match_id = match_id
        self.Session = session_factory

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("Chỉ Admin mới có quyền!", ephemeral=True)
            return False
        return True

    async def process_winner(self, interaction: discord.Interaction, winner_side: str):
        session = self.Session()
        try:
            match = session.query(Match).filter_by(match_id=self.match_id).first()
            if not match or match.status != "playing":
                return await interaction.response.send_message(
                    "Trận đấu không khả dụng hoặc đã kết thúc!", ephemeral=True
                )

            t1_players = session.query(Player).filter(Player.discord_id.in_(match.team1)).all()
            t2_players = session.query(Player).filter(Player.discord_id.in_(match.team2)).all()

            calc_res = calculate_elo_fixed_gap(
                [p.elo for p in t1_players],
                [p.elo for p in t2_players],
                winner="a" if winner_side == "Team 1" else "b",
            )

            win_points = int(calc_res["win_team_points"].replace("+", ""))
            lose_points = abs(int(calc_res["lose_team_points"]))

            for p in t1_players:
                if winner_side == "Team 1":
                    p.elo += win_points
                    p.wins += 1
                    p.streak = p.streak + 1 if p.streak >= 0 else 1
                else:
                    p.elo -= lose_points
                    p.losses += 1
                    p.streak = p.streak - 1 if p.streak < 0 else -1

            for p in t2_players:
                if winner_side == "Team 2":
                    p.elo += win_points
                    p.wins += 1
                    p.streak = p.streak + 1 if p.streak >= 0 else 1
                else:
                    p.elo -= lose_points
                    p.losses += 1
                    p.streak = p.streak - 1 if p.streak < 0 else -1

            match.status = "finished"
            match.result = f"{winner_side} thắng"
            match.elo_bonus = str(win_points)
            session.commit()

            win_label = "🔵 Team 1" if winner_side == "Team 1" else "🔴 Team 2"
            lose_label = "🔴 Team 2" if winner_side == "Team 1" else "🔵 Team 1"

            embed = discord.Embed(title="🏆 KẾT QUẢ SHOWMATCH", color=discord.Color.gold())
            embed.description = (
                f"## Trận đấu `#{str(match.match_id)[:8]}` kết thúc!\n\n"
                f"🏆 **Đội thắng:** {win_label}\n"
                f"📈 **Biến thiên Elo:**\n"
                f"- Đội thắng: `{calc_res['win_team_points']}` Elo\n"
                f"- Đội thua: `{calc_res['lose_team_points']}` Elo"
            )

            await interaction.response.send_message(embed=embed)
            self.stop()
            await interaction.edit_original_response(view=None)

        except Exception as e:
            session.rollback()
            await interaction.response.send_message(f"Lỗi hệ thống: {e}", ephemeral=True)
        finally:
            session.close()

    @discord.ui.button(label="Team 1 Win 🏆", style=discord.ButtonStyle.success)
    async def team1_win(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.process_winner(interaction, "Team 1")

    @discord.ui.button(label="Team 2 Win 🏆", style=discord.ButtonStyle.success)
    async def team2_win(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.process_winner(interaction, "Team 2")

    @discord.ui.button(label="Hủy Trận ❌", style=discord.ButtonStyle.danger)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        from match_lifecycle import cancel_match_logic  # late import to avoid circular

        session = self.Session()
        try:
            match = session.query(Match).filter_by(match_id=self.match_id).first()
            if match:
                match.status = "cancelled"
                match.result = "Hủy"
                match.elo_bonus = "0"
                session.commit()

                await cancel_match_logic(match, interaction.channel, "Admin chủ động hủy trận.")

                responder = (
                    interaction.response.send_message
                    if not interaction.response.is_done()
                    else interaction.followup.send
                )
                await responder("✅ Trận đấu đã được hủy và lưu trạng thái vào hệ thống.", ephemeral=True)
                self.stop()
        except Exception as e:
            session.rollback()
            responder = (
                interaction.response.send_message
                if not interaction.response.is_done()
                else interaction.followup.send
            )
            await responder(f"Lỗi hệ thống khi hủy trận: {e}", ephemeral=True)
        finally:
            session.close()

import discord
from discord.ui import View
from entity import Player, Match
from helpers import format_vnd, format_vn_time
from utils import calculate_elo_fixed_gap, auto_split_teams
from match_lifecycle import cancel_match_logic
from config import REGISTER_CHANNEL_ID, NOTIFY_CHANNEL_ID


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
                return await interaction.response.send_message(f"Trận đấu đã khóa! {match.status}", ephemeral=True)

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
            elif e_type == "under" and player.elo > e_max:
                return await interaction.response.send_message(
                    f"Elo {player.elo} vượt mức tối đa {e_max}", ephemeral=True
                )
            elif e_type == "above" and player.elo < e_min:
                return await interaction.response.send_message(
                    f"Elo {player.elo} không đạt tối thiểu {e_min}", ephemeral=True
                )

            parts = list(match.participants)
            if str(interaction.user.id) in parts:
                return await interaction.response.send_message("Bạn đã đăng ký rồi!", ephemeral=True)

            if player.phieu < 1:
                return await interaction.response.send_message(
                    "Bạn không đủ phiếu để đăng ký! (Cần ít nhất 1 phiếu)", ephemeral=True
                )

            parts.append(str(interaction.user.id))
            match.participants = parts
            player.phieu -= 1
            session.commit()

            players = session.query(Player).filter(Player.discord_id.in_(match.participants)).all()
            player_map = {p.discord_id: p for p in players}
            embed = interaction.message.embeds[0]
            mentions = "\n".join([
                f"<@{u}> - {p.in_game_name if (p := player_map.get(u)) else 'Unknown'}"
                for u in parts
            ]) if parts else "Chưa có ai"
            embed.set_field_at(0, name=f"Người tham gia ({len(parts)})", value=mentions, inline=False)
            await interaction.response.edit_message(embed=embed)
        finally:
            session.close()

    @discord.ui.button(label="Hủy đăng ký ❌", style=discord.ButtonStyle.danger)
    async def leave(self, interaction: discord.Interaction, button: discord.ui.Button):
        session = self.Session()
        try:
            match = session.query(Match).filter_by(match_id=self.match_id).first()
            if not match or match.status != "waiting" and match.status != "notified_low":
                return await interaction.response.send_message(
                    "Không thể hủy khi đã đến giờ check-in!", ephemeral=True
                )

            uid = str(interaction.user.id)
            parts = list(match.participants)
            if uid not in parts:
                return await interaction.response.send_message("Bạn chưa đăng ký!", ephemeral=True)

            parts.remove(uid)
            match.participants = parts
            player = session.query(Player).filter_by(discord_id=uid).first()
            if player:
                player.phieu += 1
            session.commit()

            players = session.query(Player).filter(Player.discord_id.in_(match.participants)).all()
            player_map = {p.discord_id: p for p in players}
            embed = interaction.message.embeds[0]
            mentions = "\n".join([
                f"<@{u}> - {p.in_game_name if (p := player_map.get(u)) else 'Unknown'}"
                for u in parts
            ]) if parts else "Chưa có ai"
            embed.set_field_at(
                0,
                name=f"Người tham gia ({len(parts)})",
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

            # if len(checked) >= match.team_size * 2:
            #     return await interaction.response.send_message(
            #         "Đã đủ người check-in cho trận này!", ephemeral=True
            #     )

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
                name=f"Danh sách đã check-in ({len(checked)}/{len(match.participants)})",
                value=checkin_list_str,
                inline=False,
            )
            await interaction.response.edit_message(embed=embed)
        finally:
            session.close()



# ──────────────────────────────────────────────
#  MatchResultModal – Nhập số trận thắng
# ──────────────────────────────────────────────

class MatchResultModal(discord.ui.Modal, title="Nhập kết quả trận đấu"):
    team1_wins = discord.ui.TextInput(
        label="Số trận thắng của Team 1 🔵",
        placeholder="Nhập số nguyên, ví dụ: 2",
        min_length=1,
        max_length=2,
        required=True,
    )
    team2_wins = discord.ui.TextInput(
        label="Số trận thắng của Team 2 🔴",
        placeholder="Nhập số nguyên, ví dụ: 1",
        min_length=1,
        max_length=2,
        required=True,
    )

    def __init__(self, match_id: str, session_factory, winner_side: str, admin_view: "AdminControlView"):
        super().__init__()
        self.match_id = match_id
        self.Session = session_factory
        self.winner_side = winner_side  # "Team 1" hoặc "Team 2"
        self.admin_view = admin_view

    async def on_submit(self, interaction: discord.Interaction):
        # Validate input là số nguyên không âm
        try:
            t1 = int(self.team1_wins.value.strip())
            t2 = int(self.team2_wins.value.strip())
            if t1 < 0 or t2 < 0:
                raise ValueError
        except ValueError:
            return await interaction.response.send_message(
                "❌ Số trận thắng phải là số nguyên không âm!", ephemeral=True
            )

        # Kiểm tra winner_side khớp với số trận thắng
        if self.winner_side == "Team 1" and t1 <= t2:
            return await interaction.response.send_message(
                f"❌ Team 1 được chọn thắng nhưng số trận ({t1}) không lớn hơn Team 2 ({t2})!",
                ephemeral=True,
            )
        if self.winner_side == "Team 2" and t2 <= t1:
            return await interaction.response.send_message(
                f"❌ Team 2 được chọn thắng nhưng số trận ({t2}) không lớn hơn Team 1 ({t1})!",
                ephemeral=True,
            )

        # Delegate sang AdminControlView để xử lý elo + kết thúc trận
        await self.admin_view.process_winner(interaction, self.winner_side, t1, t2)

    async def on_error(self, interaction: discord.Interaction, error: Exception):
        await interaction.response.send_message(f"Lỗi hệ thống: {error}", ephemeral=True)
# Modal

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

    async def process_winner(self, interaction: discord.Interaction, winner_side: str, team1: int, team2: int):
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
                wins_a=team1,
                wins_b=team2
            )

            win_points = int(calc_res["win_team_points"].replace("+", ""))
            lose_points = abs(int(calc_res["lose_team_points"]))

            for p in t1_players:
                if winner_side == "Team 1":
                    p.elo += win_points
                    p.wins += 1
                    p.streak = p.streak + 1 if p.streak >= 0 else 1
                else:
                    p.elo = max(0, p.elo - lose_points)
                    p.losses += 1
                    p.streak = p.streak - 1 if p.streak < 0 else -1

            for p in t2_players:
                if winner_side == "Team 2":
                    p.elo += win_points
                    p.wins += 1
                    p.streak = p.streak + 1 if p.streak >= 0 else 1
                else:
                    p.elo = max(0, p.elo - lose_points)
                    p.losses += 1
                    p.streak = p.streak - 1 if p.streak < 0 else -1

            match.status = "finished"
            match.result = f"{winner_side} thắng"
            match.elo_bonus = win_points
            session.commit()

            win_label = "🔵 Team 1" if winner_side == "Team 1" else "🔴 Team 2"
            lose_label = "🔴 Team 2" if winner_side == "Team 1" else "🔵 Team 1"
            ti_so = f"{team1} - {team2}" if winner_side == "Team 1" else f"{team2} - {team1}"

            embed = discord.Embed(title="🏆 KẾT QUẢ SHOWMATCH", color=discord.Color.gold())
            embed.description = (
                f"## Trận đấu `#{match.match_id}` kết thúc!\n\n"
                f"🏆 **Đội thắng:** {win_label}\n"
                f"🏁 **Tỉ số:** {ti_so}\n"
                f"📈 **Biến thiên Elo:**\n"
                f"- Đội thắng: `{calc_res['win_team_points']}` Elo\n"
                f"- Đội thua: `{calc_res['lose_team_points']}` Elo"
            )

            # await interaction.response.send_message(embed=embed)
            await interaction.response.edit_message(embed=embed, view=None)
            for item in self.children:
                item.disabled = True
            self.stop()

        except Exception as e:
            session.rollback()
            await interaction.response.send_message(f"Lỗi hệ thống: {e}", ephemeral=True)
        finally:
            session.close()

    @discord.ui.button(label="Team 1 Win 🏆", style=discord.ButtonStyle.success)
    async def team1_win(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = MatchResultModal(self.match_id, self.Session, "Team 1", self)
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="Team 2 Win 🏆", style=discord.ButtonStyle.success)
    async def team2_win(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = MatchResultModal(self.match_id, self.Session, "Team 2", self)
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="Hủy Trận ❌", style=discord.ButtonStyle.danger)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        from match_lifecycle import cancel_match_logic
        from config import REGISTER_CHANNEL_ID
        button.disabled = True
        button.label = "Đang hủy..."
        await interaction.response.edit_message(view=self)
        session = self.Session()
        try:
            match = session.query(Match).filter_by(match_id=self.match_id).first()
            if not match:
                button.disabled = False
                button.label = "Hủy Trận ❌"
                await interaction.edit_original_response(view=self)
                return
            match.result = "Hủy"
            match.elo_bonus = 0
            channel_register = interaction.guild.get_channel(REGISTER_CHANNEL_ID)
            await cancel_match_logic(
                match, channel_register,
                "Admin chủ động hủy trận.",
                interaction.client, self.Session,
                refund_scope="teams"
            )
            session.commit()
            embed = discord.Embed(title="❌ SHOWMATCH ĐÃ BỊ HỦY", color=discord.Color.red())
            embed.description = f"Trận `#{match.match_id}` đã bị hủy bởi admin."
            await interaction.edit_original_response(embed=embed, view=None)  # ✅ xóa toàn bộ button
            self.stop()
        except Exception as e:
            session.rollback()
            button.disabled = False
            button.label = "Hủy Trận ❌"
            await interaction.edit_original_response(view=self)
            await interaction.followup.send(f"Lỗi khi hủy: {e}", ephemeral=True)
        finally:
            session.close()


# ──────────────────────────────────────────────
#  TeamChoiceSelect / TeamChoiceView
#  Used by the /more_choice command
# ──────────────────────────────────────────────

def _trunc(text: str, limit: int) -> str:
    """Truncate *text* to *limit* characters, breaking at the last word boundary."""
    if len(text) <= limit:
        return text
    truncated  = text[: limit - 1]
    last_space = truncated.rfind(" ")
    return (truncated[:last_space] if last_space > 0 else truncated) + "…"

class TeamChoiceSelect(discord.ui.Select):
    """
    A dropdown that lists multiple balanced team-split options.
    Selecting one option updates team1/team2 in the DB and edits the
    existing team message in the notify channel.
    """

    def __init__(self, match_id: int, combinations: list, session_factory):
        self.match_id     = match_id
        self.combinations = combinations   # [(team1, team2, diff), ...]
        self.Session      = session_factory

        options = []
        for i, (team1, team2, diff) in enumerate(combinations):
            sum1       = sum(p[2] for p in team1)
            sum2       = sum(p[2] for p in team2)
            t1_names   = ", ".join(p[1] for p in team1)
            t2_names   = ", ".join(p[1] for p in team2)
            label       = _trunc(f"Phương án {i + 1} | Lệch Elo: {diff}", 100)
            description = _trunc(f"🔵 {t1_names} | 🔴 {t2_names}", 100)
            options.append(discord.SelectOption(label=label, value=str(i), description=description))

        super().__init__(
            placeholder="Chọn phương án chia team...",
            options=options,
            custom_id=f"more_choice_{match_id}",
        )

    async def callback(self, interaction: discord.Interaction):
        if not interaction.user.guild_permissions.administrator:
            return await interaction.response.send_message("Chỉ Admin mới có quyền!", ephemeral=True)

        idx             = int(self.values[0])
        team1, team2, diff = self.combinations[idx]

        session = self.Session()
        try:
            match = session.query(Match).filter_by(match_id=self.match_id).first()
            if not match:
                return await interaction.response.send_message(
                    "❌ Không tìm thấy trận đấu!", ephemeral=True
                )

            # Persist the chosen team assignment
            match.team1 = [p[0] for p in team1]
            match.team2 = [p[0] for p in team2]
            session.commit()

            # Build a new team embed (mirrors the format used by auto_split_teams)
            sum1  = sum(p[2] for p in team1)
            sum2  = sum(p[2] for p in team2)
            t1_str = "\n".join([f"• `{p[2]}` - {p[1]} (<@{p[0]}>)" for p in team1])
            t2_str = "\n".join([f"• `{p[2]}` - {p[1]} (<@{p[0]}>)" for p in team2])

            embed = discord.Embed(
                title=f"🎮 CHIA TEAM TRẬN `#{self.match_id}`",
                color=discord.Color.purple(),
            )
            embed.add_field(name=f"**Giờ thi đấu:** {format_vn_time(match.match_time)}\n", value="")
            embed.add_field(name=f"🔵 Team 1 (Tổng Elo: {sum1})", value=t1_str, inline=False)
            embed.add_field(name=f"🔴 Team 2 (Tổng Elo: {sum2})", value=t2_str, inline=False)
            embed.set_footer(text=f"Chênh lệch Elo: {diff}")

            # Edit the existing team message in the notify channel
            if match.team_msg_id:
                channel = interaction.guild.get_channel(NOTIFY_CHANNEL_ID)
                if channel:
                    try:
                        team_msg = await channel.fetch_message(int(match.team_msg_id))
                        all_ids  = [p[0] for p in team1] + [p[0] for p in team2]
                        mentions = " ".join(f"<@{u}>" for u in all_ids)
                        await team_msg.edit(
                            content=f"📊 **Chia team cho trận `#{self.match_id}`:**\n{mentions}",
                            embed=embed,
                        )
                    except Exception as e:
                        print(f"more_choice: could not edit team_msg: {e}")

            # Disable the select after a choice is made
            for item in self.view.children:
                item.disabled = True
            await interaction.response.edit_message(
                content=(
                    f"✅ Đã áp dụng **Phương án {idx + 1}** (Lệch Elo: `{diff}`) "
                    f"cho trận `#{self.match_id}`!"
                ),
                view=self.view,
            )
        except Exception as e:
            session.rollback()
            await interaction.response.send_message(f"❌ Lỗi hệ thống: {e}", ephemeral=True)
        finally:
            session.close()


class TeamChoiceView(discord.ui.View):
    """Ephemeral view that wraps the team-choice dropdown."""

    def __init__(self, match_id: int, combinations: list, session_factory):
        super().__init__(timeout=300)  # 5-minute window for the admin to choose
        self.add_item(TeamChoiceSelect(match_id, combinations, session_factory))

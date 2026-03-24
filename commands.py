"""
Slash commands related to match management: /create_match and /add.
"""

import uuid
import discord
from discord import app_commands
from datetime import datetime, timedelta
from entity import Player, Match
from helpers import format_vnd, format_vn_time, get_elo_display
from config import GUILD_ID, REGISTER_CHANNEL_ID
from views import MatchView

guild_obj = discord.Object(id=GUILD_ID)


# ── Channel guard ──────────────────────────────────────────────────────────────

def is_register_channel():
    async def predicate(interaction: discord.Interaction) -> bool:
        if interaction.channel_id != REGISTER_CHANNEL_ID:
            await interaction.response.send_message(
                "❌ Lệnh này không thể sử dụng ở đây!", ephemeral=True
            )
            return False
        return True
    return app_commands.check(predicate)


# ── Autocomplete ───────────────────────────────────────────────────────────────

async def time_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> list[app_commands.Choice[str]]:
    if interaction.response.is_done():
        return []
    suggestions = []
    try:
        now = datetime.now().replace(second=0, microsecond=0)
        for i in range(1, 15):
            suggested_time = now + timedelta(minutes=i * 30)
            time_str = suggested_time.strftime("%Y-%m-%d %H:%M")
            if current.lower() in time_str.lower():
                suggestions.append(app_commands.Choice(name=time_str, value=time_str))
                if len(suggestions) >= 10:
                    break
        return suggestions
    except discord.errors.NotFound:
        return []
    except Exception as e:
        print(f"Autocomplete error: {e}")
        return []


# ── Command registration ───────────────────────────────────────────────────────

def register_match_commands(bot, session_factory):

    @bot.tree.command(name="create_match", description="Tạo trận đấu mới", guild=guild_obj)
    @app_commands.choices(elo_type=[
        app_commands.Choice(name="Tất cả", value="all"),
        app_commands.Choice(name="Khoảng (Min-Max)", value="range"),
        app_commands.Choice(name="Dưới hoặc bằng (<= Max)", value="under"),
        app_commands.Choice(name="Trên hoặc bằng (>= Min)", value="above"),
    ])
    @app_commands.describe(
        match_time="Chọn hoặc nhập giờ (Định dạng: YYYY-MM-DD HH:MM) ví dụ 2026-03-23 20:00"
    )
    @app_commands.autocomplete(match_time=time_autocomplete)
    @is_register_channel()
    async def create_match(
        interaction: discord.Interaction,
        match_time: str,
        team_size: int,
        prize: int,
        elo_type: str,
        elo_min: int = 0,
        elo_max: int = 9999,
    ):
        if not interaction.user.guild_permissions.administrator:
            return await interaction.response.send_message("Chỉ Admin mới có quyền!", ephemeral=True)

        session = session_factory()
        try:
            if team_size < 1:
                return await interaction.response.send_message(
                    "❌ Quy mô đội phải ít nhất là 1!", ephemeral=True
                )
            try:
                dt = datetime.strptime(match_time, "%Y-%m-%d %H:%M")
            except ValueError:
                return await interaction.response.send_message(
                    "Định dạng: YYYY-MM-DD HH:MM", ephemeral=True
                )

            m_id = uuid.uuid4()
            req_str = f"{elo_type}:{elo_min}:{elo_max}"

            embed = discord.Embed(title=f"⚔️ THÔNG BÁO SHOWMATCH   `#{str(m_id)[:8]}`", color=discord.Color.blue())
            embed.description = (
                f"## ⏰ Giờ thi đấu: {format_vn_time(dt)}\n"
                f"## 👥 Quy mô: {team_size}vs{team_size}\n"
                f"**Tiền thưởng:** `{format_vnd(prize)}`\n"
                f"**Điều kiện Elo:** `{get_elo_display(req_str)}`"
            )
            embed.add_field(name="Người tham gia (0)", value="Chưa có ai", inline=False)

            view = MatchView(m_id, session_factory)
            await interaction.response.send_message(content="@everyone", embed=embed, view=view)

            msg = await interaction.original_response()
            new_m = Match(
                match_id=m_id,
                match_time=dt,
                team_size=team_size,
                prize=prize,
                elo_requirement=req_str,
                registration_msg_id=str(msg.id),
                status="waiting",
                created_by=interaction.user.id,
            )
            session.add(new_m)
            session.commit()
        finally:
            session.close()

    @bot.tree.command(name="add", description="Xét elo tài khoản", guild=guild_obj)
    async def add_player(
        interaction: discord.Interaction,
        member: discord.Member,
        ingame_name: str,
        elo: int = 1000,
    ):
        if not interaction.user.guild_permissions.administrator:
            return await interaction.response.send_message("Chỉ Admin mới có quyền!", ephemeral=True)

        session = session_factory()
        try:
            player = session.query(Player).filter_by(discord_id=str(member.id)).first()
            if player:
                player.in_game_name = ingame_name
                player.elo = elo
                msg = f"✅ Cập nhật: <@{member.id}> (IGN: `{ingame_name}` - Elo: `{elo}`)"
            else:
                new_player = Player(
                    discord_id=str(member.id), in_game_name=ingame_name, elo=elo
                )
                session.add(new_player)
                msg = f"✨ Đăng ký mới: <@{member.id}> (IGN: `{ingame_name}` - Elo: `{elo}`)"
            session.commit()
            await interaction.response.send_message(msg)
        finally:
            session.close()

    @bot.tree.command(name="add_phieu", description="Thêm phiếu cho người chơi", guild=guild_obj)
    async def add_phieu(
        interaction: discord.Interaction,
        member: discord.Member,
        amount: int,
    ):
        if not interaction.user.guild_permissions.administrator:
            return await interaction.response.send_message("Chỉ Admin mới có quyền!", ephemeral=True)

        if amount <= 0:
            return await interaction.response.send_message(
                "❌ Số phiếu cần thêm phải lớn hơn 0!", ephemeral=True
            )

        session = session_factory()
        try:
            player = session.query(Player).filter_by(discord_id=str(member.id)).first()
            if not player:
                return await interaction.response.send_message(
                    f"❌ <@{member.id}> chưa có dữ liệu trong hệ thống!", ephemeral=True
                )
            player.phieu += amount
            session.commit()
            await interaction.response.send_message(
                f"✅ Đã thêm **{amount}** phiếu cho <@{member.id}>. Phiếu hiện tại: **{player.phieu}**"
            )
        finally:
            session.close()

    @bot.tree.command(name="remove_phieu", description="Trừ phiếu của người chơi", guild=guild_obj)
    async def remove_phieu(
        interaction: discord.Interaction,
        member: discord.Member,
        amount: int,
    ):
        if not interaction.user.guild_permissions.administrator:
            return await interaction.response.send_message("Chỉ Admin mới có quyền!", ephemeral=True)

        if amount <= 0:
            return await interaction.response.send_message(
                "❌ Số phiếu cần trừ phải lớn hơn 0!", ephemeral=True
            )

        session = session_factory()
        try:
            player = session.query(Player).filter_by(discord_id=str(member.id)).first()
            if not player:
                return await interaction.response.send_message(
                    f"❌ <@{member.id}> chưa có dữ liệu trong hệ thống!", ephemeral=True
                )
            if player.phieu < amount:
                return await interaction.response.send_message(
                    f"❌ <@{member.id}> chỉ có **{player.phieu}** phiếu, không đủ để trừ **{amount}**!",
                    ephemeral=True,
                )
            player.phieu -= amount
            session.commit()
            await interaction.response.send_message(
                f"✅ Đã trừ **{amount}** phiếu của <@{member.id}>. Phiếu hiện tại: **{player.phieu}**"
            )
        finally:
            session.close()

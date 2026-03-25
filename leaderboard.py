"""
Leaderboard – view, formatting, and /leaderboard + /me slash commands.
"""

import discord
from discord import app_commands
from datetime import datetime
from helpers import now_vn
from entity import Player
from config import GUILD_ID

guild_obj = discord.Object(id=GUILD_ID)

# ── ANSI colour palette ────────────────────────────────────────────────────────

MEDAL = {1: "🥇", 2: "🥈", 3: "🥉"}
SWORD = "⚔️"

ANSI = {
    1:   "\u001b[1;33m",   # gold   – Top 1
    2:   "\u001b[1;37m",   # white  – Top 2
    3:   "\u001b[0;33m",   # brown  – Top 3
    "n": "\u001b[0;37m",   # grey   – normal
    "h": "\u001b[1;34m",   # blue   – header
    "s": "\u001b[0;30m",   # dark   – separator
    "r": "\u001b[0m",      # reset
}

# ── Tier theo ELO ──────────────────────────────────────────────────────────────
def get_tier(elo: int) -> str:
    if elo >= 1900: return "Legendary"
    if elo >= 1800: return "Diamond"
    if elo >= 1700: return "Platinum"
    if elo >= 1600: return "Gold"
    if elo >= 1500: return "Silver"
    if elo >= 1400: return "Bronze"
    return "Iron"


def _rpad(v, n: int) -> str:
    s = str(v)
    return (s[: n - 1] + "…") if len(s) > n else s.ljust(n)


def _lpad(v, n: int) -> str:
    s = str(v)
    return (s[: n - 1] + "…") if len(s) > n else s.rjust(n)


def get_streak_info(streak: int):
    if streak >= 10:  return f"{streak}!", "🔥"
    if streak >= 5:   return f"{streak}*", "⚡"
    if streak > 0:    return f"{streak} ", "✦"
    if streak <= -5:  return f"{streak}!", "💀"
    if streak < 0:    return f"{streak} ", "❄️"
    return " -- ", "  "


# ── LeaderboardView ────────────────────────────────────────────────────────────

class LeaderboardView(discord.ui.View):
    def __init__(self, session_factory, current_page: int, max_page: int):
        super().__init__(timeout=60)
        self.Session = session_factory
        self.current_page = current_page
        self.max_page = max_page
        self.message: discord.Message | None = None
        self._sync_buttons()

    def _sync_buttons(self):
        self.prev_button.disabled = self.current_page <= 1
        self.next_button.disabled = self.current_page >= self.max_page

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except Exception:
                pass

    def format_leaderboard_text(self, players, start_rank: int) -> str:
        A = ANSI
        header = (
            f"   {A['h']}{_rpad('RANK # TÊN NGƯỜI CHƠI', 22)} "  # 27 → 20
            f"{_lpad('ELO', 8)} {_rpad('TIER', 9)} "              # thêm cột TIER
            f"{_lpad('W', 5)} {_lpad('L', 5)} "
            f"{_lpad('W.RATE', 9)} {_lpad('CHUỖI', 8)}{A['r']}"
        )
        sep = f"   {A['s']}{'━' * 69}{A['r']}"

        lines = [header, sep]
        for i, p in enumerate(players):
            abs_rank = start_rank + i
            total = p.wins + p.losses
            wr = f"{(p.wins / total * 100):.1f}%" if total > 0 else "0.0%"

            medal_icon = MEDAL.get(abs_rank, SWORD)
            color = A.get(abs_rank if abs_rank <= 3 else "n")
            stk_val, stk_icon = get_streak_info(p.streak)
            tier = get_tier(p.elo)                                 # lấy tier

            rank_name = f"#{abs_rank:<2} {p.in_game_name}"
            row = (
                f"{color}{_rpad(rank_name, 20)} "                  # 27 → 20
                f"{_lpad(p.elo, 8)} {_rpad(tier, 9)} "            # thêm tier
                f"{_lpad(p.wins, 5)} {_lpad(p.losses, 5)} "
                f"{_lpad(wr, 9)} {_lpad(stk_val, 8)}{A['r']}"
            )
            lines.append(f"{medal_icon} {row} {stk_icon}")

        return "```ansi\n" + "\n".join(lines) + "\n```"

    async def _render(self, interaction: discord.Interaction):
        session = self.Session()
        try:
            offset = (self.current_page - 1) * 15
            players = (
                session.query(Player)
                .order_by(Player.elo.desc())
                .offset(offset)
                .limit(15)
                .all()
            )
            board_text = self.format_leaderboard_text(players, offset + 1)
            title = f"## 🏆 BẢNG XẾP HẠNG CAO THỦ - TRANG {self.current_page}/{self.max_page}"
            footer = f"> *Cập nhật lúc: {now_vn().strftime('%H:%M:%S')} • Server: PC Optimized*"
            content = f"{title}\n{footer}\n{board_text}"

            self._sync_buttons()
            await interaction.response.edit_message(content=content, view=self)
        finally:
            session.close()

    @discord.ui.button(label="◀️ TRANG TRƯỚC", style=discord.ButtonStyle.gray)
    async def prev_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.current_page -= 1
        await self._render(interaction)

    @discord.ui.button(label="TRANG SAU ▶️", style=discord.ButtonStyle.gray)
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.current_page += 1
        await self._render(interaction)


# ── Registration helper ────────────────────────────────────────────────────────

def register_leaderboard_commands(bot, session_factory):
    """Attach /leaderboard and /me to *bot*'s command tree."""

    @bot.tree.command(name="leaderboard_elo", description="Xem bảng xếp hạng cao thủ", guild=guild_obj)
    async def leaderboard(interaction: discord.Interaction):
        session = session_factory()
        try:
            total_players = session.query(Player).count()
            if total_players == 0:
                return await interaction.response.send_message(
                    "❌ Chưa có dữ liệu người chơi!", ephemeral=True
                )

            max_page = (total_players + 14) // 15
            players = session.query(Player).order_by(Player.elo.desc()).limit(15).all()

            view = LeaderboardView(session_factory, current_page=1, max_page=max_page)
            board_text = view.format_leaderboard_text(players, 1)
            title = f"## 🏆 BẢNG XẾP HẠNG CAO THỦ - TRANG 1/{max_page}"
            footer = f"> *Cập nhật lúc: {now_vn().strftime('%H:%M:%S')}*"

            await interaction.response.send_message(
                content=f"{title}\n{footer}\n{board_text}", view=view
            )
            view.message = await interaction.original_response()
        finally:
            session.close()

    @bot.tree.command(name="me_elo", description="Xem thông số cá nhân", guild=guild_obj)
    async def my_stats(interaction: discord.Interaction):
        session = session_factory()
        try:
            uid = str(interaction.user.id)
            player = session.query(Player).filter_by(discord_id=uid).first()
            if not player:
                return await interaction.response.send_message(
                    "❌ Bạn chưa có dữ liệu trên hệ thống!", ephemeral=True
                )

            rank = session.query(Player).filter(Player.elo > player.elo).count() + 1
            total = player.wins + player.losses
            wr = (player.wins / total * 100) if total > 0 else 0.0

            # Màu & huy hiệu theo Elo
            if player.elo >= 2000:
                color = 0xFFD700   # Vàng – Grandmaster
                tier_icon = "👑"
                tier_name = "Grandmaster"
            elif player.elo >= 1800:
                color = 0xE74C3C   # Đỏ – Diamond
                tier_icon = "💎"
                tier_name = "Diamond"
            elif player.elo >= 1600:
                color = 0x9B59B6   # Tím – Platinum
                tier_icon = "🔮"
                tier_name = "Platinum"
            elif player.elo >= 1400:
                color = 0x3498DB   # Xanh dương – Gold
                tier_icon = "🥇"
                tier_name = "Gold"
            elif player.elo >= 1200:
                color = 0x2ECC71   # Xanh lá – Silver
                tier_icon = "🥈"
                tier_name = "Silver"
            else:
                color = 0x95A5A6   # Xám – Bronze
                tier_icon = "🥉"
                tier_name = "Bronze"

            # Streak icon
            if player.streak >= 3:
                streak_display = f"🔥 +{player.streak}"
            elif player.streak <= -3:
                streak_display = f"❄️ {player.streak}"
            elif player.streak > 0:
                streak_display = f"📈 +{player.streak}"
            elif player.streak < 0:
                streak_display = f"📉 {player.streak}"
            else:
                streak_display = "➖ 0"

            # Winrate bar (10 ký tự)
            filled = round(wr / 10)
            wr_bar = "█" * filled + "░" * (10 - filled)

            embed = discord.Embed(
                title=f"{tier_icon} {player.in_game_name}",
                description=f"**{tier_name}** • Hạng **#{rank}** toàn server",
                color=color,
            )
            embed.set_thumbnail(url=interaction.user.display_avatar.url)

            embed.add_field(name="⚡ Elo", value=f"**{player.elo}**", inline=True)
            embed.add_field(name="🏆 Thắng", value=f"**{player.wins}**", inline=True)
            embed.add_field(name="💀 Thua", value=f"**{player.losses}**", inline=True)

            embed.add_field(
                name="📊 Winrate",
                value=f"`{wr_bar}` **{wr:.1f}%**\n({player.wins}W / {player.losses}L / {total} trận)",
                inline=False,
            )

            embed.add_field(name="⚔️ Chuỗi hiện tại", value=streak_display, inline=True)
            embed.add_field(name="🎫 Phiếu", value=f"**{player.phieu}**", inline=True)

            embed.set_footer(text="📌 Dữ liệu cá nhân • Chỉ bạn thấy được tin nhắn này")

            await interaction.response.send_message(embed=embed, ephemeral=True)

        finally:
            session.close()

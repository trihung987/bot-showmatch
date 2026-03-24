"""
Match lifecycle helpers.

These are standalone async functions (not methods) so they can be called
from both the scheduler task and the AdminControlView without circular imports.
"""

import discord
from entity import Match
from helpers import format_vn_time, format_vnd
from config import REGISTER_CHANNEL_ID, NOTIFY_CHANNEL_ID


async def start_checkin_phase(match: Match, channel: discord.TextChannel, bot, session_factory):
    """
    Transition a match from 'waiting' → 'checkin'.
    Sends the check-in embed and disables the registration message buttons.
    """
    from views import MatchView, CheckInView  # late import to avoid circular

    channel_register = bot.get_channel(REGISTER_CHANNEL_ID)
    match.status = "checkin"

    tags = " ".join([f"<@{u}>" for u in match.participants])
    embed = discord.Embed(title="🔔 CHECK-IN SHOWMATCH", color=discord.Color.gold())
    embed.description = (
        f"## ⚔️ Trận: `#{str(match.match_id)[:8]}`\n"
        f"**Giờ thi đấu:** {format_vn_time(match.match_time)}\n"
        f"**Quy mô:** {match.team_size}vs{match.team_size}\n"
        f"**Tiền thưởng:** {format_vnd(match.prize)}\n"
    )
    embed.add_field(name="Danh sách đã check-in (0)", value="Chưa có ai", inline=False)

    c_msg = await channel.send(
        content=tags, embed=embed, view=CheckInView(match.match_id, session_factory)
    )
    match.checkin_msg_id = str(c_msg.id)

    # Disable the registration buttons
    try:
        reg_msg = await channel_register.fetch_message(int(match.registration_msg_id))
        v = MatchView(match.match_id, session_factory)
        v.disable_all()
        await reg_msg.edit(view=v)
    except Exception:
        pass


async def cancel_match_logic(match: Match, channel: discord.TextChannel, reason: str, bot, session_factory):
    """
    Cancel a match: disable all buttons, update embeds, send cancellation notice.
    `channel` is the channel where the cancellation announcement should go (usually REGISTER_CHANNEL).
    """
    from views import MatchView  # late import to avoid circular
    from discord.ui import View

    channel_register = bot.get_channel(REGISTER_CHANNEL_ID)
    channel_notify = bot.get_channel(NOTIFY_CHANNEL_ID)
    match.status = "cancelled"
    vn_time = format_vn_time(match.match_time)

    # Disable registration buttons
    try:
        reg_msg = await channel_register.fetch_message(int(match.registration_msg_id))
        v = MatchView(match.match_id, session_factory)
        v.disable_all()
        await reg_msg.edit(view=v)
    except Exception:
        pass

    # Update check-in message to show cancellation
    if match.checkin_msg_id:
        try:
            c_msg = await channel_notify.fetch_message(int(match.checkin_msg_id))
            v = View.from_message(c_msg)
            for item in v.children:
                item.disabled = True

            embed = c_msg.embeds[0]
            old_desc = embed.description or ""
            embed.description = f"{old_desc}\n\n**❌ Trận đấu đã bị hủy\n Lý do {reason}**"
            embed.color = 0xFF0000

            await c_msg.edit(embed=embed, view=v)
        except Exception:
            pass

    # Send cancellation notice as a reply to the registration message
    cancel_embed = discord.Embed(
        title="🚫 THÔNG BÁO HỦY TRẬN",
        description=(
            f"Trận đấu `#{str(match.match_id)[:8]}` dự kiến lúc **{vn_time}** đã bị hủy.\n"
            f"**Lý do:** {reason}"
        ),
        color=discord.Color.red(),
    )
    try:
        reg_msg = await channel_register.fetch_message(int(match.registration_msg_id))
        await reg_msg.reply(embed=cancel_embed)
    except Exception:
        pass

"""
Match lifecycle helpers.

These are standalone async functions (not methods) so they can be called
from both the scheduler task and the AdminControlView without circular imports.
"""

import discord
from entity import Match, Player
from helpers import format_vn_time, format_vnd
from config import REGISTER_CHANNEL_ID, NOTIFY_CHANNEL_ID
import message_store as ms


async def start_checkin_phase(match: Match, channel: discord.TextChannel, bot, session_factory):
    """
    Transition a match from 'waiting' → 'checkin'.
    Sends the check-in embed and disables the registration message buttons.
    """
    from views import MatchView, CheckInView  # late import to avoid circular

    channel_register = bot.get_channel(REGISTER_CHANNEL_ID)
    match.status = "checkin"

    bo_line = f"**Best Of:** BO{match.bo}\n" if match.bo else ""
    tags = " ".join([f"<@{u}>" for u in match.participants])
    embed = discord.Embed(title="🔔 CHECK-IN SHOWMATCH", color=discord.Color.gold())
    embed.description = (
        f"## ⚔️ Trận: `#{match.match_id}`\n"
        f"**Giờ thi đấu:** {format_vn_time(match.match_time)}\n"
        f"**Quy mô:** {match.team_size}vs{match.team_size}\n"
        f"**Tiền thưởng:** {format_vnd(match.prize)}\n"
        f"{bo_line}"
    )
    embed.add_field(name=f"Danh sách đã check-in (0/{len(match.participants)})", value="Chưa có ai", inline=False)

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

def get_refund_players(match, scope):
    if scope == "all":
        return list(match.participants) or []
    elif scope == "teams":
        return list(set((match.team1 or []) + (match.team2 or [])))
    return []  

async def cancel_match_logic(match: Match, channel: discord.TextChannel, reason: str, bot, session_factory, refund_scope="all"):
    """
    Cancel a match: disable all buttons, update embeds, send cancellation notice.
    Refunds 1 phieu to every registered participant.
    `channel` is the channel where the cancellation announcement should go (usually REGISTER_CHANNEL).
    Tracks the cancel notification message in message_store for later cleanup.
    """
    from views import MatchView  # late import to avoid circular
    from discord.ui import View
    channel_register = bot.get_channel(REGISTER_CHANNEL_ID)
    channel_notify = bot.get_channel(NOTIFY_CHANNEL_ID)
    match.status = "cancelled"

    # Refund 1 phieu to all registered participants or team1+team2
    participant_ids = get_refund_players(match, refund_scope)
    if participant_ids:
        phieu_session = session_factory()
        try:
            players = phieu_session.query(Player).filter(
                Player.discord_id.in_(participant_ids)
            ).all()
            for p in players:
                p.phieu += 1
            phieu_session.commit()
        except Exception as e:
            phieu_session.rollback()
            print(f"Phieu refund error on cancel match {match.match_id}: {e}")
        finally:
            phieu_session.close()

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
            f"Trận đấu `#{match.match_id}` dự kiến lúc **{vn_time}** đã bị hủy.\n"
            f"**Lý do:** {reason}"
        ),
        color=discord.Color.red(),
    )
    try:
        reg_msg = await channel_register.fetch_message(int(match.registration_msg_id))
        cancel_msg = await reg_msg.reply(embed=cancel_embed)
        # Track cancel notification in message_store for later cleanup
        ms.add_extra_msg(match.match_id, channel_register.id, str(cancel_msg.id))
    except Exception:
        pass


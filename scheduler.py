"""
Background task: polls active matches every minute and drives state transitions.

State machine (thresholds are configurable via /set_time_stages):
  waiting ──(T-STAGE_1, full)────────► checkin
  waiting ──(T-STAGE_1, not full)────► notified_low
  notified_low ──(T-STAGE_2, full)───► checkin
  notified_low ──(T-STAGE_2, not)────► cancelled
  checkin ──(T-STAGE_3)──────────────► team split (team_msg_id set)
  checkin ──(T-0, full check-in)─────► playing
  checkin ──(T-0, not full)──────────► cancelled

Default thresholds: STAGE_1=12 h, STAGE_2=11 h, STAGE_3=6 h (see config.py).

A separate cleanup_scheduler task runs every 5 minutes and deletes all
Discord messages for matches that have been cancelled or finished for 6+ hours.
"""

import discord
from datetime import datetime, timedelta, timezone
from discord.ext import tasks

import config
from entity import Match
from helpers import format_vn_time, now_vn
from config import NOTIFY_CHANNEL_ID, REGISTER_CHANNEL_ID, START_SHOWMATCH_CHANNEL_ID
from match_lifecycle import start_checkin_phase, cancel_match_logic
from utils import auto_split_teams, build_start_showmatch_embed
from discord.ui import View
from views import AdminControlView
import message_store as ms


def setup_scheduler(bot, session_factory):
    """Create and return the match_scheduler and cleanup_scheduler loops bound to *bot*."""

    @tasks.loop(minutes=1)
    async def match_scheduler():
        session = session_factory()
        try:
            now = now_vn()
            channel_notify = bot.get_channel(NOTIFY_CHANNEL_ID)
            channel_register = bot.get_channel(REGISTER_CHANNEL_ID)
            if not channel_notify or not channel_register:
                return

            active_matches = session.query(Match).filter(
                Match.status.in_(["waiting", "checkin", "notified_low"])
            ).all()
            print(active_matches)
            for m in active_matches:
                total_slots = m.team_size * 2
                time_diff = m.match_time - now
                minutes_left = time_diff.total_seconds() / 60

                # ── T-0: match time reached ──────────────────────────────────
                print(m.match_id," time left",minutes_left)
                if minutes_left <= 0:
                    if m.status == "checkin":
                        if len(m.checked_in) < total_slots:
                            await cancel_match_logic(
                                m, channel_notify,
                                "Không đủ người check-in đúng giờ thi đấu.",
                                bot, session_factory,
                            )
                            _commit(session, m.match_id, "T-0 status")
                            # Điều kiện này đang nghi vấn là k dùng nên có thể bỏ vì k cần thiết đã có T dưới handle rồi
                        else:
                            # Commit status change first, independent of Discord delivery
                            m.status = "playing"
                            _commit(session, m.match_id, "T-0 status")
                            try:
                                embed = discord.Embed(
                                    title="🎮 TRẬN ĐẤU BẮT ĐẦU",
                                    description="Trận đấu đang diễn ra. Admin vui lòng cập nhật kết quả khi kết thúc.",
                                    color=discord.Color.green(),
                                )
                                team_msg = await channel_notify.fetch_message(int(m.team_msg_id))
                                admin_msg = await team_msg.reply(
                                    embed=embed,
                                    view=AdminControlView(m.match_id, session_factory),
                                )
                                ms.add_extra_msg(m.match_id, channel_notify.id, str(admin_msg.id))
                            except Exception as e:
                                print(f"T-0 Discord error match {m.match_id}: {e}")
                    else:
                        _commit(session, m.match_id, "T-0")
                    continue

                # ── T-STAGE_3: split teams ───────────────────────────────────
                if minutes_left <= config.TIME_STAGE_3 and m.status == "checkin" and not m.team_msg_id:
                    if len(m.checked_in) < total_slots:
                        await cancel_match_logic(
                            m, channel_notify,
                            "Không đủ người check-in đúng giờ thi đấu.",
                            bot, session_factory,
                        )
                        _commit(session, m.match_id, "T-STAGE_3 status")
                        continue

                    team_embed = await auto_split_teams(m.match_id, session)
                    print("divide team", team_embed)
                    if team_embed:
                        # Refund 1 phieu to participants who were not selected for any team
                        from entity import Player as PlayerEntity
                        all_team_ids = set(m.team1) | set(m.team2)
                        unselected_ids = [uid for uid in m.participants if uid not in all_team_ids]
                        if unselected_ids:
                            unselected_players = session.query(PlayerEntity).filter(
                                PlayerEntity.discord_id.in_(unselected_ids)
                            ).all()
                            for p in unselected_players:
                                p.phieu += 1

                        # Derive team data from the match object for the START_SHOWMATCH embed
                        team_players = session.query(PlayerEntity).filter(
                            PlayerEntity.discord_id.in_(list(all_team_ids))
                        ).all()
                        team_player_map = {p.discord_id: p for p in team_players}
                        team1_data = [
                            (uid, team_player_map[uid].in_game_name, team_player_map[uid].elo)
                            for uid in m.team1 if uid in team_player_map
                        ]
                        team2_data = [
                            (uid, team_player_map[uid].in_game_name, team_player_map[uid].elo)
                            for uid in m.team2 if uid in team_player_map
                        ]
                        team_diff = abs(sum(p[2] for p in team1_data) - sum(p[2] for p in team2_data))

                        # Commit team assignment before any Discord calls
                        _commit(session, m.match_id, "T-STAGE_3 teams")
                        try:
                            mentions = " ".join([f"<@{u}>" for u in all_team_ids])
                            c_msg = await channel_notify.fetch_message(int(m.checkin_msg_id))
                            divide_team_msg = await c_msg.reply(
                                content=(
                                    f"📊 **Đã cân bằng elo tốt nhất trong số danh sách người đã check-in\n"
                                    f"Chia team cho trận `#{m.match_id}`:**\n{mentions}"
                                ),
                                embed=team_embed,
                            )
                            m.team_msg_id = str(divide_team_msg.id)
                            _commit(session, m.match_id, "T-STAGE_3 msg_id")
                        except Exception as e:
                            print(f"T-STAGE_3 Discord send error match {m.match_id}: {e}")

                        # Send announcement to START_SHOWMATCH_CHANNEL_ID and save message ID
                        try:
                            channel_start = bot.get_channel(START_SHOWMATCH_CHANNEL_ID)
                            if channel_start and team1_data and team2_data:
                                start_embed = build_start_showmatch_embed(
                                    m.match_id, m.match_time, team1_data, team2_data, team_diff,
                                    bo=m.bo,
                                )
                                start_msg = await channel_start.send(
                                    content="@everyone Anh em điểm danh chuẩn bị xem siêu kinh điển nào! 🔥",
                                    embed=start_embed,
                                )
                                m.start_match_message_id = str(start_msg.id)
                                _commit(session, m.match_id, "T-STAGE_3 start_msg_id")
                        except Exception as e:
                            print(f"START_SHOWMATCH send error match {m.match_id}: {e}")

                        # Disable check-in button (best-effort)
                        try:
                            c_msg = await channel_notify.fetch_message(int(m.checkin_msg_id))
                            v = View.from_message(c_msg)
                            for item in v.children:
                                item.disabled = True
                            await c_msg.edit(view=v)
                        except Exception:
                            pass
                    continue

                # ── T-STAGE_2: final call or cancel ─────────────────────────
                if minutes_left <= config.TIME_STAGE_2 and m.status in ["waiting", "notified_low"]:
                    if len(m.participants) < total_slots:
                        await cancel_match_logic(
                            m, channel_register,
                            "Không đủ người tham gia sau thời gian gia hạn bổ sung.",
                            bot, session_factory,
                        )
                    else:
                        await start_checkin_phase(m, channel_notify, bot, session_factory)
                    _commit(session, m.match_id, "T-STAGE_2")
                    continue

                # ── T-STAGE_1: warn or start check-in ───────────────────────
                if minutes_left <= config.TIME_STAGE_1 and m.status == "waiting":
                    if len(m.participants) >= total_slots:
                        await start_checkin_phase(m, channel_notify, bot, session_factory)
                    else:
                        missing = total_slots - len(m.participants)
                        try:
                            reg_msg = await channel_register.fetch_message(
                                int(m.registration_msg_id)
                            )
                            stage_diff = config.TIME_STAGE_1 - config.TIME_STAGE_2
                            end_time = now + timedelta(minutes=stage_diff)
                            supplement_msg = await reg_msg.reply(
                                f"📢 **THÔNG BÁO BỔ SUNG** @everyone\n"
                                f"Trận đấu lúc **{format_vn_time(m.match_time)}** hiện đang thiếu "
                                f"**{missing}** người.\n"
                                f"Các bạn vui lòng đăng ký bổ sung trong **{stage_diff} phút** tới để trận đấu được diễn ra!\n"
                                f"Kết thúc đăng ký bổ sung lúc **{format_vn_time(end_time)}**"
                            )
                            # Track supplement notification for later cleanup
                            ms.add_extra_msg(m.match_id, channel_register.id, str(supplement_msg.id))
                            m.status = "notified_low"
                        except Exception as e:
                            print(f"Lỗi khi fetch hoặc reply message: {e}")
                    _commit(session, m.match_id, "T-STAGE_1")
                    continue

        except Exception as e:
            print(f"Task Error: {e}")
            session.rollback()
        finally:
            session.close()

    @tasks.loop(minutes=5)
    async def cleanup_scheduler():
        """Delete all Discord messages for matches that ended 6+ hours ago."""
        session = session_factory()
        try:
            now = now_vn()
            ended_matches = session.query(Match).filter(
                Match.status.in_(["cancelled", "finished"])
            ).all()

            for m in ended_matches:
                if ms.is_cleaned_up(m.match_id):
                    continue

                # First time we see this match as ended: record the time
                if ms.get_match_ended(m.match_id) is None:
                    ms.set_match_ended(m.match_id, now)
                    continue

                ended_at = ms.get_match_ended(m.match_id)
                hours_elapsed = (now - ended_at).total_seconds() / 3600

                if hours_elapsed >= 6:
                    try:
                        await _delete_match_messages(bot, m)
                    except Exception as del_err:
                        print(f"Cleanup: error deleting messages for match {m.match_id}: {del_err}")
                    # Mark as cleaned up regardless so we don't retry indefinitely
                    ms.remove_match(m.match_id)

        except Exception as e:
            print(f"Cleanup task error: {e}")
        finally:
            session.close()

    return match_scheduler, cleanup_scheduler


async def _delete_match_messages(bot, match):
    """Delete all Discord messages associated with a match (best-effort)."""
    channel_register = bot.get_channel(REGISTER_CHANNEL_ID)
    channel_notify = bot.get_channel(NOTIFY_CHANNEL_ID)
    channel_start = bot.get_channel(START_SHOWMATCH_CHANNEL_ID)

    async def _try_delete(channel, msg_id_str):
        if not channel or not msg_id_str:
            return
        try:
            msg = await channel.fetch_message(int(msg_id_str))
            await msg.delete()
        except Exception:
            pass

    await _try_delete(channel_register, match.registration_msg_id)
    await _try_delete(channel_notify, match.checkin_msg_id)
    await _try_delete(channel_notify, match.team_msg_id)
    await _try_delete(channel_start, match.start_match_message_id)

    for ch_id, msg_id in ms.get_extra_msgs(match.match_id):
        channel = bot.get_channel(ch_id)
        await _try_delete(channel, msg_id)


def _commit(session, match_id, label):
    try:
        session.commit()
    except Exception as e:
        print(f"Scheduler commit error ({label}) match {match_id}: {e}")
        session.rollback()
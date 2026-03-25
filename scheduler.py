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
"""

import discord
from datetime import datetime, timedelta, timezone
from discord.ext import tasks

import config
from entity import Match
from helpers import format_vn_time
from config import NOTIFY_CHANNEL_ID, REGISTER_CHANNEL_ID
from match_lifecycle import start_checkin_phase, cancel_match_logic
from utils import auto_split_teams
from discord.ui import View
from views import AdminControlView


def setup_scheduler(bot, session_factory):
    """Create and return the match_scheduler loop bound to *bot*."""

    @tasks.loop(minutes=1)
    async def match_scheduler():
        session = session_factory()
        try:
            now = datetime.now()
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
                                await team_msg.reply(
                                    embed=embed,
                                    view=AdminControlView(m.match_id, session_factory),
                                )
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
                            await reg_msg.reply(
                                f"📢 **THÔNG BÁO BỔ SUNG** @everyone\n"
                                f"Trận đấu lúc **{format_vn_time(m.match_time)}** hiện đang thiếu "
                                f"**{missing}** người.\n"
                                f"Các bạn vui lòng đăng ký bổ sung trong **{stage_diff} phút** tới để trận đấu được diễn ra!\n"
                                f"Kết thúc đăng ký bổ sung lúc **{format_vn_time(end_time)}**"
                            )
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

    return match_scheduler


def _commit(session, match_id, label):
    try:
        session.commit()
    except Exception as e:
        print(f"Scheduler commit error ({label}) match {match_id}: {e}")
        session.rollback()


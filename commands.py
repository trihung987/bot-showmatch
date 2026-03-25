"""
Slash commands related to match management: /create_match and /add.
"""

import re
import discord
from discord import app_commands
from datetime import datetime, timedelta
from entity import Player, Match
from helpers import format_vnd, format_vn_time, get_elo_display, now_vn
import config
from config import GUILD_ID, REGISTER_CHANNEL_ID, NOTIFY_CHANNEL_ID
from views import MatchView, CheckInView, AdminControlView, TeamChoiceView
from utils import auto_split_teams, generate_team_combinations, build_start_showmatch_embed

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
        now = now_vn().replace(second=0, microsecond=0)
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

            req_str = f"{elo_type}:{elo_min}:{elo_max}"

            new_m = Match(
                match_time=dt,
                team_size=team_size,
                prize=prize,
                elo_requirement=req_str,
                status="waiting",
                created_by=interaction.user.id,
            )
            session.add(new_m)
            session.flush()  # get autoincrement match_id

            embed = discord.Embed(title=f"⚔️ THÔNG BÁO SHOWMATCH   `#{new_m.match_id}`", color=discord.Color.blue())
            embed.description = (
                f"## ⏰ Giờ thi đấu: {format_vn_time(dt)}\n"
                f"## 👥 Quy mô: {team_size}vs{team_size}\n"
                f"**Tiền thưởng:** `{format_vnd(prize)}`\n"
                f"**Điều kiện Elo:** `{get_elo_display(req_str)}`"
            )
            embed.add_field(name="Người tham gia (0)", value="Chưa có ai", inline=False)

            view = MatchView(new_m.match_id, session_factory)
            await interaction.response.send_message(content="@everyone", embed=embed, view=view)

            msg = await interaction.original_response()
            new_m.registration_msg_id = str(msg.id)
            session.commit()
        finally:
            session.close()

    @bot.tree.command(name="add_elo", description="Xét elo tài khoản", guild=guild_obj)
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

    # ── /set_time_stages ──────────────────────────────────────────────────────

    @bot.tree.command(
        name="set_time_stages",
        description="Đặt các mốc thời gian check-in và chia team (đơn vị: giờ)",
        guild=guild_obj,
    )
    @app_commands.describe(
        stage1="Mốc 1: Cảnh báo / bắt đầu check-in (giờ, mặc định 12)",
        stage2="Mốc 2: Check-in lần cuối hoặc hủy trận (giờ, mặc định 11)",
        stage3="Mốc 3: Tự động chia team (giờ, mặc định 6)",
    )
    async def set_time_stages(
        interaction: discord.Interaction,
        stage1: int,
        stage2: int,
        stage3: int,
    ):
        if not interaction.user.guild_permissions.administrator:
            return await interaction.response.send_message("Chỉ Admin mới có quyền!", ephemeral=True)

        if not (stage1 > stage2 > stage3 > 0):
            return await interaction.response.send_message(
                "❌ Các mốc phải thỏa mãn: Mốc 1 > Mốc 2 > Mốc 3 > 0 (đơn vị giờ)!",
                ephemeral=True,
            )

        config.TIME_STAGE_1 = stage1 * 60
        config.TIME_STAGE_2 = stage2 * 60
        config.TIME_STAGE_3 = stage3 * 60

        await interaction.response.send_message(
            f"✅ Đã cập nhật mốc thời gian:\n"
            f"- **Mốc 1** (cảnh báo / bắt đầu check-in): **{stage1} giờ** trước trận\n"
            f"- **Mốc 2** (check-in lần cuối / hủy nếu thiếu): **{stage2} giờ** trước trận\n"
            f"- **Mốc 3** (tự động chia team): **{stage3} giờ** trước trận\n"
            f"⚠️ Lưu ý: Thay đổi có hiệu lực ngay và sẽ được đặt lại về mặc định khi bot khởi động lại.",
            ephemeral=True,
        )

    # ── /create_match_now ─────────────────────────────────────────────────────

    @bot.tree.command(
        name="create_match_now",
        description="Tạo trận đấu ngay: chọn người chơi đã đăng ký → tự check-in → chia team",
        guild=guild_obj,
    )
    @app_commands.choices(elo_type=[
        app_commands.Choice(name="Tất cả", value="all"),
        app_commands.Choice(name="Khoảng (Min-Max)", value="range"),
        app_commands.Choice(name="Dưới hoặc bằng (<= Max)", value="under"),
        app_commands.Choice(name="Trên hoặc bằng (>= Min)", value="above"),
    ])
    @app_commands.describe(
        team_size="Số người mỗi đội",
        prize="Tiền thưởng (VND)",
        players="Danh sách @mention người chơi, cách nhau bằng dấu cách (cần đủ team_size × 2 người)",
        elo_type="Điều kiện Elo (mặc định: tất cả)",
        elo_min="Elo tối thiểu (dùng với range / above)",
        elo_max="Elo tối đa (dùng với range / under)",
    )
    async def create_match_now(
        interaction: discord.Interaction,
        team_size: int,
        prize: int,
        players: str,
        elo_type: str = "all",
        elo_min: int = 0,
        elo_max: int = 9999,
    ):
        if not interaction.user.guild_permissions.administrator:
            return await interaction.response.send_message("Chỉ Admin mới có quyền!", ephemeral=True)

        if team_size < 1:
            return await interaction.response.send_message(
                "❌ Quy mô đội phải ít nhất là 1!", ephemeral=True
            )

        # Parse Discord mentions (<@123> or <@!123>) and plain IDs; deduplicate preserving order
        mention_ids = re.findall(r'<@!?(\d+)>', players)
        plain_ids = re.findall(r'\b(\d{17,20})\b', players)
        player_ids = list(dict.fromkeys(mention_ids + plain_ids))

        required = team_size * 2
        if len(player_ids) < required:
            return await interaction.response.send_message(
                f"❌ Cần ít nhất **{required}** người cho trận **{team_size}vs{team_size}** "
                f"(hiện cung cấp: {len(player_ids)}).",
                ephemeral=True,
            )

        # Warn if more players were given than needed, then take exactly required
        excess = len(player_ids) - required
        player_ids = player_ids[:required]

        await interaction.response.defer(ephemeral=True)

        session = session_factory()
        try:
            # Validate all players are registered in DB
            db_players = session.query(Player).filter(Player.discord_id.in_(player_ids)).all()
            db_player_map = {p.discord_id: p for p in db_players}
            missing_ids = [pid for pid in player_ids if pid not in db_player_map]
            if missing_ids:
                mentions_str = " ".join([f"<@{pid}>" for pid in missing_ids])
                return await interaction.followup.send(
                    f"❌ Người chơi chưa được xét elo (chưa có trong hệ thống): {mentions_str}",
                    ephemeral=True,
                )

            dt = now_vn().replace(second=0, microsecond=0)
            req_str = f"{elo_type}:{elo_min}:{elo_max}"

            # Create match directly in "checkin" status with all players checked-in
            new_m = Match(
                match_time=dt,
                team_size=team_size,
                prize=prize,
                elo_requirement=req_str,
                status="checkin",
                participants=player_ids,
                checked_in=player_ids,
                created_by=str(interaction.user.id),
            )
            session.add(new_m)
            session.flush()  # make the row visible for auto_split_teams query and get autoincrement match_id

            # Auto-split teams
            team_embed = await auto_split_teams(new_m.match_id, session)
            print("chia team now")
            if not team_embed:
                session.rollback()
                return await interaction.followup.send(
                    "❌ Không thể chia team. Vui lòng kiểm tra lại số người chơi.", ephemeral=True
                )
            # Derive team data from the match object (set by auto_split_teams) and db_player_map
            team1_data = [
                (uid, db_player_map[uid].in_game_name, db_player_map[uid].elo)
                for uid in new_m.team1 if uid in db_player_map
            ]
            team2_data = [
                (uid, db_player_map[uid].in_game_name, db_player_map[uid].elo)
                for uid in new_m.team2 if uid in db_player_map
            ]
            team_diff = abs(sum(p[2] for p in team1_data) - sum(p[2] for p in team2_data))
            for p in db_players:
                p.phieu -= 1
                if p.phieu <= 0:
                    session.rollback()
                    return await interaction.followup.send(
                        f"❌ Người chơi <@{p.discord_id}> không đủ phiếu để tham gia.", ephemeral=True
                    )
                
            session.commit()

            channel_register = bot.get_channel(REGISTER_CHANNEL_ID)
            channel_notify = bot.get_channel(NOTIFY_CHANNEL_ID)

            # ── Registration embed (buttons disabled) ──────────────────────
            reg_embed = discord.Embed(
                title=f"⚔️ THÔNG BÁO SHOWMATCH   `#{new_m.match_id}`",
                color=discord.Color.blue(),
            )
            player_list_str = "\n".join([
                f"<@{uid}> - {db_player_map[uid].in_game_name}"
                for uid in player_ids
            ])
            reg_embed.description = (
                f"## ⏰ Giờ thi đấu: {format_vn_time(dt)}\n"
                f"## 👥 Quy mô: {team_size}vs{team_size}\n"
                f"**Tiền thưởng:** `{format_vnd(prize)}`\n"
                f"**Điều kiện Elo:** `{get_elo_display(req_str)}`"
            )
            reg_embed.add_field(
                name=f"Người tham gia ({len(player_ids)})",
                value=player_list_str,
                inline=False,
            )
            reg_view = MatchView(new_m.match_id, session_factory)
            reg_view.disable_all()
            print("send every one")
            reg_msg = await channel_register.send(
                content="@everyone", embed=reg_embed, view=reg_view
            )
            new_m.registration_msg_id = str(reg_msg.id)

            # ── Check-in embed (all players auto checked-in, button disabled) ──
            tags = " ".join([f"<@{uid}>" for uid in player_ids])
            checkin_embed = discord.Embed(title="🔔 CHECK-IN SHOWMATCH", color=discord.Color.gold())
            checkin_embed.description = (
                f"## ⚔️ Trận: `#{new_m.match_id}`\n"
                f"**Giờ thi đấu:** {format_vn_time(dt)}\n"
                f"**Quy mô:** {team_size}vs{team_size}\n"
                f"**Tiền thưởng:** {format_vnd(prize)}\n"
            )
            checkin_list_str = "\n".join([
                f"- {db_player_map[uid].in_game_name} ✅" for uid in player_ids
            ])
            checkin_embed.add_field(
                name=f"Danh sách đã check-in ({len(player_ids)}/{len(player_ids)})",
                value=checkin_list_str,
                inline=False,
            )
            checkin_view = CheckInView(new_m.match_id, session_factory)
            for item in checkin_view.children:
                item.disabled = True
            print("send notify")
            c_msg = await channel_notify.send(content=tags, embed=checkin_embed, view=checkin_view)
            new_m.checkin_msg_id = str(c_msg.id)

            # ── Team embed ─────────────────────────────────────────────────
            all_team_ids = set(new_m.team1) | set(new_m.team2)
            team_mentions = " ".join([f"<@{u}>" for u in all_team_ids])
            divide_team_msg = await c_msg.reply(
                content=(
                    f"📊 **Chia team cho trận `#{new_m.match_id}`:**\n{team_mentions}"
                ),
                embed=team_embed,
            )
            new_m.team_msg_id = str(divide_team_msg.id)

            # ── Send announcement to START_SHOWMATCH_CHANNEL_ID ─────────────
            try:
                channel_start = bot.get_channel(config.START_SHOWMATCH_CHANNEL_ID)
                if channel_start and team1_data and team2_data:
                    start_embed = build_start_showmatch_embed(new_m.match_id, dt, team1_data, team2_data, team_diff)
                    await channel_start.send(
                        content="@everyone Anh em điểm danh chuẩn bị xem siêu kinh điển nào! 🔥",
                        embed=start_embed,
                    )
            except Exception as e:
                print(f"START_SHOWMATCH send error match {new_m.match_id}: {e}")

            # ── Admin control embed ────────────────────────────────────────
            new_m.status = "playing"
            session.commit()

            admin_embed = discord.Embed(
                title="🎮 TRẬN ĐẤU BẮT ĐẦU",
                description="Trận đấu đang diễn ra. Admin vui lòng cập nhật kết quả khi kết thúc.",
                color=discord.Color.green(),
            )
            await divide_team_msg.reply(
                embed=admin_embed,
                view=AdminControlView(new_m.match_id, session_factory),
            )

            print("send for me")
            await interaction.followup.send(
                f"✅ Trận đấu `#{new_m.match_id}` đã được tạo, check-in và chia team thành công!"
                + (f"\n⚠️ Có **{excess}** người chơi dư không được đưa vào trận." if excess > 0 else ""),
                ephemeral=True,
            )
        except Exception as e:
            session.rollback()
            print(f"create_match_now error: {e}")
            await interaction.followup.send(f"❌ Lỗi hệ thống: {e}", ephemeral=True)
        finally:
            session.close()

    # ── /more_choice ──────────────────────────────────────────────────────────

    @bot.tree.command(
        name="more_choice",
        description="Hiển thị nhiều phương án chia team để admin chọn và áp dụng cho trận đấu",
        guild=guild_obj,
    )
    @app_commands.describe(
        match_id="ID trận đấu cần chia lại team",
    )
    async def more_choice(
        interaction: discord.Interaction,
        match_id: int,
    ):
        if not interaction.user.guild_permissions.administrator:
            return await interaction.response.send_message("Chỉ Admin mới có quyền!", ephemeral=True)

        await interaction.response.defer(ephemeral=True)

        session = session_factory()
        try:
            match = session.query(Match).filter_by(match_id=match_id).first()
            if not match:
                return await interaction.followup.send(
                    f"❌ Không tìm thấy trận đấu `#{match_id}`!", ephemeral=True
                )

            player_ids = list(match.checked_in or [])
            team_size  = match.team_size

            if len(player_ids) < team_size * 2:
                return await interaction.followup.send(
                    f"❌ Trận `#{match_id}` chưa đủ người check-in "
                    f"(cần {team_size * 2}, hiện có {len(player_ids)}).",
                    ephemeral=True,
                )

            # Fetch player data from DB
            db_players    = session.query(Player).filter(Player.discord_id.in_(player_ids)).all()
            db_player_map = {p.discord_id: p for p in db_players}
            missing_ids   = [pid for pid in player_ids if pid not in db_player_map]
            if missing_ids:
                mentions_str = " ".join(f"<@{pid}>" for pid in missing_ids)
                return await interaction.followup.send(
                    f"❌ Người chơi chưa có trong hệ thống: {mentions_str}", ephemeral=True
                )

            player_data = [
                (db_player_map[pid].discord_id, db_player_map[pid].in_game_name, db_player_map[pid].elo)
                for pid in player_ids
            ]

            combinations = generate_team_combinations(player_data, team_size)
            if not combinations:
                return await interaction.followup.send(
                    "❌ Không thể tạo phương án chia team!", ephemeral=True
                )

            # Build a compact text preview (one line per option)
            preview_lines = []
            for i, (t1, t2, d) in enumerate(combinations):
                sum1     = sum(p[2] for p in t1)
                sum2     = sum(p[2] for p in t2)
                t1_names = ", ".join(p[1] for p in t1)
                t2_names = ", ".join(p[1] for p in t2)
                preview_lines.append(
                    f"**{i + 1}.** Lệch `{d}` | 🔵 {t1_names} (Σ{sum1}) | 🔴 {t2_names} (Σ{sum2})"
                )

            preview_text = "\n".join(preview_lines)
            # Guard against Discord's 2000-char message limit; break at a line boundary
            if len(preview_text) > 1800:
                cut = preview_text[:1800].rfind("\n")
                preview_text = (preview_text[:cut] if cut > 0 else preview_text[:1800]) + "\n…"

            view = TeamChoiceView(match_id, combinations, session_factory)
            await interaction.followup.send(
                content=(
                    f"📊 **Các phương án chia team cho trận `#{match_id}`** "
                    f"({len(combinations)} phương án):\n\n"
                    f"{preview_text}\n\n"
                    f"⬇️ Chọn một phương án bên dưới để áp dụng:"
                ),
                view=view,
                ephemeral=True,
            )
        finally:
            session.close()
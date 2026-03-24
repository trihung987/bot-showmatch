import os
import uuid
import discord
from discord.ext import commands, tasks
from discord import app_commands
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from datetime import datetime, timedelta
from entity import Base, Player, Match
from dotenv import load_dotenv
from utils import auto_split_teams, balance_teams_heuristic
from discord.ui import View

# ---------------- CONFIG ----------------
load_dotenv()
TOKEN = os.getenv("TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")
GUILD_ID = 1485525920696635445 
NOTIFY_CHANNEL_ID = 1485525921351073904
REGISTER_CHANNEL_ID=1485525921351073903

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(bind=engine)
Base.metadata.create_all(engine)

intents = discord.Intents.default()
intents.members = True 
bot = commands.Bot(command_prefix='!', intents=intents)
guild_obj = discord.Object(id=GUILD_ID)

def is_register_channel():
    async def predicate(interaction: discord.Interaction) -> bool:
        if interaction.channel_id != REGISTER_CHANNEL_ID:
            await interaction.response.send_message(
                f"❌ Lệnh này không thể sử dụng ở đây!", 
                ephemeral=True
            )
            return False
        return True
    return app_commands.check(predicate)

# ---------------- UTILS ----------------

def format_vnd(amount: int):
    return f"{amount:,.0f} VNĐ".replace(",", ".")

def format_vn_time(dt: datetime):
    """Format: 14:30 - Ngày 25/12/2026"""
    return dt.strftime("%H:%M - Ngày %d/%m/%Y")

def get_elo_display(elo_str: str):
    try:
        e_type, e_min, e_max = elo_str.split(":")
        mapping = {
            "all": "Tự do", 
            "range": f"Từ {e_min} đến {e_max}", 
            "under": f"≤ {e_min}", 
            "above": f"≥ {e_min}"
        }
        return mapping.get(e_type, "Không xác định")
    except: return "Không xác định"

async def disable_match_buttons(match_id, channel_id):
    session = SessionLocal()
    try:
        match = session.query(Match).filter_by(match_id=match_id).first()
        channel = bot.get_channel(int(channel_id))
        if not match or not channel: return

        if match.registration_msg_id:
            try:
                reg_msg = await channel.fetch_message(int(match.registration_msg_id))
                view = MatchView(match.match_id, SessionLocal)
                view.disable_all()
                await reg_msg.edit(view=view)
            except: pass

        if match.checkin_msg_id:
            try:
                c_msg = await channel.fetch_message(int(match.checkin_msg_id))
                await c_msg.edit(view=None)
            except: pass
    finally:
        session.close()

# ---------------- UI COMPONENTS ----------------

from utils import calculate_elo_fixed_gap

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
        """
        winner_side: 'Team 1' hoặc 'Team 2'
        """
        session = self.Session()
        try:
            match = session.query(Match).filter_by(match_id=self.match_id).first()
            if not match or match.status != "playing":
                return await interaction.response.send_message("Trận đấu không khả dụng hoặc đã kết thúc!", ephemeral=True)

            # Lấy danh sách Player object
            t1_players = session.query(Player).filter(Player.discord_id.in_(match.team1)).all()
            t2_players = session.query(Player).filter(Player.discord_id.in_(match.team2)).all()

            # Chuẩn bị list Elo để tính toán
            t1_elos = [p.elo for p in t1_players]
            t2_elos = [p.elo for p in t2_players]

            # Tính toán Elo theo hàm có sẵn
            # winner='a' tương ứng Team 1, 'b' tương ứng Team 2
            calc_res = calculate_elo_fixed_gap(t1_elos, t2_elos, winner='a' if winner_side == "Team 1" else 'b')
            
            # Lấy giá trị số (ví dụ: 32) từ chuỗi "+32" hoặc "-32"
            points = int(calc_res["win_team_points"].replace("+", ""))        

            # Cập nhật Database cho từng người chơi
            # Xử lý Team 1
            for p in t1_players:
                if winner_side == "Team 1":
                    p.elo += points
                    p.wins += 1
                    p.streak = p.streak + 1 if p.streak >= 0 else 1
                else:
                    p.elo -= points
                    p.losses += 1
                    p.streak = p.streak - 1 if p.streak <= 0 else -1

            # Xử lý Team 2
            for p in t2_players:
                if winner_side == "Team 2":
                    p.elo += points
                    p.wins += 1
                    p.streak = p.streak + 1 if p.streak >= 0 else 1
                else:
                    p.elo -= points
                    p.losses += 1
                    p.streak = p.streak - 1 if p.streak <= 0 else -1

            # Cập nhật trạng thái trận đấu
            match.status = "finished"
            match.result = f"{winner_side} thắng"
            match.elo_bonus = str(points)
            # Lưu thông tin thắng/thua vào field status hoặc in ra kết quả
            session.commit()

            # Tạo Embed hiển thị kết quả chi tiết +/- Elo
            embed = discord.Embed(title="🏆 KẾT QUẢ SHOWMATCH", color=discord.Color.gold())
            
            win_label = "🔵 Team 1" if winner_side == "Team 1" else "🔴 Team 2"
            lose_label = "🔴 Team 2" if winner_side == "Team 1" else "🔵 Team 1"
            
            embed.description = (
                f"## Trận đấu `#{str(match.match_id)[:8]}` kết thúc!\n\n"
                f"🏆 **Đội thắng:** {win_label}\n"
                f"📈 **Biến thiên Elo:**\n"
                f"- Đội thắng: `{calc_res['win_team_points']}` Elo\n"
                f"- Đội thua: `{calc_res['lose_team_points']}` Elo"
            )
            
            await interaction.response.send_message(embed=embed)
            
            # Xóa các nút điều khiển sau khi đã xong
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
        session = self.Session()
        try:
            match = session.query(Match).filter_by(match_id=self.match_id).first()
            if match:
                # Cập nhật trạng thái Hủy vào DB
                match.status = "cancelled"
                match.result = "Hủy"
                match.elo_bonus = "0"
                
                await cancel_match_logic(match, interaction.channel, "Admin chủ động hủy trận.")
                session.commit()
                
                await interaction.response.send_message("✅ Trận đấu đã được hủy và lưu trạng thái vào hệ thống.", ephemeral=True)
                self.stop()
        finally:
            session.close()

class CheckInView(discord.ui.View):
    def __init__(self, match_id, session_factory):
        super().__init__(timeout=None)
        self.match_id = match_id
        self.Session = session_factory

    @discord.ui.button(label="Check-in ✅", style=discord.ButtonStyle.primary)
    async def checkin(self, interaction: discord.Interaction, button: discord.ui.Button):
        session = self.Session()
        try:
            match = session.query(Match).filter_by(match_id=self.match_id).first()
            if not match or match.status != "checkin":
                return await interaction.response.send_message("Cổng check-in đã đóng!", ephemeral=True)

            uid = str(interaction.user.id)
            if uid not in match.participants:
                return await interaction.response.send_message("Bạn chưa đăng ký tham gia trận này!", ephemeral=True)

            checked = list(match.checked_in)
            if uid in checked:
                return await interaction.response.send_message("Bạn đã check-in rồi!", ephemeral=True)

            # Cập nhật danh sách check-in
            checked.append(uid)
            match.checked_in = checked
            session.commit()

            total_slots = match.team_size * 2
            
            # # Kiểm tra nếu đã đủ người check-in
            # if len(checked) == total_slots:
            #     # 1. Disable nút check-in
            #     self.clear_items() 
            #     await interaction.response.edit_message(view=self)
                
            #     # 2. Gọi hàm chia team từ utils.py
            #     # Lưu ý: Cần import thêm discord và Player, Match nếu utils chưa có đủ context
            #     from utils import auto_split_teams
            #     team_embed = await auto_split_teams(match.match_id, session)
                
            #     if team_embed:
            #         # 3. Tag tất cả người chơi đã check-in
            #         mentions = " ".join([f"<@{u}>" for u in checked])
            #         divide_team_msg = await interaction.followup.send(content=f"📊 **Đã đủ người! Chia team cho trận `#{str(match.match_id)[:8]}`:**\n{mentions}", embed=team_embed)
            #         match.team_msg_id = divide_team_msg.id
            #         # 4. Cập nhật trạng thái trận đấu
            #         match.status = "playing"
            #         session.commit()
            # else:
            # Nếu chưa đủ, chỉ cập nhật danh sách hiển thị như cũ
            players = session.query(Player).filter(Player.discord_id.in_(checked)).all()
            p_map = {p.discord_id: p.in_game_name for p in players}
            checkin_list_str = "\n".join([f"- {p_map.get(u, 'Unknown')} ✅" for u in checked])

            embed = interaction.message.embeds[0]
            embed.set_field_at(0, name=f"Danh sách đã check-in ({len(checked)}/{total_slots})", value=checkin_list_str, inline=False)
            await interaction.response.edit_message(embed=embed)
                
        finally:
            session.close()

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
            if not match or match.status not in ["waiting", "checkin"]:
                return await interaction.response.send_message("Trận đấu đã khóa!", ephemeral=True)

            player = session.query(Player).filter_by(discord_id=str(interaction.user.id)).first()
            if not player:
                return await interaction.response.send_message("Vui lòng được xét elo trước, hỏi admin nếu chưa có!", ephemeral=True)

            # Check Elo
            req = match.elo_requirement.split(":")
            e_type, e_min, e_max = req[0], int(req[1]), int(req[2])
            if e_type == "range" and not (e_min <= player.elo <= e_max):
                return await interaction.response.send_message(f"Elo {player.elo} không hợp lệ ({e_min}-{e_max})", ephemeral=True)
            elif e_type == "under" and player.elo > e_min:
                return await interaction.response.send_message(f"Elo {player.elo} vượt mức tối đa {e_min}", ephemeral=True)
            elif e_type == "above" and player.elo < e_min:
                return await interaction.response.send_message(f"Elo {player.elo} không đạt tối thiểu {e_min}", ephemeral=True)

            parts = list(match.participants)
            total_slots = match.team_size * 2
            
            if str(interaction.user.id) in parts:
                return await interaction.response.send_message("Bạn đã đăng ký rồi!", ephemeral=True)
            # if len(parts) >= total_slots:
            #     return await interaction.response.send_message("Trận đấu đã đầy!", ephemeral=True)

            parts.append(str(interaction.user.id))
            match.participants = parts
            
            # Logic: Nếu đủ người và đang trong giai đoạn 30p trước trận, mở check-in ngay
            should_start_checkin = False
            time_diff = match.match_time - datetime.now()
            if len(parts) == total_slots and time_diff <= timedelta(minutes=30) and match.status == "waiting":
                should_start_checkin = True

            session.commit()

            embed = interaction.message.embeds[0]
            mentions = "\n".join([f"<@{u}>" for u in parts]) if parts else "Chưa có ai"
            embed.set_field_at(0, name=f"Người tham gia ({len(parts)})", value=mentions, inline=False)
            await interaction.response.edit_message(embed=embed)

            if should_start_checkin:
                channel = bot.get_channel(NOTIFY_CHANNEL_ID)
                await channel.send(f"✅ Trận `#{str(match.match_id)[:8]}` đã đủ người đăng ký bổ sung!")
                await start_checkin_phase(match, channel)
                session.commit()

        finally:
            session.close()

    @discord.ui.button(label="Hủy đăng ký ❌", style=discord.ButtonStyle.danger)
    async def leave(self, interaction: discord.Interaction, button: discord.ui.Button):
        session = self.Session()
        try:
            match = session.query(Match).filter_by(match_id=self.match_id).first()
            if not match or match.status != "waiting":
                return await interaction.response.send_message("Không thể hủy khi đã đến giờ check-in!", ephemeral=True)

            parts = list(match.participants)
            uid = str(interaction.user.id)
            if uid not in parts:
                return await interaction.response.send_message("Bạn chưa đăng ký!", ephemeral=True)

            parts.remove(uid)
            match.participants = parts
            session.commit()

            embed = interaction.message.embeds[0]
            mentions = "\n".join([f"<@{u}>" for u in parts]) if parts else "Chưa có ai"
            embed.set_field_at(0, name=f"Người tham gia ({len(parts)}/{match.team_size*2})", value=mentions, inline=False)
            await interaction.response.edit_message(embed=embed)
        finally:
            session.close()

# ---------------- SLASH COMMANDS ----------------

async def time_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> list[app_commands.Choice[str]]:
    suggestions = []
    now = datetime.now().replace(second=0, microsecond=0)
    
    # Gợi ý 10 mốc thời gian tiếp theo, mỗi mốc cách nhau 30 phút
    for i in range(1, 11):
        suggested_time = now + timedelta(minutes=i * 30)
        time_str = suggested_time.strftime("%Y-%m-%d %H:%M")
        
        # Chỉ thêm vào danh sách nếu khớp với những gì người dùng đang gõ
        if current.lower() in time_str.lower():
            suggestions.append(app_commands.Choice(name=time_str, value=time_str))
            
    return suggestions

@bot.tree.command(name="create_match", description="Tạo trận đấu mới", guild=guild_obj)
@app_commands.choices(elo_type=[
    app_commands.Choice(name="Tất cả", value="all"),
    app_commands.Choice(name="Khoảng (Min-Max)", value="range"),
    app_commands.Choice(name="Dưới hoặc bằng (<= Max)", value="under"),
    app_commands.Choice(name="Trên hoặc bằng (>= Min)", value="above")
])
@app_commands.describe(match_time="Chọn hoặc nhập giờ (Định dạng: YYYY-MM-DD HH:MM) ví dụ 2026-03-23 20:00")
@app_commands.autocomplete(match_time=time_autocomplete)
@is_register_channel()
async def create_match(interaction: discord.Interaction, match_time: str, team_size: int, prize: int, elo_type: str, elo_min: int = 0, elo_max: int = 9999):
    session = SessionLocal()
    if not interaction.user.guild_permissions.administrator:
        return await interaction.response.send_message("Chỉ Admin mới có quyền!", ephemeral=True)
    try:
        try:
            dt = datetime.strptime(match_time, "%Y-%m-%d %H:%M")
        except:
            return await interaction.response.send_message("Định dạng: YYYY-MM-DD HH:MM", ephemeral=True)

        m_id = uuid.uuid4()
        req_str = f"{elo_type}:{elo_min}:{elo_max}"
        
        embed = discord.Embed(title="⚔️ THÔNG BÁO SHOWMATCH ", color=discord.Color.blue())
        # Tăng cỡ chữ bằng Markdown header ##
        embed.description = (
            f"## ⏰ Giờ: {format_vn_time(dt)}\n"
            f"## 👥 Size: {team_size}vs{team_size}\n"
            f"**Tiền thưởng:** `{format_vnd(prize)}`\n"
            f"**Điều kiện Elo:** `{get_elo_display(req_str)}`"
        )
        embed.add_field(name=f"Người tham gia (0)", value="Chưa có ai", inline=False)

        view = MatchView(m_id, SessionLocal)
        await interaction.response.send_message(content="@everyone", embed=embed, view=view)
        
        msg = await interaction.original_response()
        new_m = Match(
            match_id=m_id, match_time=dt, team_size=team_size, prize=prize, 
            elo_requirement=req_str, registration_msg_id=str(msg.id), status="waiting",
            created_by=interaction.user.id
        )
        session.add(new_m)
        session.commit()
    finally:
        session.close()

@bot.tree.command(name="add", description="Xét elo tài khoản", guild=guild_obj)
async def add_player(interaction: discord.Interaction, member: discord.Member, ingame_name: str, elo: int = 1000):
    if not interaction.user.guild_permissions.administrator:
        return await interaction.response.send_message("Chỉ Admin mới có quyền!", ephemeral=True)
    session = SessionLocal()
    try:
        player = session.query(Player).filter_by(discord_id=str(member.id)).first()
        if player:
            player.in_game_name, player.elo = ingame_name, elo
            msg = f"✅ Cập nhật: <@{member.id}> (IGN: `{ingame_name}` - Elo: `{elo}`)"
        else:
            new_player = Player(discord_id=str(member.id), in_game_name=ingame_name, elo=elo)
            session.add(new_player)
            msg = f"✨ Đăng ký mới: <@{member.id}> (IGN: `{ingame_name}` - Elo: `{elo}`)"
        session.commit()
        await interaction.response.send_message(msg)
    finally:
        session.close()

# ---------------- TASKS & LOGIC ----------------

@tasks.loop(minutes=1)
async def match_scheduler():
    session = SessionLocal()
    try:
        now = datetime.now()
        channel_notify = bot.get_channel(NOTIFY_CHANNEL_ID)
        channel_register = bot.get_channel(REGISTER_CHANNEL_ID)
        if not channel_notify or not channel_register: return

        active_matches = session.query(Match).filter(Match.status.in_(["waiting", "checkin"])).all()

        for m in active_matches:
            total_slots = m.team_size * 2
            time_diff = m.match_time - now

            # 1. Mốc T-30 phút: Kiểm tra quân số
            if timedelta(minutes=29) < time_diff <= timedelta(minutes=30):
                if len(m.participants) >= total_slots:
                    if m.status == "waiting":
                        await start_checkin_phase(m, channel_notify)
                else:
                    # Gửi thông báo bổ sung (Reply tin nhắn đăng ký)
                    missing = total_slots - len(m.participants)
                    try:
                        end_time = now + timedelta(minutes=15)
                        reg_msg = await channel_register.fetch_message(int(m.registration_msg_id))
                        await reg_msg.reply(
                            f"📢 **THÔNG BÁO BỔ SUNG** @everyone\n"
                            f"Trận đấu lúc **{format_vn_time(m.match_time)}** hiện đang thiếu **{missing}** người.\n"
                            f"Các bạn vui lòng đăng ký bổ sung trong 15 phút tới để trận đấu được diễn ra!\n"
                            f"Kết thúc đăng ký bổ sung lúc **{format_vn_time(end_time)}**"
                        )
                    except Exception as e:
                        print(f"Lỗi khi fetch hoặc reply message: {e}")
                        print("regis id", int(m.registration_msg_id))
                        await channel_register.send(f"📢 Trận `#{str(m.match_id)[:8]}` thiếu {missing} người!")

            # 2. Mốc T-15 phút: Hủy nếu vẫn thiếu
            elif timedelta(minutes=14) < time_diff <= timedelta(minutes=15):
                if len(m.participants) < total_slots:
                    await cancel_match_logic(m, channel_register, "Không đủ người tham gia sau thời gian gia hạn bổ sung.")
                elif m.status == "waiting":
                    await start_checkin_phase(m, channel_notify)
            # 3. Mốc 7 phút: chia team và disable checkin đi:
            elif timedelta(minutes=6) < time_diff <= timedelta(minutes=7): 
                team_embed = await auto_split_teams(match.match_id, session)
                
                if team_embed:
                    # 3.1 Tag tất cả người chơi đã checkin được chọn trong danh sách chia team dc tối ưu nhất
                    mentions = " ".join([f"<@{u}>" for u in checked])
                    divide_team_msg = await interaction.followup.send(content=f"📊 **Đã cân bằng elo tốt nhất trong số danh sách người đã check-in\nChia team cho trận `#{str(match.match_id)[:8]}`:**\n{mentions}", embed=team_embed)
                    match.team_msg_id = divide_team_msg.id

                    c_msg = await channel_notify.fetch_message(int(match.checkin_msg_id))
                    v = View.from_message(c_msg)
                    for item in v.children:
                        item.disabled = True

                    # Chỉ edit lại view, giữ nguyên embed cũ
                    await c_msg.edit(view=v)

                    # 3.2 Cập nhật trạng thái trận đấu
                    match.status = "playing"
                    session.commit()

            # 4. Mốc T-0: Kiểm tra check-in
            elif now >= m.match_time and m.status == "checkin":
                if len(m.checked_in) < total_slots:
                    await cancel_match_logic(m, channel_notify, "Không đủ người check-in đúng giờ thi đấu.")
                else:
                    m.status = "playing"
                    embed = discord.Embed(title="🎮 TRẬN ĐẤU BẮT ĐẦU", description="Trận đấu đang diễn ra. Admin vui lòng cập nhật kết quả khi kết thúc.", color=discord.Color.green())
                    team_msg = await channel_notify.fetch_message(int(m.team_msg_id))
                    team_msg.reply(embed=embed, view=AdminControlView(m.match_id, SessionLocal))
        
        session.commit()
    except Exception as e:
        print(f"Task Error: {e}")
        session.rollback()
    finally:
        session.close()

async def start_checkin_phase(match, channel):
    match.status = "checkin"
    tags = " ".join([f"<@{u}>" for u in match.participants])
    embed = discord.Embed(title="🔔 CHECK-IN SHOWMATCH", color=discord.Color.gold())
    embed.description = (
        f"## ⚔️ Trận: `#{str(match.match_id)[:8]}`\n"
        f"**Giờ thi đấu:** {format_vn_time(match.match_time)}\n"
        f"**Quy mô:** {match.team_size}vs{match.team_size}"
    )
    embed.add_field(name="Danh sách đã check-in (0)", value="Chưa có ai", inline=False)
    
    c_msg = await channel.send(content=tags, embed=embed, view=CheckInView(match.match_id, SessionLocal))
    match.checkin_msg_id = str(c_msg.id)
    reg_msg = await channel.fetch_message(int(match.registration_msg_id))
    v = MatchView(match.match_id, SessionLocal)
    v.disable_all()
    await reg_msg.edit(view=v)

async def cancel_match_logic(match, channel, reason):
    match.status = "cancelled"
    vn_time = format_vn_time(match.match_time)
    
    # Vô hiệu hóa nút tin nhắn đăng ký
    try:
        reg_msg = await channel.fetch_message(int(match.registration_msg_id))
        v = MatchView(match.match_id, SessionLocal)
        v.disable_all()
        await reg_msg.edit(view=v)
    except: pass

    # Cập nhật tin nhắn check-in thành Hủy
    if match.checkin_msg_id:
        try:
            c_msg = await channel.fetch_message(int(match.checkin_msg_id))
            v = View.from_message(c_msg)
            for item in v.children:
                item.disabled = True

            # Chỉ edit lại view, giữ nguyên embed cũ
            await c_msg.edit(view=v)
        except: pass

    # Gửi thông báo hủy chung
    cancel_embed = discord.Embed(
        title="🚫 THÔNG BÁO HỦY TRẬN",
        description=f"Trận đấu `#{str(match.match_id)[:8]}` dự kiến lúc **{vn_time}** đã bị hủy.\n**Lý do:** {reason}",
        color=discord.Color.red()
    )
    reg_msg = await channel.fetch_message(int(match.registration_msg_id))
    await reg_msg.reply(embed=cancel_embed)


#-------------------- Leatherboard --------------------------

MEDAL = {1: "🥇", 2: "🥈", 3: "🥉"}
SWORD = "⚔️ "

ANSI = {
    1: "\u001b[1;33m", # Vàng đậm (Top 1)
    2: "\u001b[1;37m", # Trắng sáng (Top 2)
    3: "\u001b[0;33m", # Nâu/Vàng nhạt (Top 3)
    "n": "\u001b[0;37m", # Trắng xám (Thường)
    "h": "\u001b[1;34m", # Xanh dương sáng (Header)
    "s": "\u001b[0;30m", # Xám đen (Separator)
    "r": "\u001b[0m",    # Reset
}

def _rpad(v, n):
    s = str(v)
    return (s[:n-1] + "…") if len(s) > n else s.ljust(n)

def _lpad(v, n):
    s = str(v)
    return (s[:n-1] + "…") if len(s) > n else s.rjust(n)

def get_streak_info(streak: int):
    if streak >= 10: return f"{streak}!", "🔥"
    if streak >= 5:  return f"{streak}*", "⚡"
    if streak > 0:   return f"{streak} ", "✦"
    return " -- ", "  "

class LeaderboardView(discord.ui.View):
    def __init__(self, session_factory, player_model, current_page, max_page):
        super().__init__(timeout=60)
        self.SessionFactory = session_factory
        self.Player = player_model
        self.current_page = current_page
        self.max_page = max_page
        self.update_button_states()

    def update_button_states(self):
        self.prev_button.disabled = (self.current_page <= 1)
        self.next_button.disabled = (self.current_page >= self.max_page)

    def format_leaderboard_text(self, players, start_rank: int):
        A = ANSI
        lines = []

        # Header: Cấu trúc rộng rãi cho màn hình PC
        # Rank(5) | Tên(22) | Elo(8) | W(5) | L(5) | WR(8) | Streak(8)
        header = (
            f"   {A['h']}{_rpad('RANK # TÊN NGƯỜI CHƠI', 27)} "
            f"{_lpad('ELO', 8)} {_lpad('W', 5)} {_lpad('L', 5)} "
            f"{_lpad('W.RATE', 9)} {_lpad('STREAK', 8)}{A['r']}"
        )
        sep = f"   {A['s']}{'━' * 68}{A['r']}"
        
        lines.append(header)
        lines.append(sep)

        for i, p in enumerate(players):
            abs_rank = start_rank + i
            total = p.wins + p.losses
            wr = f"{(p.wins/total*100):.1f}%" if total > 0 else "0.0%"
            
            # Lấy icon huy chương nằm NGOÀI khối ANSI để giữ màu gốc của Emoji
            medal_icon = MEDAL.get(abs_rank, SWORD)
            
            # Lấy màu dựa theo rank
            color = A.get(abs_rank if abs_rank <= 3 else "n")
            
            # Xử lý streak
            stk_val, stk_icon = get_streak_info(p.streak)
            
            # Tạo dòng nội dung
            rank_name = f"#{abs_rank:<2} {p.in_game_name}"
            row = (
                f"{color}{_rpad(rank_name, 27)} "
                f"{_lpad(p.elo, 8)} {_lpad(p.wins, 5)} {_lpad(p.losses, 5)} "
                f"{_lpad(wr, 9)} {_lpad(stk_val, 8)}{A['r']}"
            )
            
            # Ghép Icon + Dòng ANSI + Icon Streak cuối
            lines.append(f"{medal_icon} {row} {stk_icon}")

        return "```ansi\n" + "\n".join(lines) + "\n```"

    async def update_view(self, interaction: discord.Interaction):
        session = self.SessionFactory()
        try:
            offset = (self.current_page - 1) * 10
            players = session.query(self.Player).order_by(self.Player.elo.desc()).offset(offset).limit(10).all()
            
            board_text = self.format_leaderboard_text(players, offset + 1)
            
            title = f"## 🏆 BẢNG XẾP HẠNG CAO THỦ - TRANG {self.current_page}/{self.max_page}"
            footer = f"> *Cập nhật lúc: {datetime.now().strftime('%H:%M:%S')} • Server: PC Optimized*"
            
            content = f"{title}\n{footer}\n{board_text}"
            
            self.update_button_states()
            await interaction.response.edit_message(content=content, view=self)
        finally:
            session.close()

    @discord.ui.button(label="◀️ TRANG TRƯỚC", style=discord.ButtonStyle.gray)
    async def prev_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.current_page -= 1
        await self.update_view(interaction)

    @discord.ui.button(label="TRANG SAU ▶️", style=discord.ButtonStyle.gray)
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.current_page += 1
        await self.update_view(interaction)

# --- SLASH COMMAND CHÍNH ---
@bot.tree.command(name="leaderboard", description="Xem bảng xếp hạng cao thủ", guild=guild_obj)
async def leaderboard(interaction: discord.Interaction):
    session = SessionLocal() # Thay bằng session_factory của bạn
    try:
        total_players = session.query(Player).count() # Thay Player bằng model của bạn
        if total_players == 0:
            return await interaction.response.send_message("❌ Chưa có dữ liệu người chơi!", ephemeral=True)
        
        max_page = (total_players + 9) // 10
        players = session.query(Player).order_by(Player.elo.desc()).limit(10).all()
        
        # Khởi tạo View
        view = LeaderboardView(SessionLocal, Player, 1, max_page)
        board_text = view.format_leaderboard_text(players, 1)
        
        title = f"## 🏆 BẢNG XẾP HẠNG CAO THỦ - TRANG 1/{max_page}"
        footer = f"> *Cập nhật lúc: {datetime.now().strftime('%H:%M:%S')}*"
        
        await interaction.response.send_message(
            content=f"{title}\n{footer}\n{board_text}", 
            view=view
        )
    finally:
        session.close()

@bot.tree.command(name="me", description="Xem thông số cá nhân", guild=guild_obj)
async def my_stats(interaction: discord.Interaction):
    session = SessionLocal()
    try:
        uid = str(interaction.user.id)
        player = session.query(Player).filter_by(discord_id=uid).first()
        
        if not player:
            return await interaction.response.send_message("❌ Bạn chưa có dữ liệu trên hệ thống!", ephemeral=True)
            
        rank = session.query(Player).filter(Player.elo > player.elo).count() + 1
        total = player.wins + player.losses
        wr = f"{(player.wins/total*100):.1f}%" if total > 0 else "0.0%"
        
        # Bảng rộng cho PC
        header = f"{'Hạng':<8} {'Tên người chơi':<25} {'Elo':<10} {'Thắng':<10} {'Thua':<10} {'Chuỗi':<10} {'Winrate'}"
        line = "-" * len(header)
        
        msg = "```\n"
        msg += f"{header}\n"
        msg += f"{line}\n"
        msg += f"{rank:<8} {player.in_game_name[:22]:<25} {player.elo:<10} {player.wins:<10} {player.losses:<10} {player.streak:<10} {wr}\n"
        msg += "```"
        
        await interaction.response.send_message(content=f"📊 **Thông số của <@{uid}>:**\n{msg}", ephemeral=True)
    finally:
        session.close()


@bot.event
async def on_ready():
    if not match_scheduler.is_running():
        match_scheduler.start()
    await bot.tree.sync(guild=guild_obj)
    print(f"Logged in as {bot.user}")

bot.run(TOKEN)
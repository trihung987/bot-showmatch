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
from utils import auto_split_teams, balance_teams

# ---------------- CONFIG ----------------
load_dotenv()
TOKEN = os.getenv("TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")
GUILD_ID = 1485525920696635445 
NOTIFY_CHANNEL_ID = 1485525921351073904 

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(bind=engine)
Base.metadata.create_all(engine)

intents = discord.Intents.default()
intents.members = True 
bot = commands.Bot(command_prefix='!', intents=intents)
guild_obj = discord.Object(id=GUILD_ID)

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

    @discord.ui.button(label="Team 1 Win 🏆", style=discord.ButtonStyle.success)
    async def team1_win(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.stop()
        await interaction.response.send_message("Xác nhận Team 1 thắng. Hệ thống đang cập nhật Elo...")

    @discord.ui.button(label="Team 2 Win 🏆", style=discord.ButtonStyle.success)
    async def team2_win(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.stop()
        await interaction.response.send_message("Xác nhận Team 2 thắng. Hệ thống đang cập nhật Elo...")

    @discord.ui.button(label="Hủy Trận ❌", style=discord.ButtonStyle.danger)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        session = self.Session()
        m = session.query(Match).filter_by(match_id=self.match_id).first()
        if m: 
            await cancel_match_logic(m, interaction.channel, "Admin chủ động hủy trận.")
            session.commit()
        session.close()
        self.stop()

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
            
            # Kiểm tra nếu đã đủ người check-in
            if len(checked) == total_slots:
                # 1. Disable nút check-in
                self.clear_items() 
                await interaction.response.edit_message(view=self)
                
                # 2. Gọi hàm chia team từ utils.py
                # Lưu ý: Cần import thêm discord và Player, Match nếu utils chưa có đủ context
                from utils import auto_split_teams
                team_embed = await auto_split_teams(match.match_id, session)
                
                if team_embed:
                    # 3. Tag tất cả người chơi đã check-in
                    mentions = " ".join([f"<@{u}>" for u in checked])
                    divide_team_msg = await interaction.followup.send(content=f"📊 **Đã đủ người! Chia team cho trận `#{str(match.match_id)[:8]}`:**\n{mentions}", embed=team_embed)
                    match.team_msg_id = divide_team_msg.id
                    # 4. Cập nhật trạng thái trận đấu
                    match.status = "playing"
                    session.commit()
            else:
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
                return await interaction.response.send_message("Vui lòng đăng ký bằng lệnh `/add`!", ephemeral=True)

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
            if len(parts) >= total_slots:
                return await interaction.response.send_message("Trận đấu đã đầy!", ephemeral=True)

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
            embed.set_field_at(0, name=f"Người tham gia ({len(parts)}/{total_slots})", value=mentions, inline=False)
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

@bot.tree.command(name="create_match", description="Tạo trận đấu mới", guild=guild_obj)
@app_commands.choices(elo_type=[
    app_commands.Choice(name="Tất cả", value="all"),
    app_commands.Choice(name="Khoảng (Min-Max)", value="range"),
    app_commands.Choice(name="Dưới hoặc bằng (<= Max)", value="under"),
    app_commands.Choice(name="Trên hoặc bằng (>= Min)", value="above")
])
async def create_match(interaction: discord.Interaction, match_time: str, team_size: int, prize: int, elo_type: str, elo_min: int = 0, elo_max: int = 9999):
    session = SessionLocal()
    try:
        try:
            dt = datetime.strptime(match_time, "%Y-%m-%d %H:%M")
        except:
            return await interaction.response.send_message("Định dạng: YYYY-MM-DD HH:MM", ephemeral=True)

        m_id = uuid.uuid4()
        req_str = f"{elo_type}:{elo_min}:{elo_max}"
        
        embed = discord.Embed(title="⚔️ THÔNG BÁO SHOWMATCH", color=discord.Color.blue())
        # Tăng cỡ chữ bằng Markdown header ##
        embed.description = (
            f"## ⏰ Giờ: {format_vn_time(dt)}\n"
            f"## 👥 Size: {team_size}vs{team_size}\n"
            f"**Tiền thưởng:** `{format_vnd(prize)}`\n"
            f"**Điều kiện Elo:** `{get_elo_display(req_str)}`"
        )
        embed.add_field(name=f"Người tham gia (0/{team_size*2})", value="Chưa có ai", inline=False)

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

@bot.tree.command(name="add", description="Đăng ký tài khoản", guild=guild_obj)
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
        channel = bot.get_channel(NOTIFY_CHANNEL_ID)
        if not channel: return

        active_matches = session.query(Match).filter(Match.status.in_(["waiting", "checkin"])).all()

        for m in active_matches:
            total_slots = m.team_size * 2
            time_diff = m.match_time - now

            # 1. Mốc T-30 phút: Kiểm tra quân số
            if timedelta(minutes=29) < time_diff <= timedelta(minutes=30):
                if len(m.participants) >= total_slots:
                    if m.status == "waiting":
                        await start_checkin_phase(m, channel)
                else:
                    # Gửi thông báo bổ sung (Reply tin nhắn đăng ký)
                    missing = total_slots - len(m.participants)
                    try:
                        end_time = now + timedelta(minutes=15)
                        reg_msg = await channel.fetch_message(int(m.registration_msg_id))
                        await reg_msg.reply(
                            f"📢 **THÔNG BÁO BỔ SUNG** @everyone\n"
                            f"Trận đấu lúc **{format_vn_time(m.match_time)}** hiện đang thiếu **{missing}** người.\n"
                            f"Các bạn vui lòng đăng ký bổ sung trong 15 phút tới để trận đấu được diễn ra!"
                            f"Kết thúc đăng ký bổ sung lúc **{format_vn_time(m.match_time)}**"
                        )
                    except:
                        await channel.send(f"📢 Trận `#{str(m.match_id)[:8]}` thiếu {missing} người!")

            # 2. Mốc T-15 phút: Hủy nếu vẫn thiếu
            elif timedelta(minutes=14) < time_diff <= timedelta(minutes=15):
                if len(m.participants) < total_slots:
                    await cancel_match_logic(m, channel, "Không đủ người tham gia sau thời gian gia hạn bổ sung.")
                elif m.status == "waiting":
                    await start_checkin_phase(m, channel)

            # 3. Mốc T-0: Kiểm tra check-in
            elif now >= m.match_time and m.status == "checkin":
                if len(m.checked_in) < total_slots:
                    await cancel_match_logic(m, channel, "Không đủ người check-in đúng giờ thi đấu.")
                else:
                    m.status = "playing"
                    embed = discord.Embed(title="🎮 TRẬN ĐẤU BẮT ĐẦU", description="Trận đấu đang diễn ra. Admin vui lòng cập nhật kết quả khi kết thúc.", color=discord.Color.green())
                    team_msg = await channel.fetch_message(int(m.team_msg_id))
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
            emb = c_msg.embeds[0]
            emb.title = "❌ TRẬN ĐẤU BỊ HỦY"
            emb.description = f"## Lý do: {reason}\n**Lịch dự kiến:** {vn_time}"
            emb.color = discord.Color.red()
            await c_msg.edit(content=None, embed=emb, view=None)
        except: pass

    # Gửi thông báo hủy chung
    cancel_embed = discord.Embed(
        title="🚫 THÔNG BÁO HỦY TRẬN",
        description=f"Trận đấu `#{str(match.match_id)[:8]}` dự kiến lúc **{vn_time}** đã bị hủy.\n**Lý do:** {reason}",
        color=discord.Color.red()
    )
    reg_msg = await channel.fetch_message(int(match.registration_msg_id))
    await rep_msg.reply(embed=cancel_embed)

@bot.event
async def on_ready():
    if not match_scheduler.is_running():
        match_scheduler.start()
    await bot.tree.sync(guild=guild_obj)
    print(f"Logged in as {bot.user}")

bot.run(TOKEN)
import os, sqlite3, uuid, asyncio
from datetime import datetime
from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse
import uvicorn, discord
from discord.ext import commands, tasks
from discord.ui import Button, View

DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN")
DB_NAME = "verification.db"

def init_db():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("CREATE TABLE IF NOT EXISTS verifications (token TEXT PRIMARY KEY, user_id TEXT, guild_id TEXT, email TEXT, status TEXT DEFAULT 'pending', ip_address TEXT, created_at TIMESTAMP)")
    conn.commit()
    conn.close()

def create_verification(user_id, guild_id):
    token = str(uuid.uuid4())
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("INSERT INTO verifications (token, user_id, guild_id, created_at) VALUES (?, ?, ?, ?)", (token, user_id, guild_id, datetime.now()))
    conn.commit()
    conn.close()
    return token

def get_verified():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT token, user_id, guild_id FROM verifications WHERE status='verified'")
    rows = c.fetchall()
    conn.close()
    return rows

def mark_done(token):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("UPDATE verifications SET status='processed' WHERE token=?", (token,))
    conn.commit()
    conn.close()

def get_logs(limit=10):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT user_id, email, ip_address, created_at FROM verifications WHERE status IN ('verified','processed') ORDER BY created_at DESC LIMIT ?", (limit,))
    rows = c.fetchall()
    conn.close()
    return rows

app = FastAPI()
HTML = """<!DOCTYPE html><html><head><title>Verify</title><style>body{font-family:sans-serif;text-align:center;margin-top:50px;background:#2c2f33;color:white}.box{background:#23272a;padding:30px;border-radius:10px;max-width:300px;margin:auto}input{width:100%;padding:10px;margin:10px 0;border-radius:5px;border:none;background:#40444b;color:white}button{background:#5865F2;color:white;border:none;padding:10px;border-radius:5px;cursor:pointer;width:100%}.error{color:#ed4245}</style></head><body><div class="box"><h2>Verify</h2><form action="/submit" method="POST"><input type="hidden" name="token" value="{{token}}"><input type="email" name="email" placeholder="Email" required><button>Verify</button></form></div></body></html>"""

@app.on_event("startup")
async def startup(): init_db()

@app.get("/verify/{token}")
async def get_verify(token: str):
    return HTMLResponse(content=HTML.replace("{{token}}", token))

@app.post("/submit")
async def post_submit(request: Request, token: str = Form(...), email: str = Form(...)):
    ip = request.client.host
    email = email.lower().strip()
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT 1 FROM verifications WHERE (email=? OR ip_address=?) AND status IN ('verified','processed')", (email, ip))
    if c.fetchone():
        conn.close()
        return HTMLResponse(content="<div class='box'><h2 class='error'>Already used</h2></div>", status_code=400)
    c.execute("UPDATE verifications SET status='verified',email=?,ip_address=? WHERE token=?", (email, ip, token))
    conn.commit()
    conn.close()
    return HTMLResponse(content="<div class='box'><h2>Success!</h2></div>")

intents = discord.Intents.default()
intents.members = True
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)
PUBLIC_URL = ""

class VerifyView(View):
    def __init__(self, url):
        super().__init__(timeout=None)
        self.add_item(Button(label="Verify now", style=discord.ButtonStyle.primary, url=url))
        why = Button(label="Why?", style=discord.ButtonStyle.secondary)
        async def why_cb(i): await i.response.send_message("Prevents bots!", ephemeral=True)
        why.callback = why_cb
        self.add_item(why)

@bot.event
async def on_ready():
    global PUBLIC_URL
    cs = os.environ.get("CODESPACE_NAME")
    dm = os.environ.get("GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN")
    PUBLIC_URL = f"https://{cs}-8080.{dm}" if cs and dm else "http://localhost:8080"
    print(f"[✅] Bot: {bot.user} | URL: {PUBLIC_URL}")
    await bot.tree.sync()
    check.start()

@bot.command(name="verify")
async def verify_cmd(ctx):
    t = create_verification(str(ctx.author.id), str(ctx.guild.id))
    link = f"{PUBLIC_URL}/verify/{t}"
    e = discord.Embed(title="🤖 Verification required", description=f"To gain access to **{ctx.guild.name}** you need to prove you are a human by completing verification. Click the button below to get started!", color=discord.Color.blue())
    await ctx.send(embed=e, view=VerifyView(link))

@bot.tree.command(name="verify", description="Send verification message")
async def verify_slash(i: discord.Interaction):
    t = create_verification(str(i.user.id), str(i.guild.id))
    link = f"{PUBLIC_URL}/verify/{t}"
    e = discord.Embed(title="🤖 Verification required", description=f"To gain access to **{i.guild.name}** you need to prove you are a human by completing verification. Click the button below to get started!", color=discord.Color.blue())
    await i.response.send_message(embed=e, view=VerifyView(link))

@bot.command(name="logs")
@commands.has_permissions(administrator=True)
async def logs_cmd(ctx):
    logs = get_logs(10)
    if not logs: return await ctx.send("No logs")
    txt = "\n".join([f"<@{u}> | `{e}` | `{ip}`" for u,e,ip,_ in logs])
    await ctx.send(embed=discord.Embed(title="Logs", description=txt, color=0x57F287))

@tasks.loop(seconds=5)
async def check():
    for t, uid, gid in get_verified():
        g = bot.get_guild(int(gid))
        if not g: continue
        m = g.get_member(int(uid))
        if not m: continue
        r = discord.utils.get(g.roles, name="Verified")
        if r and r not in m.roles:
            await m.add_roles(r)
            mark_done(t)

async def main():
    cfg = uvicorn.Config(app, host="0.0.0.0", port=8080)
    srv = uvicorn.Server(cfg)
    await asyncio.gather(srv.serve(), bot.start(DISCORD_TOKEN))

if __name__ == "__main__":
    asyncio.run(main())

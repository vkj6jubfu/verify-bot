import os
import sqlite3
import uuid
from datetime import datetime
import asyncio
from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse
import uvicorn
import discord
from discord.ext import commands, tasks

DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN")
VERIFIED_ROLE_NAME = "Verified" 
DB_NAME = "verification.db"

def init_db():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS verifications
                 (token TEXT PRIMARY KEY, user_id TEXT, guild_id TEXT, 
                  email TEXT, status TEXT DEFAULT 'pending', ip_address TEXT, user_agent TEXT, 
                  created_at TIMESTAMP)''')
    conn.commit()
    conn.close()

def create_verification(user_id, guild_id):
    token = str(uuid.uuid4())
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("INSERT INTO verifications (token, user_id, guild_id, created_at) VALUES (?, ?, ?, ?)",
              (token, user_id, guild_id, datetime.now()))
    conn.commit()
    conn.close()
    return token

def get_verified_tokens():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT token, user_id, guild_id, ip_address, user_agent FROM verifications WHERE status = 'verified'")
    rows = c.fetchall()
    conn.close()
    return rows

def mark_processed(token):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("UPDATE verifications SET status = 'processed' WHERE token = ?", (token,))
    conn.commit()
    conn.close()

def get_all_logs(limit=10):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT user_id, email, ip_address, created_at FROM verifications WHERE status IN ('verified', 'processed') ORDER BY created_at DESC LIMIT ?", (limit,))
    rows = c.fetchall()
    conn.close()
    return rows

app = FastAPI()

HTML_TEMPLATE = """<!DOCTYPE html>
<html><head><title>Verify</title><style>
body { font-family: sans-serif; text-align: center; margin-top: 50px; background: #2c2f33; color: white; }
.box { background: #23272a; padding: 30px; border-radius: 10px; width: 90%; max-width: 300px; margin: auto; }
input { width: 100%; padding: 10px; margin: 10px 0; border-radius: 5px; border: none; background: #40444b; color: white; box-sizing: border-box; }
button { background: #5865F2; color: white; border: none; padding: 10px 20px; border-radius: 5px; cursor: pointer; font-size: 16px; width: 100%; }
.error { color: #ed4245; font-weight: bold; }
</style></head><body><div class="box"><h2>Server Verification</h2><p>Enter your email to verify.</p>
<form action="/submit" method="POST"><input type="hidden" name="token" value="{{ token }}">
<input type="email" name="email" placeholder="your.email@example.com" required>
<button type="submit">Verify Me</button></form></div></body></html>"""

@app.on_event("startup")
async def startup_event():
    init_db()

@app.get("/verify/{token}", response_class=HTMLResponse)
async def verify_page(token: str):
    return HTMLResponse(content=HTML_TEMPLATE.replace("{{ token }}", token))

@app.post("/submit")
async def submit_verification(request: Request, token: str = Form(...), email: str = Form(...)):
    client_ip = request.client.host
    user_agent = request.headers.get("user-agent")
    email = email.lower().strip()
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT 1 FROM verifications WHERE email = ? AND status IN ('verified', 'processed')", (email,))
    if c.fetchone():
        conn.close()
        return HTMLResponse(content="<div class='box'><h2 class='error'>Failed</h2><p>Email already used.</p></div>", status_code=400)
    c.execute("SELECT 1 FROM verifications WHERE ip_address = ? AND status IN ('verified', 'processed')", (client_ip,))
    if c.fetchone():
        conn.close()
        return HTMLResponse(content="<div class='box'><h2 class='error'>Failed</h2><p>IP already used.</p></div>", status_code=400)
    c.execute("UPDATE verifications SET status = 'verified', email = ?, ip_address = ?, user_agent = ? WHERE token = ?",
              (email, client_ip, user_agent, token))
    conn.commit()
    conn.close()
    return HTMLResponse(content="<div class='box'><h2>Success!</h2><p>Verified.</p></div>")

intents = discord.Intents.default()
intents.members = True
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)
PUBLIC_URL = ""

@bot.event
async def on_ready():
    print(f"[✅] Bot online: {bot.user}")
    print(f"[✅] Web URL: {PUBLIC_URL}")
    check_web_verifications.start()

@bot.event
async def on_member_join(member: discord.Member):
    token = create_verification(str(member.id), str(member.guild.id))
    verify_link = f"{PUBLIC_URL}/verify/{token}"
    try:
        embed = discord.Embed(title=f"Welcome to {member.guild.name}!", description=f"Verify here:\n[**Click Here**]({verify_link})", color=discord.Color.blue())
        await member.send(embed=embed)
    except: pass

@bot.command(name="logs")
@commands.has_permissions(administrator=True)
async def view_logs(ctx):
    logs = get_all_logs(10)
    if not logs:
        await ctx.send("No logs yet.")
        return
    embed = discord.Embed(title="📜 Logs", color=discord.Color.green())
    text = ""
    for user_id, email, ip, ts in logs:
        text += f"**User:** <@{user_id}>\n**Email:** `{email}`\n**IP:** `{ip}`\n\n"
    embed.description = text
    await ctx.send(embed=embed)

@tasks.loop(seconds=5)
async def check_web_verifications():
    for token, user_id, guild_id, ip, ua in get_verified_tokens():
        guild = bot.get_guild(int(guild_id))
        if not guild: continue
        member = guild.get_member(int(user_id))
        if not member: continue
        role = discord.utils.get(guild.roles, name=VERIFIED_ROLE_NAME)
        if role and role not in member.roles:
            await member.add_roles(role)
            mark_processed(token)

async def main():
    global PUBLIC_URL
    codespace_name = os.environ.get("CODESPACE_NAME")
    domain = os.environ.get("GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN")
    if codespace_name and domain:
        PUBLIC_URL = f"https://{codespace_name}-8080.{domain}"
    else:
        PUBLIC_URL = "http://localhost:8080"
    config = uvicorn.Config(app, host="0.0.0.0", port=8080)
    server = uvicorn.Server(config)
    await asyncio.gather(server.serve(), bot.start(DISCORD_TOKEN))

if __name__ == "__main__":
    asyncio.run(main())

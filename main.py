import json
import uuid
import hashlib
import secrets
import sqlite3
import os
import logging
import base64
import asyncio
import random
import time
import hmac
from datetime import datetime, timedelta
from fastapi import FastAPI, WebSocket, HTTPException, Request, File, UploadFile, Form, Query, Response
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse, StreamingResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Dict, Optional, List
from PIL import Image
import io
import uvicorn

# ----- ОТКЛЮЧАЕМ ЛОГИРОВАНИЕ IP -----
logging.getLogger("uvicorn.access").handlers = []
logging.getLogger("uvicorn.access").propagate = False

app = FastAPI(title="Nyx - TG Desktop Layout")

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ----- ПАПКИ -----
os.makedirs("avatars", exist_ok=True)
os.makedirs("voice", exist_ok=True)
os.makedirs("video", exist_ok=True)
os.makedirs("stickers", exist_ok=True)
os.makedirs("photos", exist_ok=True)
os.makedirs("thumbnails", exist_ok=True)

app.mount("/avatars", StaticFiles(directory="avatars"), name="avatars")
app.mount("/voice", StaticFiles(directory="voice"), name="voice")
app.mount("/video", StaticFiles(directory="video"), name="video")
app.mount("/stickers", StaticFiles(directory="stickers"), name="stickers")
app.mount("/photos", StaticFiles(directory="photos"), name="photos")
app.mount("/thumbnails", StaticFiles(directory="thumbnails"), name="thumbnails")

# ----- БАЗА ДАННЫХ -----
conn = sqlite3.connect("nyx_tg_clone.db", check_same_thread=False)
c = conn.cursor()

# Пользователи
c.execute('''CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    username TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    salt TEXT NOT NULL,
    premium INTEGER DEFAULT 1,
    nft_collection TEXT DEFAULT '[]',
    profile_emoji TEXT DEFAULT '𖤐',
    avatar_path TEXT DEFAULT '',
    role TEXT DEFAULT 'user',
    device_id TEXT DEFAULT '',
    blocked_users TEXT DEFAULT '[]',
    last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    is_online INTEGER DEFAULT 0,
    bio TEXT DEFAULT '',
    phone TEXT DEFAULT '',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    stealth_mode INTEGER DEFAULT 1,
    vk_id TEXT DEFAULT '',
    ok_id TEXT DEFAULT ''
)''')

# Чаты
c.execute('''CREATE TABLE IF NOT EXISTS chats (
    id TEXT PRIMARY KEY,
    type TEXT DEFAULT 'private',
    title TEXT DEFAULT '',
    username TEXT UNIQUE DEFAULT '',
    description TEXT DEFAULT '',
    participants TEXT DEFAULT '[]',
    admin_username TEXT DEFAULT '',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)''')

# Сообщения
c.execute('''CREATE TABLE IF NOT EXISTS messages (
    id TEXT PRIMARY KEY,
    chat_id TEXT,
    sender_username TEXT,
    text TEXT,
    reply_to TEXT,
    reactions TEXT DEFAULT '{}',
    is_location INTEGER DEFAULT 0,
    latitude REAL DEFAULT NULL,
    longitude REAL DEFAULT NULL,
    is_voice INTEGER DEFAULT 0,
    voice_path TEXT DEFAULT '',
    is_video INTEGER DEFAULT 0,
    video_path TEXT DEFAULT '',
    is_photo INTEGER DEFAULT 0,
    photo_path TEXT DEFAULT '',
    sticker TEXT DEFAULT '',
    is_document INTEGER DEFAULT 0,
    document_name TEXT DEFAULT '',
    document_path TEXT DEFAULT '',
    edited INTEGER DEFAULT 0,
    deleted INTEGER DEFAULT 0,
    views INTEGER DEFAULT 0,
    forwards INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)''')

# Подарки (NFT)
c.execute('''CREATE TABLE IF NOT EXISTS gifts (
    id TEXT PRIMARY KEY,
    name TEXT,
    rarity TEXT,
    icon TEXT
)''')

# Черный список
c.execute('''CREATE TABLE IF NOT EXISTS blocks (
    id TEXT PRIMARY KEY,
    blocker TEXT,
    blocked TEXT,
    UNIQUE(blocker, blocked)
)''')

# Стикерпаки
c.execute('''CREATE TABLE IF NOT EXISTS sticker_packs (
    id TEXT PRIMARY KEY,
    name TEXT UNIQUE,
    emojis TEXT DEFAULT '[]'
)''')

# Подписки на каналы
c.execute('''CREATE TABLE IF NOT EXISTS channel_subscribers (
    id TEXT PRIMARY KEY,
    channel_id TEXT NOT NULL,
    username TEXT NOT NULL,
    joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(channel_id, username)
)''')

# Админы каналов
c.execute('''CREATE TABLE IF NOT EXISTS channel_admins (
    id TEXT PRIMARY KEY,
    channel_id TEXT NOT NULL,
    username TEXT NOT NULL,
    UNIQUE(channel_id, username)
)''')

# Истории
c.execute('''CREATE TABLE IF NOT EXISTS stories (
    id TEXT PRIMARY KEY,
    username TEXT NOT NULL,
    media_type TEXT DEFAULT 'photo',
    media_path TEXT NOT NULL,
    caption TEXT DEFAULT '',
    views INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMP DEFAULT (datetime('now', '+24 hours')),
    is_archived INTEGER DEFAULT 0,
    FOREIGN KEY (username) REFERENCES users(username)
)''')

# Просмотры историй
c.execute('''CREATE TABLE IF NOT EXISTS story_views (
    id TEXT PRIMARY KEY,
    story_id TEXT NOT NULL,
    viewer_username TEXT NOT NULL,
    viewed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(story_id, viewer_username),
    FOREIGN KEY (story_id) REFERENCES stories(id)
)''')

# Реакции на истории
c.execute('''CREATE TABLE IF NOT EXISTS story_reactions (
    id TEXT PRIMARY KEY,
    story_id TEXT NOT NULL,
    username TEXT NOT NULL,
    reaction TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(story_id, username),
    FOREIGN KEY (story_id) REFERENCES stories(id)
)''')

# Добавляем подарки
gifts_data = [
    ("fsociety_mask", "Маска Fsociety", "Legendary", "🎭"),
    ("cyber_skull", "Кибер-Череп", "Epic", "💀"),
    ("anon_glitch", "Глитч-Анон", "Rare", "👤"),
    ("matrix_rain", "Цифровой дождь", "Common", "🌧️"),
    ("neon_snake", "Неоновая змея", "Epic", "🐍"),
    ("dark_phoenix", "Темный феникс", "Legendary", "🔥"),
    ("fsociety_eye", "Глаз Fsociety", "Legendary", "👁️")
]
for g in gifts_data:
    c.execute("INSERT OR IGNORE INTO gifts (id, name, rarity, icon) VALUES (?, ?, ?, ?)",
              (str(uuid.uuid4()), g[0], g[1], g[2]))
conn.commit()

# ----- ТВОЙ АККАУНТ -----
def hash_password(password: str, salt: str = None) -> tuple:
    if not salt:
        salt = secrets.token_hex(16)
    h = hashlib.sha256((salt + password).encode()).hexdigest()
    return h, salt

ADMIN_USERNAME = "seconddurov"
ADMIN_PASSWORD = "020112"

c.execute("SELECT * FROM users WHERE username = ?", (ADMIN_USERNAME,))
if not c.fetchone():
    pwd_hash, salt = hash_password(ADMIN_PASSWORD)
    user_id = str(uuid.uuid4())
    c.execute("""INSERT INTO users
                 (id, username, password_hash, salt, premium, profile_emoji, role, nft_collection, bio, vk_id)
                 VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
              (user_id, ADMIN_USERNAME, pwd_hash, salt, 1, "👑", "admin",
               json.dumps(["Корона Даркнета"]), "Владыка подполья", str(random.randint(100000000, 999999999))))
    conn.commit()
    print(f"✅ Аккаунт {ADMIN_USERNAME} создан в БД (автовход ОТКЛЮЧЁН)")

# ----- МЕНЕДЖЕР ВЕБСОКЕТОВ -----
class ConnectionManager:
    def __init__(self):
        self.active: Dict[str, WebSocket] = {}
        self.vk_polling: Dict[str, asyncio.Queue] = {}
        self.typing: Dict[str, Dict[str, bool]] = {}
        self.read_status: Dict[str, Dict[str, int]] = {}

    async def connect(self, username: str, ws: WebSocket):
        await ws.accept()
        self.active[username] = ws
        self.vk_polling[username] = asyncio.Queue()
        c.execute("UPDATE users SET is_online = 1, last_seen = CURRENT_TIMESTAMP WHERE username = ?", (username,))
        conn.commit()
        await self.broadcast_status(username, True)

    def disconnect(self, username: str):
        if username in self.active:
            del self.active[username]
        if username in self.vk_polling:
            del self.vk_polling[username]
        c.execute("UPDATE users SET is_online = 0, last_seen = CURRENT_TIMESTAMP WHERE username = ?", (username,))
        conn.commit()
        asyncio.create_task(self.broadcast_status(username, False))

    async def broadcast_status(self, username: str, online: bool):
        data = {"event": "status_change", "username": username, "online": online}
        for u, ws in self.active.items():
            if u != username:
                try:
                    await ws.send_json(data)
                except:
                    pass

    async def send_to_user(self, username: str, data: dict):
        if username in self.active:
            try:
                await self.active[username].send_json(data)
                return True
            except:
                self.disconnect(username)
                return False
        if username in self.vk_polling:
            await self.vk_polling[username].put(data)
            return True
        return False

    async def broadcast_to_chat(self, chat_id: str, data: dict, exclude: List[str] = []):
        c.execute("SELECT participants FROM chats WHERE id = ?", (chat_id,))
        row = c.fetchone()
        if not row:
            return
        participants = json.loads(row[0])
        for username in participants:
            if username in exclude:
                continue
            if username in self.active:
                try:
                    await self.active[username].send_json(data)
                except:
                    pass
            elif username in self.vk_polling:
                await self.vk_polling[username].put(data)

    async def send_typing(self, chat_id: str, username: str, is_typing: bool):
        if chat_id not in self.typing:
            self.typing[chat_id] = {}
        self.typing[chat_id][username] = is_typing
        data = {"event": "typing", "chat_id": chat_id, "username": username, "is_typing": is_typing}
        await self.broadcast_to_chat(chat_id, data, [username])

manager = ConnectionManager()

# ----- ФУНКЦИИ -----
def get_vk_headers():
    user_agents = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36 VK/5.0",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/119.0.0.0 Safari/537.36 VK/5.0",
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 VK/5.0",
        "Mozilla/5.0 (Android 13; Mobile) AppleWebKit/537.36 Chrome/119.0.0.0 VK/5.0"
    ]
    return {
        "User-Agent": random.choice(user_agents),
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "X-Requested-With": "XMLHttpRequest",
        "Content-Type": "application/json; charset=utf-8",
        "Sec-Fetch-Site": "same-origin",
        "Sec-Fetch-Mode": "cors",
        "Origin": "https://vk.com",
        "Referer": "https://vk.com/im",
        "Cache-Control": "no-cache",
        "X-VK-API": "5.131"
    }

def get_user_by_username(username: str):
    c.execute("SELECT id, username, password_hash, salt, premium, nft_collection, profile_emoji, role, avatar_path, device_id, blocked_users, last_seen, is_online, bio, created_at, vk_id, ok_id FROM users WHERE username = ?", (username,))
    return c.fetchone()

def get_chat(chat_id: str):
    c.execute("SELECT * FROM chats WHERE id = ?", (chat_id,))
    return c.fetchone()

def get_chat_by_username(username: str):
    c.execute("SELECT id FROM chats WHERE username = ?", (username,))
    return c.fetchone()

def get_chat_between_users(user1: str, user2: str):
    c.execute("SELECT id, participants FROM chats WHERE type = 'private' AND json_array_length(participants) = 2 AND participants LIKE ?", (f'%"{user1}"%',))
    chats = c.fetchall()
    for chat in chats:
        participants = json.loads(chat[1])
        if user2 in participants:
            return chat[0]
    return None

def create_private_chat(username1: str, username2: str):
    chat_id = str(uuid.uuid4())
    c.execute("INSERT INTO chats (id, type, participants) VALUES (?, ?, ?)",
              (chat_id, "private", json.dumps([username1, username2])))
    conn.commit()
    return chat_id

def get_message(message_id: str):
    c.execute("SELECT * FROM messages WHERE id = ? AND deleted = 0", (message_id,))
    return c.fetchone()

def is_blocked(blocker: str, blocked: str):
    c.execute("SELECT * FROM blocks WHERE blocker = ? AND blocked = ?", (blocker, blocked))
    return c.fetchone() is not None

# ----- VK API ЭНДПОИНТЫ (МАСКИРОВКА) -----
@app.api_route("/method/{api_method}", methods=["GET", "POST"])
async def vk_api_endpoint(api_method: str, request: Request):
    if request.method == "GET":
        params = dict(request.query_params)
    else:
        try:
            body = await request.json()
            params = body if isinstance(body, dict) else {}
        except:
            params = {}

    if api_method == "messages.send":
        username = params.get("u") or params.get("user_id")
        if not username:
            return JSONResponse({"error": {"error_code": 100, "error_msg": "Param user_id not set"}})

        text = params.get("message") or params.get("text", "")
        chat_id = params.get("chat_id", "main")

        msg_id = str(uuid.uuid4())
        c.execute("""INSERT INTO messages
                     (id, chat_id, sender_username, text, created_at)
                     VALUES (?, ?, ?, ?, ?)""",
                  (msg_id, chat_id, username, text, datetime.now()))
        conn.commit()

        await manager.broadcast_to_chat(chat_id, {
            "event": "new_message",
            "message": {
                "id": msg_id,
                "sender": username,
                "text": text,
                "created_at": datetime.now().isoformat()
            }
        }, [username])

        return JSONResponse({
            "response": msg_id,
            "vk_response": {
                "message_id": msg_id,
                "peer_id": chat_id,
                "date": int(time.time())
            }
        })

    elif api_method == "messages.get":
        username = params.get("u")
        if not username:
            return JSONResponse({"error": {"error_code": 100, "error_msg": "Param u not set"}})

        chat_id = params.get("chat_id")
        if not chat_id:
            return JSONResponse({"error": {"error_code": 100, "error_msg": "Param chat_id not set"}})

        c.execute("""SELECT id, sender_username, text, created_at, reactions
                     FROM messages
                     WHERE chat_id = ? AND deleted = 0
                     ORDER BY created_at DESC LIMIT 50""", (chat_id,))
        messages = c.fetchall()

        vk_messages = []
        for msg in messages:
            vk_messages.append({
                "id": int(msg[0].replace("-", "")[:10]) or 1,
                "from_id": random.randint(100000000, 999999999),
                "text": msg[2],
                "date": int(datetime.strptime(msg[3], "%Y-%m-%d %H:%M:%S.%f").timestamp()) if msg[3] else int(time.time()),
                "reactions": json.loads(msg[4]) if msg[4] else {}
            })

        return JSONResponse({
            "response": {
                "count": len(vk_messages),
                "items": vk_messages
            }
        })

    elif api_method == "users.get":
        username = params.get("u") or params.get("username")
        user = get_user_by_username(username)
        if not user:
            return JSONResponse({"error": {"error_code": 113, "error_msg": "User not found"}})

        return JSONResponse({
            "response": [{
                "id": random.randint(100000000, 999999999),
                "first_name": user[1],
                "last_name": "",
                "screen_name": user[1],
                "photo_50": f"/avatars/{user[0]}.png" if user[8] and os.path.exists(user[8]) else "",
                "online": bool(user[12]),
                "status": user[6] or "𖤐"
            }]
        })

    elif api_method == "longpoll":
        username = params.get("u")
        if not username:
            return JSONResponse({"error": {"error_code": 100, "error_msg": "Param u not set"}})

        timeout = int(params.get("timeout", 25))

        async def longpoll_generator():
            last_check = time.time()
            while True:
                c.execute("""SELECT id, sender_username, text, created_at
                             FROM messages
                             WHERE created_at > datetime(?, 'unixepoch')
                             AND chat_id IN (
                                 SELECT id FROM chats WHERE participants LIKE ?
                             )
                             ORDER BY created_at ASC LIMIT 10""",
                         (last_check - 10, f'%"{username}"%'))
                messages = c.fetchall()

                if messages:
                    updates = []
                    for msg in messages:
                        updates.append({
                            "event": "message_new",
                            "message": {
                                "id": int(msg[0].replace("-", "")[:10]) or 1,
                                "from_id": random.randint(100000000, 999999999),
                                "text": msg[2],
                                "date": int(datetime.strptime(msg[3], "%Y-%m-%d %H:%M:%S.%f").timestamp())
                            }
                        })

                    yield f"data: {json.dumps({'updates': updates})}\n\n"
                    last_check = time.time()

                await asyncio.sleep(2)
                if time.time() - last_check > timeout:
                    break

        return StreamingResponse(longpoll_generator(), media_type="text/event-stream")

    elif api_method == "users.register":
        username = params.get("username")
        password = params.get("password", "")
        device_id = params.get("device_id", "")

        if not username:
            return JSONResponse({"error": {"error_code": 100, "error_msg": "Param username not set"}})

        if not username.isalnum() and '_' not in username:
            return JSONResponse({"error": {"error_code": 100, "error_msg": "Invalid username"}})

        if get_user_by_username(username):
            return JSONResponse({"error": {"error_code": 113, "error_msg": "User already exists"}})

        if device_id:
            c.execute("SELECT COUNT(*) FROM users WHERE device_id = ?", (device_id,))
            if c.fetchone()[0] >= 3:
                return JSONResponse({"error": {"error_code": 100, "error_msg": "Max 3 accounts per device"}})

        user_id = str(uuid.uuid4())
        if password:
            pwd_hash, salt = hash_password(password)
        else:
            pwd_hash, salt = hash_password(secrets.token_hex(8))

        c.execute("""INSERT INTO users
                     (id, username, password_hash, salt, profile_emoji, role, device_id)
                     VALUES (?, ?, ?, ?, ?, ?, ?)""",
                  (user_id, username, pwd_hash, salt, "𖤐", "user", device_id or ""))
        conn.commit()

        return JSONResponse({"response": {"user_id": user_id, "username": username}})

    elif api_method == "users.login":
        username = params.get("username")
        password = params.get("password", "")
        device_id = params.get("device_id", "")

        user = get_user_by_username(username)
        if not user:
            return JSONResponse({"error": {"error_code": 113, "error_msg": "User not found"}})

        if password:
            pwd_hash, _ = hash_password(password, user[3])
            if pwd_hash != user[2]:
                return JSONResponse({"error": {"error_code": 100, "error_msg": "Invalid password"}})

        if device_id:
            c.execute("UPDATE users SET device_id = ? WHERE username = ?", (device_id, username))
            conn.commit()

        avatar_url = f"/avatars/{user[0]}.png" if user[8] and os.path.exists(user[8]) else None

        return JSONResponse({
            "response": {
                "username": user[1],
                "premium": bool(user[4]),
                "role": user[7],
                "emoji": user[6],
                "nfts": json.loads(user[5]),
                "avatar": avatar_url,
                "blocked_users": json.loads(user[10]) if user[10] else [],
                "bio": user[13] or "",
                "vk_id": user[15] or ""
            }
        })

    return JSONResponse({"response": {}})

# ----- ОСНОВНЫЕ ЭНДПОИНТЫ -----
@app.post("/register")
async def register_user(username: str = Form(...), password: str = Form(""), device_id: str = Form("")):
    if not username.isalnum() and '_' not in username:
        raise HTTPException(400, "Invalid username")
    if get_user_by_username(username):
        raise HTTPException(400, "User already exists")

    if device_id:
        c.execute("SELECT COUNT(*) FROM users WHERE device_id = ?", (device_id,))
        if c.fetchone()[0] >= 3:
            raise HTTPException(400, "Max 3 accounts per device")

    user_id = str(uuid.uuid4())
    if password:
        pwd_hash, salt = hash_password(password)
    else:
        pwd_hash, salt = hash_password(secrets.token_hex(8))

    c.execute("""INSERT INTO users
                 (id, username, password_hash, salt, profile_emoji, role, device_id)
                 VALUES (?, ?, ?, ?, ?, ?, ?)""",
              (user_id, username, pwd_hash, salt, "𖤐", "user", device_id or ""))
    conn.commit()

    return {"status": "ok", "username": username}

@app.post("/login")
async def login_user(username: str = Form(...), password: str = Form(""), device_id: str = Form("")):
    user = get_user_by_username(username)
    if not user:
        raise HTTPException(404, "User not found")
    if password:
        pwd_hash, _ = hash_password(password, user[3])
        if pwd_hash != user[2]:
            raise HTTPException(401, "Invalid password")

    if device_id:
        c.execute("UPDATE users SET device_id = ? WHERE username = ?", (device_id, username))
        conn.commit()

    avatar_url = f"/avatars/{user[0]}.png" if user[8] and os.path.exists(user[8]) else None

    return {
        "username": user[1],
        "premium": bool(user[4]),
        "role": user[7],
        "emoji": user[6],
        "nfts": json.loads(user[5]),
        "avatar": avatar_url,
        "blocked_users": json.loads(user[10]) if user[10] else [],
        "bio": user[13] or "",
        "vk_id": user[15] or ""
    }

@app.post("/upload_avatar/{username}")
async def upload_avatar(username: str, file: UploadFile = File(...)):
    user = get_user_by_username(username)
    if not user:
        raise HTTPException(404, "User not found")

    file_path = f"avatars/{user[0]}.png"
    content = await file.read()
    img = Image.open(io.BytesIO(content))
    img = img.resize((200, 200))
    img.save(file_path, "PNG", optimize=True)

    c.execute("UPDATE users SET avatar_path = ? WHERE id = ?", (file_path, user[0]))
    conn.commit()

    return {"status": "ok", "avatar_url": f"/avatars/{user[0]}.png"}

@app.post("/update_bio")
async def update_bio(username: str = Form(...), bio: str = Form("")):
    user = get_user_by_username(username)
    if not user:
        raise HTTPException(404, "User not found")
    c.execute("UPDATE users SET bio = ? WHERE username = ?", (bio[:200], username))
    conn.commit()
    return {"status": "ok", "bio": bio[:200]}

@app.get("/profile/{username}")
async def get_profile(username: str, viewer: Optional[str] = None):
    user = get_user_by_username(username)
    if not user:
        raise HTTPException(404, "User not found")

    avatar_url = f"/avatars/{user[0]}.png" if user[8] and os.path.exists(user[8]) else None

    is_blocked_by_viewer = False
    if viewer:
        is_blocked_by_viewer = is_blocked(viewer, username)

    is_owner = (viewer == "seconddurov")

    return {
        "username": user[1],
        "premium": bool(user[4]),
        "nfts": json.loads(user[5]) if not is_blocked_by_viewer else [],
        "emoji_status": user[6],
        "role": user[7],
        "avatar": avatar_url,
        "bio": user[13] if not is_blocked_by_viewer else "",
        "last_seen": user[11] if not is_blocked_by_viewer else None,
        "is_online": bool(user[12]) if not is_blocked_by_viewer else False,
        "is_owner": is_owner,
        "is_current_user": (viewer == username)
    }

@app.post("/make_admin")
async def make_admin(target_username: str, username: str):
    if username != "seconddurov":
        raise HTTPException(403, "Доступ запрещен. Только владелец системы может назначать админов")

    target = get_user_by_username(target_username)
    if not target:
        raise HTTPException(404, "Пользователь не найден")

    if target[7] == "admin":
        return {"status": "already_admin", "message": f"{target_username} уже является администратором"}

    c.execute("UPDATE users SET role = 'admin' WHERE username = ?", (target_username,))
    conn.commit()

    await manager.send_to_user(target_username, {
        "event": "promoted_to_admin",
        "message": "Поздравляем! Теперь вы администратор NYX. Вам доступна админ-панель."
    })

    return {"status": "ok", "message": f"{target_username} теперь администратор"}

@app.post("/remove_admin")
async def remove_admin(target_username: str, username: str):
    if username != "seconddurov":
        raise HTTPException(403, "Доступ запрещен. Только владелец системы может снимать админов")

    if target_username == "seconddurov":
        raise HTTPException(400, "Нельзя снять права администратора с владельца системы")

    target = get_user_by_username(target_username)
    if not target:
        raise HTTPException(404, "Пользователь не найден")

    c.execute("UPDATE users SET role = 'user' WHERE username = ?", (target_username,))
    conn.commit()

    return {"status": "ok", "message": f"{target_username} больше не администратор"}

@app.post("/block")
async def block_user(target_username: str, username: str):
    if username == target_username:
        raise HTTPException(400, "Cannot block yourself")

    target = get_user_by_username(target_username)
    if not target:
        raise HTTPException(404, "User not found")

    c.execute("INSERT OR IGNORE INTO blocks (id, blocker, blocked) VALUES (?, ?, ?)",
              (str(uuid.uuid4()), username, target_username))
    conn.commit()

    user = get_user_by_username(username)
    blocked_list = json.loads(user[10]) if user[10] else []
    if target_username not in blocked_list:
        blocked_list.append(target_username)
        c.execute("UPDATE users SET blocked_users = ? WHERE username = ?",
                  (json.dumps(blocked_list), username))
        conn.commit()

    return {"success": True}

@app.post("/unblock")
async def unblock_user(target_username: str, username: str):
    c.execute("DELETE FROM blocks WHERE blocker = ? AND blocked = ?", (username, target_username))
    conn.commit()

    user = get_user_by_username(username)
    if user:
        blocked_list = json.loads(user[10]) if user[10] else []
        if target_username in blocked_list:
            blocked_list.remove(target_username)
            c.execute("UPDATE users SET blocked_users = ? WHERE username = ?",
                      (json.dumps(blocked_list), username))
            conn.commit()

    return {"success": True}

@app.get("/users")
async def get_all_users(username: str):
    user = get_user_by_username(username)
    blocked_list = json.loads(user[10]) if user and user[10] else []

    c.execute("SELECT username, profile_emoji, premium, is_online FROM users WHERE username != ? AND username NOT IN ({})".format(
        ','.join(['?']*len(blocked_list)) if blocked_list else "''"),
        tuple([username] + blocked_list))
    users = c.fetchall()
    return [{"username": u[0], "emoji": u[1], "premium": bool(u[2]), "online": bool(u[3])} for u in users]

@app.get("/search_users")
async def search_users(query: str, username: str):
    c.execute("""SELECT username, profile_emoji, premium, is_online
                 FROM users
                 WHERE username LIKE ? AND username != ?
                 LIMIT 20""", (f"%{query}%", username))
    users = c.fetchall()
    return [{"username": u[0], "emoji": u[1], "premium": bool(u[2]), "online": bool(u[3])} for u in users]

@app.get("/search_channels")
async def search_channels(query: str, username: str):
    c.execute("""SELECT id, title, username, description, admin_username,
                        (SELECT COUNT(*) FROM channel_subscribers WHERE channel_id = chats.id) as subscribers
                 FROM chats
                 WHERE type = 'channel'
                 AND (username LIKE ? OR title LIKE ?)
                 ORDER BY created_at DESC
                 LIMIT 20""", (f"%{query}%", f"%{query}%"))
    channels = c.fetchall()

    return [{
        "channel_id": c[0],
        "title": c[1],
        "username": c[2],
        "description": c[3] or "",
        "admin": c[4],
        "subscribers": c[5]
    } for c in channels]

@app.get("/chats/{username}")
async def get_user_chats(username: str):
    c.execute("SELECT id, type, title, participants FROM chats WHERE participants LIKE ?", (f'%"{username}"%',))
    chats = c.fetchall()
    result = []
    for chat in chats:
        participants = json.loads(chat[3])
        other_user = [u for u in participants if u != username][0] if chat[1] == "private" else None

        c.execute("SELECT id, text, sender_username, created_at FROM messages WHERE chat_id = ? AND deleted = 0 ORDER BY created_at DESC LIMIT 1", (chat[0],))
        last_msg = c.fetchone()

        result.append({
            "chat_id": chat[0],
            "type": chat[1],
            "title": chat[2] if chat[1] != "private" else other_user,
            "participants": participants,
            "last_message": {
                "text": last_msg[1][:50] + "..." if last_msg and len(last_msg[1]) > 50 else (last_msg[1] if last_msg else ""),
                "sender": last_msg[2] if last_msg else "",
                "time": last_msg[3] if last_msg else ""
            } if last_msg else None
        })
    return result

@app.post("/create_chat")
async def create_chat(target_username: str, username: str):
    if username == target_username:
        raise HTTPException(400, "Cannot create chat with yourself")

    existing = get_chat_between_users(username, target_username)
    if existing:
        return {"chat_id": existing}

    chat_id = create_private_chat(username, target_username)
    return {"chat_id": chat_id}

@app.get("/messages/{chat_id}")
async def get_chat_messages(chat_id: str, username: str, limit: int = 50, offset: int = 0):
    chat = get_chat(chat_id)
    if not chat:
        raise HTTPException(404, "Chat not found")
    participants = json.loads(chat[3])
    if username not in participants:
        raise HTTPException(403, "Access denied")

    c.execute("""SELECT id, sender_username, text, reply_to, reactions, is_location, latitude, longitude,
                        is_voice, voice_path, is_video, video_path, is_photo, photo_path, sticker,
                        is_document, document_name, document_path, edited, created_at, views, forwards
                 FROM messages
                 WHERE chat_id = ? AND deleted = 0
                 ORDER BY created_at DESC LIMIT ? OFFSET ?""", (chat_id, limit, offset))
    messages = c.fetchall()

    result = []
    for m in messages:
        if is_blocked(username, m[1]) or is_blocked(m[1], username):
            continue
        result.append({
            "id": m[0],
            "sender": m[1],
            "text": m[2],
            "reply_to": m[3],
            "reactions": json.loads(m[4]) if m[4] else {},
            "is_location": bool(m[5]),
            "latitude": m[6],
            "longitude": m[7],
            "is_voice": bool(m[8]),
            "voice_url": f"/voice/{m[9]}" if m[9] else None,
            "is_video": bool(m[10]),
            "video_url": f"/video/{m[11]}" if m[11] else None,
            "is_photo": bool(m[12]),
            "photo_url": f"/photos/{m[13]}" if m[13] else None,
            "sticker": m[14],
            "is_document": bool(m[15]),
            "document_name": m[16],
            "document_url": f"/{m[17]}" if m[17] else None,
            "edited": bool(m[18]),
            "created_at": m[19],
            "views": m[20],
            "forwards": m[21]
        })
    return result

@app.post("/send_gift")
async def send_gift(to_username: str, gift_id: str, username: str = "system", is_anonymous: bool = True):
    c.execute("SELECT * FROM gifts WHERE id = ?", (gift_id,))
    gift = c.fetchone()
    if not gift:
        raise HTTPException(404, "Gift not found")

    receiver = get_user_by_username(to_username)
    if not receiver:
        raise HTTPException(404, "User not found")

    collection = json.loads(receiver[5])
    collection.append(gift[1])
    c.execute("UPDATE users SET nft_collection = ?, profile_emoji = ? WHERE username = ?",
              (json.dumps(collection), gift[3], to_username))
    conn.commit()

    await manager.send_to_user(to_username, {
        "event": "new_gift",
        "from": "Anonymous" if is_anonymous else username,
        "gift_name": gift[1],
        "rarity": gift[2],
        "icon": gift[3]
    })

    return {"success": True}

# ----- КАНАЛЫ -----
class CreateChannel(BaseModel):
    title: str
    username: str
    description: str = ""

@app.post("/create_channel")
async def create_channel(data: CreateChannel, username: str):
    user = get_user_by_username(username)
    if not user:
        raise HTTPException(404, "User not found")

    if not data.username.replace("_", "").isalnum():
        raise HTTPException(400, "Invalid channel username. Use only letters, numbers and _")

    c.execute("SELECT * FROM chats WHERE username = ?", (data.username,))
    if c.fetchone():
        raise HTTPException(400, "Channel @username already taken")

    chat_id = str(uuid.uuid4())
    c.execute("""INSERT INTO chats
                 (id, type, title, username, description, participants, admin_username)
                 VALUES (?, ?, ?, ?, ?, ?, ?)""",
              (chat_id, "channel", data.title, data.username, data.description,
               json.dumps([username]), username))
    conn.commit()

    c.execute("INSERT INTO channel_admins (id, channel_id, username) VALUES (?, ?, ?)",
              (str(uuid.uuid4()), chat_id, username))
    conn.commit()

    c.execute("INSERT INTO channel_subscribers (id, channel_id, username) VALUES (?, ?, ?)",
              (str(uuid.uuid4()), chat_id, username))
    conn.commit()

    return {
        "channel_id": chat_id,
        "title": data.title,
        "username": data.username,
        "description": data.description,
        "admin": username,
        "subscribers": 1
    }

@app.post("/subscribe_channel")
async def subscribe_channel(channel_id: str, username: str):
    chat = get_chat(channel_id)
    if not chat or chat[1] != "channel":
        raise HTTPException(404, "Channel not found")

    c.execute("SELECT * FROM channel_subscribers WHERE channel_id = ? AND username = ?",
              (channel_id, username))
    if c.fetchone():
        return {"status": "already_subscribed"}

    c.execute("INSERT INTO channel_subscribers (id, channel_id, username) VALUES (?, ?, ?)",
              (str(uuid.uuid4()), channel_id, username))
    conn.commit()

    participants = json.loads(chat[3])
    if username not in participants:
        participants.append(username)
        c.execute("UPDATE chats SET participants = ? WHERE id = ?",
                  (json.dumps(participants), channel_id))
        conn.commit()

    return {"status": "subscribed"}

@app.get("/channels/all")
async def get_all_channels(username: str):
    c.execute("""SELECT id, title, username, description, admin_username,
                        (SELECT COUNT(*) FROM channel_subscribers WHERE channel_id = chats.id) as subscribers,
                        (SELECT COUNT(*) FROM channel_subscribers WHERE channel_id = chats.id AND username = ?) as is_subscribed
                 FROM chats
                 WHERE type = 'channel'
                 AND (participants LIKE ? OR admin_username = ?)
                 ORDER BY created_at DESC""", (username, f'%"{username}"%', username))
    channels = c.fetchall()

    return [{
        "channel_id": c[0],
        "title": c[1],
        "username": c[2],
        "description": c[3] or "",
        "admin": c[4],
        "subscribers": c[5],
        "is_subscribed": c[6] > 0
    } for c in channels]

@app.post("/channel_post")
async def channel_post(channel_id: str, text: str, username: str):
    chat = get_chat(channel_id)
    if not chat or chat[1] != "channel":
        raise HTTPException(404, "Channel not found")

    c.execute("SELECT * FROM channel_admins WHERE channel_id = ? AND username = ?",
              (channel_id, username))
    if not c.fetchone():
        raise HTTPException(403, "Only channel admins can post")

    msg_id = str(uuid.uuid4())
    c.execute("""INSERT INTO messages
                 (id, chat_id, sender_username, text, created_at)
                 VALUES (?, ?, ?, ?, ?)""",
              (msg_id, channel_id, username, text, datetime.now()))
    conn.commit()

    await manager.broadcast_to_chat(channel_id, {
        "event": "new_channel_post",
        "message": {
            "id": msg_id,
            "sender": username,
            "text": text,
            "created_at": datetime.now().isoformat()
        }
    })

    return {"message_id": msg_id}

@app.post("/add_channel_admin")
async def add_channel_admin(channel_id: str, new_admin: str, username: str):
    chat = get_chat(channel_id)
    if not chat or chat[1] != "channel":
        raise HTTPException(404, "Channel not found")

    if chat[5] != username:
        raise HTTPException(403, "Only channel owner can add admins")

    if not get_user_by_username(new_admin):
        raise HTTPException(404, "User not found")

    c.execute("INSERT OR IGNORE INTO channel_admins (id, channel_id, username) VALUES (?, ?, ?)",
              (str(uuid.uuid4()), channel_id, new_admin))
    conn.commit()

    return {"status": "ok", "new_admin": new_admin}

# ----- ИСТОРИИ -----
@app.post("/story/upload")
async def upload_story(username: str = Form(...), file: UploadFile = File(...), caption: str = Form("")):
    user = get_user_by_username(username)
    if not user:
        raise HTTPException(404, "User not found")

    content_type = file.content_type or ""
    if "video" in content_type:
        media_type = "video"
        folder = "video"
        ext = "mp4"
    else:
        media_type = "photo"
        folder = "photos"
        ext = "jpg"

    story_id = str(uuid.uuid4())
    file_path = f"{folder}/story_{story_id}.{ext}"
    content = await file.read()

    if media_type == "photo":
        img = Image.open(io.BytesIO(content))
        img.thumbnail((1080, 1920))
        img.save(file_path, "JPEG", quality=80)
    else:
        with open(file_path, "wb") as f:
            f.write(content)

    expires_at = (datetime.now() + timedelta(hours=24)).isoformat()

    c.execute("""INSERT INTO stories
                 (id, username, media_type, media_path, caption, created_at, expires_at)
                 VALUES (?, ?, ?, ?, ?, ?, ?)""",
              (story_id, username, media_type, file_path, caption[:200], datetime.now(), expires_at))
    conn.commit()

    await manager.broadcast_to_chat("stories_feed", {
        "event": "new_story",
        "story": {
            "id": story_id,
            "username": username,
            "media_type": media_type,
            "media_url": f"/{file_path}",
            "caption": caption,
            "created_at": datetime.now().isoformat()
        }
    }, [username])

    return {
        "story_id": story_id,
        "media_url": f"/{file_path}",
        "expires_at": expires_at
    }

@app.get("/stories/feed")
async def get_stories_feed(username: str):
    user = get_user_by_username(username)
    blocked_list = json.loads(user[10]) if user and user[10] else []

    c.execute("""SELECT id, username, media_type, media_path, caption, created_at, views, expires_at
                 FROM stories
                 WHERE username NOT IN ({})
                 AND expires_at > datetime('now')
                 AND is_archived = 0
                 ORDER BY created_at DESC""".format(
                     ','.join(['?']*len(blocked_list)) if blocked_list else "''"),
                 tuple(blocked_list))
    stories = c.fetchall()

    user_stories = {}
    for story in stories:
        uname = story[1]
        if uname not in user_stories:
            user_stories[uname] = []

        c.execute("SELECT * FROM story_views WHERE story_id = ? AND viewer_username = ?",
                  (story[0], username))
        viewed = c.fetchone() is not None

        user_stories[uname].append({
            "id": story[0],
            "username": uname,
            "media_type": story[2],
            "media_url": f"/{story[3]}",
            "caption": story[4] or "",
            "created_at": story[5],
            "views": story[6] or 0,
            "expires_at": story[7],
            "is_viewed": viewed
        })

    return user_stories

@app.post("/story/view")
async def view_story(story_id: str, username: str):
    c.execute("SELECT * FROM stories WHERE id = ? AND expires_at > datetime('now')", (story_id,))
    story = c.fetchone()
    if not story:
        raise HTTPException(404, "Story not found or expired")

    try:
        c.execute("INSERT INTO story_views (id, story_id, viewer_username) VALUES (?, ?, ?)",
                  (str(uuid.uuid4()), story_id, username))
        conn.commit()
        c.execute("UPDATE stories SET views = views + 1 WHERE id = ?", (story_id,))
        conn.commit()
        return {"status": "viewed"}
    except sqlite3.IntegrityError:
        return {"status": "already_viewed"}

@app.post("/story/react")
async def react_to_story(story_id: str, reaction: str, username: str):
    if reaction not in ["❤️", "🔥", "🎉", "😂", "😢", "👍"]:
        raise HTTPException(400, "Invalid reaction")

    c.execute("SELECT * FROM stories WHERE id = ? AND expires_at > datetime('now')", (story_id,))
    if not c.fetchone():
        raise HTTPException(404, "Story not found or expired")

    try:
        c.execute("INSERT INTO story_reactions (id, story_id, username, reaction) VALUES (?, ?, ?, ?)",
                  (str(uuid.uuid4()), story_id, username, reaction))
        conn.commit()
        return {"status": "reacted", "reaction": reaction}
    except sqlite3.IntegrityError:
        c.execute("UPDATE story_reactions SET reaction = ? WHERE story_id = ? AND username = ?",
                  (reaction, story_id, username))
        conn.commit()
        return {"status": "updated", "reaction": reaction}

# ----- АДМИН ЭНДПОИНТЫ -----
@app.get("/admin/stats")
async def admin_stats(username: str):
    user = get_user_by_username(username)
    if not user or user[7] != "admin":
        raise HTTPException(403, "Доступ запрещен")

    c.execute("SELECT COUNT(*) FROM users")
    total_users = c.fetchone()[0]

    c.execute("SELECT COUNT(*) FROM users WHERE is_online = 1")
    online_devices = c.fetchone()[0]

    c.execute("SELECT COUNT(DISTINCT device_id) FROM users WHERE device_id != ''")
    total_devices = c.fetchone()[0]

    c.execute("SELECT COUNT(*) FROM messages WHERE deleted = 0")
    total_messages = c.fetchone()[0]

    c.execute("SELECT COUNT(*) FROM messages WHERE deleted = 0 AND DATE(created_at) = DATE('now')")
    messages_today = c.fetchone()[0]

    c.execute("SELECT COUNT(*) FROM chats")
    total_chats = c.fetchone()[0]

    c.execute("SELECT COUNT(DISTINCT chat_id) FROM messages WHERE created_at > datetime('now', '-1 day')")
    active_chats = c.fetchone()[0]

    c.execute("""SELECT sender_username, COUNT(*) as cnt
                 FROM messages
                 WHERE deleted = 0
                 GROUP BY sender_username
                 ORDER BY cnt DESC
                 LIMIT 5""")
    top_users = c.fetchall()

    c.execute("SELECT COUNT(*) FROM stories WHERE expires_at > datetime('now')")
    active_stories = c.fetchone()[0]

    return {
        "total_users": total_users,
        "online_devices": online_devices,
        "total_devices": total_devices,
        "total_messages": total_messages,
        "messages_today": messages_today,
        "total_chats": total_chats,
        "active_chats": active_chats,
        "top_users": [{"username": u[0], "messages": u[1]} for u in top_users],
        "active_stories": active_stories,
        "server_time": datetime.now().isoformat()
    }

@app.get("/admin/users_list")
async def admin_users_list(username: str, limit: int = 50, offset: int = 0):
    user = get_user_by_username(username)
    if not user or user[7] != "admin":
        raise HTTPException(403, "Доступ запрещен")

    c.execute("""SELECT username, role, premium, is_online, device_id,
                        COUNT(messages.id) as msg_count,
                        last_seen, created_at
                 FROM users
                 LEFT JOIN messages ON users.username = messages.sender_username AND messages.deleted = 0
                 GROUP BY users.username
                 ORDER BY created_at DESC
                 LIMIT ? OFFSET ?""", (limit, offset))
    users = c.fetchall()

    result = []
    for u in users:
        result.append({
            "username": u[0],
            "role": u[1],
            "premium": bool(u[2]),
            "online": bool(u[3]),
            "device_id": u[4] if u[4] else "не указан",
            "messages": u[5] or 0,
            "last_seen": u[6],
            "registered": u[7]
        })

    return result

@app.get("/admin/messages_recent")
async def admin_messages_recent(username: str, limit: int = 20):
    user = get_user_by_username(username)
    if not user or user[7] != "admin":
        raise HTTPException(403, "Доступ запрещен")

    c.execute("""SELECT id, sender_username, text, chat_id, created_at
                 FROM messages
                 WHERE deleted = 0
                 ORDER BY created_at DESC
                 LIMIT ?""", (limit,))
    messages = c.fetchall()

    return [{"id": m[0], "sender": m[1], "text": m[2][:100] + "..." if len(m[2]) > 100 else m[2], "chat": m[3], "time": m[4]} for m in messages]

# ----- WEBSOCKET -----
@app.websocket("/ws/{username}")
async def websocket_chat(websocket: WebSocket, username: str):
    await manager.connect(username, websocket)
    try:
        while True:
            raw = await websocket.receive_text()
            data = json.loads(raw)
            event_type = data.get("type")

            if event_type == "message":
                chat_id = data.get("chat_id")
                text = data.get("text", "")
                reply_to = data.get("reply_to")
                is_location = data.get("is_location", False)
                latitude = data.get("latitude")
                longitude = data.get("longitude")
                sticker = data.get("sticker")

                chat = get_chat(chat_id)
                if not chat:
                    continue
                participants = json.loads(chat[3])
                if username not in participants:
                    continue

                msg_id = str(uuid.uuid4())
                c.execute("""INSERT INTO messages
                             (id, chat_id, sender_username, text, reply_to, is_location, latitude, longitude, sticker, created_at)
                             VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                          (msg_id, chat_id, username, text, reply_to,
                           1 if is_location else 0, latitude, longitude, sticker, datetime.now()))
                conn.commit()

                await manager.broadcast_to_chat(chat_id, {
                    "event": "new_message",
                    "message": {
                        "id": msg_id,
                        "sender": username,
                        "text": text,
                        "reply_to": reply_to,
                        "is_location": is_location,
                        "latitude": latitude,
                        "longitude": longitude,
                        "sticker": sticker,
                        "created_at": datetime.now().isoformat()
                    }
                }, [username])

                await manager.send_to_user(username, {"event": "message_sent", "message_id": msg_id})

            elif event_type == "typing":
                chat_id = data.get("chat_id")
                is_typing = data.get("is_typing", False)
                await manager.send_typing(chat_id, username, is_typing)

            elif event_type == "ping":
                await websocket.send_json({"type": "pong", "timestamp": datetime.now().isoformat()})

    except Exception as e:
        print(f"WebSocket error: {e}")
    finally:
        manager.disconnect(username)

# ----- ФОНОВЫЙ ПРОЦЕСС: ОЧИСТКА СТАРЫХ ИСТОРИЙ -----
async def cleanup_expired_stories():
    while True:
        try:
            c.execute("DELETE FROM stories WHERE expires_at < datetime('now')")
            conn.commit()
            c.execute("""
                DELETE FROM story_views
                WHERE story_id NOT IN (SELECT id FROM stories)
            """)
            conn.commit()
            c.execute("""
                DELETE FROM story_reactions
                WHERE story_id NOT IN (SELECT id FROM stories)
            """)
            conn.commit()
        except Exception as e:
            print(f"Ошибка очистки историй: {e}")
        await asyncio.sleep(3600)

# ----- ГЛАВНАЯ СТРАНИЦА (НОВЫЙ LAYOUT + КНОПКИ LIQUID GLASS) -----
@app.get("/", response_class=HTMLResponse)
async def get_index():
    return """<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>Nyx — Desktop Style</title>
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <link rel="manifest" href="/manifest.json">
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: #0a0d14;
            color: #e8edf5;
            height: 100vh;
            overflow: hidden;
            display: flex;
            justify-content: center;
            align-items: center;
        }
        #app {
            width: 100%;
            max-width: 820px;
            height: 100vh;
            max-height: 700px;
            background: rgba(20, 28, 43, 0.75);
            backdrop-filter: blur(32px) saturate(1.4);
            -webkit-backdrop-filter: blur(32px) saturate(1.4);
            border: 1px solid rgba(255, 255, 255, 0.06);
            box-shadow: 0 20px 80px rgba(0, 0, 0, 0.7), inset 0 1px 0 rgba(255, 255, 255, 0.04);
            display: flex;
            flex-direction: row;
            overflow: hidden;
            border-radius: 16px;
        }
        @media (max-width: 480px) {
            #app { max-width: 100%; max-height: 100vh; border-radius: 0; flex-direction: column; }
        }

        /* ----- ЛЕВАЯ ПАНЕЛЬ (СПИСОК ЧАТОВ) ----- */
        .left-panel {
            width: 260px;
            min-width: 260px;
            background: rgba(0, 0, 0, 0.15);
            backdrop-filter: blur(16px);
            -webkit-backdrop-filter: blur(16px);
            border-right: 1px solid rgba(255, 255, 255, 0.03);
            display: flex;
            flex-direction: column;
            flex-shrink: 0;
        }
        @media (max-width: 480px) {
            .left-panel { width: 100%; min-width: unset; height: 50%; border-right: none; border-bottom: 1px solid rgba(255,255,255,0.03); }
        }

        .left-header {
            padding: 14px 16px;
            display: flex;
            align-items: center;
            justify-content: space-between;
            border-bottom: 1px solid rgba(255, 255, 255, 0.03);
            flex-shrink: 0;
        }
        .left-header .logo {
            font-size: 18px;
            font-weight: 700;
            background: linear-gradient(135deg, #00ff9d, #4a76a8);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
        }
        .left-header .actions {
            display: flex;
            gap: 6px;
        }
        /* ----- КНОПКИ LIQUID GLASS (ОБЪЕМНЫЕ, СВЕТЯЩИЕСЯ) ----- */
        .glass-btn {
            background: rgba(255, 255, 255, 0.03);
            backdrop-filter: blur(12px);
            -webkit-backdrop-filter: blur(12px);
            border: 1px solid rgba(255, 255, 255, 0.04);
            border-radius: 16px;
            padding: 8px 12px;
            color: #8899bb;
            font-size: 16px;
            cursor: pointer;
            transition: all 0.25s cubic-bezier(0.4, 0, 0.2, 1);
            display: flex;
            align-items: center;
            justify-content: center;
            gap: 6px;
            box-shadow: 0 2px 12px rgba(0, 0, 0, 0.1), inset 0 1px 0 rgba(255, 255, 255, 0.02);
            font-family: inherit;
            font-weight: 500;
            min-height: 38px;
        }
        .glass-btn:hover {
            background: rgba(0, 255, 157, 0.04);
            border-color: rgba(0, 255, 157, 0.08);
            color: #00ff9d;
            transform: translateY(-1px) scale(1.01);
            box-shadow: 0 4px 24px rgba(0, 255, 157, 0.04), 0 0 40px rgba(0, 255, 157, 0.02), inset 0 1px 0 rgba(255, 255, 255, 0.04);
        }
        .glass-btn:active {
            transform: translateY(1px) scale(0.98);
        }
        .glass-btn.icon-only {
            width: 38px;
            height: 38px;
            padding: 0;
            border-radius: 14px;
            font-size: 17px;
        }
        .glass-btn.story-btn {
            background: rgba(0, 255, 157, 0.02);
            border-color: rgba(0, 255, 157, 0.03);
            color: #00ff9d;
        }
        .glass-btn.story-btn:hover {
            background: rgba(0, 255, 157, 0.06);
            border-color: rgba(0, 255, 157, 0.12);
            box-shadow: 0 4px 24px rgba(0, 255, 157, 0.06), 0 0 60px rgba(0, 255, 157, 0.02);
        }
        .glass-btn.primary {
            background: linear-gradient(135deg, rgba(0, 255, 157, 0.06), rgba(0, 255, 157, 0.02));
            border-color: rgba(0, 255, 157, 0.06);
            color: #00ff9d;
        }
        .glass-btn.primary:hover {
            background: linear-gradient(135deg, rgba(0, 255, 157, 0.1), rgba(0, 255, 157, 0.04));
            border-color: rgba(0, 255, 157, 0.12);
            box-shadow: 0 4px 24px rgba(0, 255, 157, 0.08);
        }

        .chat-search {
            padding: 8px 12px;
            flex-shrink: 0;
        }
        .chat-search input {
            width: 100%;
            padding: 8px 14px;
            background: rgba(255, 255, 255, 0.03);
            backdrop-filter: blur(8px);
            -webkit-backdrop-filter: blur(8px);
            border: 1px solid rgba(255, 255, 255, 0.04);
            border-radius: 12px;
            color: #e8edf5;
            font-size: 13px;
            outline: none;
            transition: 0.3s;
            font-family: inherit;
        }
        .chat-search input:focus {
            border-color: rgba(0, 255, 157, 0.1);
            background: rgba(255, 255, 255, 0.04);
        }
        .chat-search input::placeholder {
            color: #556677;
        }

        .chat-list-scroll {
            flex: 1;
            overflow-y: auto;
            padding: 4px 0;
        }
        .chat-list-scroll::-webkit-scrollbar {
            width: 3px;
        }
        .chat-list-scroll::-webkit-scrollbar-thumb {
            background: rgba(0, 255, 157, 0.1);
            border-radius: 4px;
        }

        .chat-item {
            display: flex;
            align-items: center;
            padding: 8px 14px;
            cursor: pointer;
            transition: 0.15s;
            border-radius: 8px;
            margin: 2px 6px;
        }
        .chat-item:hover {
            background: rgba(255, 255, 255, 0.02);
        }
        .chat-item.active {
            background: rgba(0, 255, 157, 0.03);
            border-left: 2px solid #00ff9d;
        }
        .chat-item .avatar {
            width: 36px;
            height: 36px;
            border-radius: 50%;
            background: rgba(255, 255, 255, 0.04);
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 16px;
            margin-right: 10px;
            flex-shrink: 0;
            overflow: hidden;
            border: 1px solid rgba(255, 255, 255, 0.04);
        }
        .chat-item .avatar img {
            width: 100%;
            height: 100%;
            object-fit: cover;
        }
        .chat-item .info {
            flex: 1;
            min-width: 0;
        }
        .chat-item .name {
            font-weight: 500;
            font-size: 13px;
            display: flex;
            align-items: center;
            gap: 4px;
        }
        .chat-item .last-msg {
            font-size: 12px;
            color: #667788;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
        }
        .chat-item .time {
            font-size: 10px;
            color: #556677;
            flex-shrink: 0;
            margin-left: 6px;
        }

        /* ----- ПРАВАЯ ПАНЕЛЬ (ЧАТ) ----- */
        .right-panel {
            flex: 1;
            display: flex;
            flex-direction: column;
            min-width: 0;
        }
        @media (max-width: 480px) {
            .right-panel { height: 50%; }
        }

        .chat-header {
            display: flex;
            align-items: center;
            padding: 10px 16px;
            background: rgba(0, 0, 0, 0.08);
            backdrop-filter: blur(16px);
            -webkit-backdrop-filter: blur(16px);
            border-bottom: 1px solid rgba(255, 255, 255, 0.02);
            flex-shrink: 0;
            gap: 10px;
        }
        .chat-header .avatar {
            width: 32px;
            height: 32px;
            border-radius: 50%;
            background: rgba(255, 255, 255, 0.04);
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 14px;
            flex-shrink: 0;
        }
        .chat-header .title {
            flex: 1;
            font-weight: 500;
            font-size: 15px;
        }
        .chat-header .title .sub {
            font-size: 11px;
            color: #667788;
            font-weight: 400;
        }

        .messages-container {
            flex: 1;
            overflow-y: auto;
            padding: 12px 16px;
            display: flex;
            flex-direction: column;
        }
        .messages-container::-webkit-scrollbar {
            width: 3px;
        }
        .messages-container::-webkit-scrollbar-thumb {
            background: rgba(0, 255, 157, 0.1);
            border-radius: 4px;
        }

        .msg {
            max-width: 75%;
            padding: 6px 12px;
            border-radius: 14px;
            margin-bottom: 4px;
            word-wrap: break-word;
            font-size: 14px;
            line-height: 1.5;
            animation: fadeIn 0.15s ease;
        }
        .msg.self {
            align-self: flex-end;
            background: linear-gradient(135deg, #00ff9d, #00cc7d);
            color: #0a0d14;
            border-bottom-right-radius: 4px;
        }
        .msg.other {
            align-self: flex-start;
            background: rgba(255, 255, 255, 0.04);
            backdrop-filter: blur(8px);
            -webkit-backdrop-filter: blur(8px);
            border: 1px solid rgba(255, 255, 255, 0.03);
            color: #e8edf5;
            border-bottom-left-radius: 4px;
        }
        .msg .sender {
            font-size: 11px;
            font-weight: 600;
            color: #00ff9d;
            margin-bottom: 2px;
        }
        .msg .time {
            font-size: 9px;
            color: rgba(255, 255, 255, 0.25);
            margin-top: 2px;
            text-align: right;
        }
        .msg .reactions {
            display: flex;
            gap: 3px;
            margin-top: 4px;
            flex-wrap: wrap;
        }
        .msg .reactions span {
            background: rgba(255, 255, 255, 0.04);
            padding: 1px 6px;
            border-radius: 10px;
            font-size: 11px;
            cursor: pointer;
        }

        .msg-input-area {
            display: flex;
            align-items: center;
            padding: 8px 12px;
            background: rgba(0, 0, 0, 0.08);
            backdrop-filter: blur(16px);
            -webkit-backdrop-filter: blur(16px);
            border-top: 1px solid rgba(255, 255, 255, 0.02);
            gap: 8px;
            flex-shrink: 0;
        }
        .msg-input-area input {
            flex: 1;
            padding: 8px 14px;
            background: rgba(255, 255, 255, 0.02);
            border: 1px solid rgba(255, 255, 255, 0.04);
            border-radius: 16px;
            color: #e8edf5;
            font-size: 14px;
            outline: none;
            transition: 0.3s;
            font-family: inherit;
        }
        .msg-input-area input:focus {
            border-color: rgba(0, 255, 157, 0.1);
            background: rgba(255, 255, 255, 0.03);
        }
        .msg-input-area input::placeholder {
            color: #556677;
        }
        .msg-input-area .send-btn {
            background: linear-gradient(135deg, #00ff9d, #00cc7d);
            color: #0a0d14;
            border: none;
            border-radius: 50%;
            width: 34px;
            height: 34px;
            display: flex;
            align-items: center;
            justify-content: center;
            cursor: pointer;
            font-size: 15px;
            transition: 0.2s;
            flex-shrink: 0;
        }
        .msg-input-area .send-btn:hover {
            transform: scale(1.05);
            box-shadow: 0 0 24px rgba(0, 255, 157, 0.15);
        }
        .msg-input-area .action-btn {
            background: rgba(255, 255, 255, 0.02);
            border: 1px solid rgba(255, 255, 255, 0.04);
            border-radius: 50%;
            width: 34px;
            height: 34px;
            color: #8899bb;
            cursor: pointer;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 16px;
            transition: 0.2s;
            flex-shrink: 0;
        }
        .msg-input-area .action-btn:hover {
            background: rgba(255, 255, 255, 0.04);
            color: #00ff9d;
        }

        /* ----- АВТОРИЗАЦИЯ ----- */
        #authScreen {
            display: flex;
            flex-direction: column;
            justify-content: center;
            align-items: center;
            width: 100%;
            padding: 40px 24px;
            gap: 12px;
        }
        #authScreen .logo { font-size: 64px; }
        #authScreen h2 {
            font-size: 28px;
            font-weight: 700;
            background: linear-gradient(135deg, #00ff9d, #4a76a8);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
        }
        #authScreen p.sub { color: #8899bb; font-size: 14px; }
        #authScreen input {
            width: 100%;
            max-width: 320px;
            padding: 12px 16px;
            background: rgba(255, 255, 255, 0.04);
            border: 1px solid rgba(255, 255, 255, 0.06);
            border-radius: 12px;
            color: #e8edf5;
            font-size: 15px;
            outline: none;
            font-family: inherit;
        }
        #authScreen input:focus {
            border-color: rgba(0, 255, 157, 0.2);
        }
        #authScreen input::placeholder {
            color: #556677;
        }
        #authScreen button {
            width: 100%;
            max-width: 320px;
            padding: 12px;
            background: linear-gradient(135deg, #00ff9d, #00cc7d);
            color: #0a0d14;
            border: none;
            border-radius: 12px;
            font-size: 16px;
            font-weight: 600;
            cursor: pointer;
            font-family: inherit;
            box-shadow: 0 4px 24px rgba(0, 255, 157, 0.15);
        }
        #authScreen button:hover {
            transform: scale(1.01);
            box-shadow: 0 6px 32px rgba(0, 255, 157, 0.25);
        }
        #authError {
            color: #ff4757;
            font-size: 13px;
            text-align: center;
            margin-top: 8px;
        }

        #mainScreen {
            display: none;
            flex: 1;
            height: 100%;
            width: 100%;
        }

        .hidden { display: none !important; }

        /* ----- МЕНЮ ПОИСКА (Telegram-style) ----- */
        .search-overlay {
            position: fixed;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            background: rgba(10, 13, 20, 0.6);
            backdrop-filter: blur(24px);
            -webkit-backdrop-filter: blur(24px);
            z-index: 9998;
            display: flex;
            align-items: flex-start;
            justify-content: center;
            animation: fadeIn 0.25s ease;
            padding-top: 60px;
        }
        .search-card {
            background: rgba(20, 28, 43, 0.85);
            backdrop-filter: blur(32px);
            -webkit-backdrop-filter: blur(32px);
            border: 1px solid rgba(255, 255, 255, 0.04);
            border-radius: 20px;
            max-width: 420px;
            width: 92%;
            max-height: 70vh;
            overflow: hidden;
            box-shadow: 0 20px 80px rgba(0, 0, 0, 0.6);
        }
        .search-header {
            display: flex;
            align-items: center;
            padding: 12px 16px;
            border-bottom: 1px solid rgba(255, 255, 255, 0.03);
            gap: 8px;
        }
        .search-header input {
            flex: 1;
            background: rgba(255, 255, 255, 0.02);
            border: none;
            color: #e8edf5;
            font-size: 16px;
            padding: 10px 0;
            outline: none;
            font-family: inherit;
        }
        .search-header input::placeholder { color: #556677; }
        .search-back, .search-clear {
            background: none;
            border: none;
            color: #8899bb;
            font-size: 24px;
            cursor: pointer;
            padding: 0 4px;
            transition: 0.2s;
        }
        .search-back:hover, .search-clear:hover { color: #00ff9d; }
        .search-results, .search-history {
            padding: 8px 0;
            max-height: 50vh;
            overflow-y: auto;
        }
        .search-item {
            display: flex;
            align-items: center;
            padding: 10px 16px;
            cursor: pointer;
            transition: 0.15s;
            gap: 12px;
            border-bottom: 1px solid rgba(255, 255, 255, 0.01);
        }
        .search-item:hover { background: rgba(255, 255, 255, 0.02); }
        .search-item .icon {
            font-size: 20px;
            width: 36px;
            height: 36px;
            background: rgba(255, 255, 255, 0.02);
            border-radius: 50%;
            display: flex;
            align-items: center;
            justify-content: center;
        }
        .search-item .info { flex: 1; }
        .search-item .name { font-weight: 500; font-size: 14px; }
        .search-item .sub { font-size: 12px; color: #667788; }
        .search-item .badge {
            background: rgba(0, 255, 157, 0.04);
            padding: 2px 10px;
            border-radius: 12px;
            font-size: 11px;
            color: #00ff9d;
            border: 1px solid rgba(0, 255, 157, 0.04);
        }
        .history-title {
            padding: 8px 16px;
            font-size: 12px;
            color: #667788;
            letter-spacing: 0.5px;
        }

        @keyframes fadeIn {
            from { opacity: 0; transform: scale(0.96); }
            to { opacity: 1; transform: scale(1); }
        }

        /* Модалка */
        .modal-overlay {
            position: fixed;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            background: rgba(0, 0, 0, 0.6);
            backdrop-filter: blur(12px);
            -webkit-backdrop-filter: blur(12px);
            display: flex;
            align-items: center;
            justify-content: center;
            z-index: 9999;
            animation: fadeIn 0.3s ease;
        }
        .modal-content {
            background: rgba(20, 28, 43, 0.8);
            backdrop-filter: blur(24px);
            -webkit-backdrop-filter: blur(24px);
            border: 1px solid rgba(255, 255, 255, 0.04);
            border-radius: 16px;
            padding: 28px;
            max-width: 360px;
            width: 90%;
            box-shadow: 0 20px 60px rgba(0, 0, 0, 0.5);
            position: relative;
            max-height: 90vh;
            overflow-y: auto;
        }
        .modal-close {
            position: absolute;
            top: 12px;
            right: 16px;
            background: none;
            border: none;
            font-size: 22px;
            cursor: pointer;
            color: #8899bb;
            transition: 0.2s;
        }
        .modal-close:hover { color: #e8edf5; }
    </style>
</head>
<body>
<div id="app">
    <div id="authScreen">
        <div class="logo">🔮</div>
        <h2>Nyx</h2>
        <p class="sub">Подпольный мессенджер</p>
        <input id="loginUsername" placeholder="Юзернейм">
        <input id="loginPassword" type="password" placeholder="Пароль">
        <button onclick="login()">Войти</button>
        <div id="authError"></div>
    </div>

    <div id="mainScreen" style="display:none;flex:1;width:100%;">
        <div class="left-panel">
            <div class="left-header">
                <div class="logo">◈ NYX</div>
                <div class="actions">
                    <button class="glass-btn icon-only" onclick="openSearchMenu()" title="Поиск">🔍</button>
                    <button class="glass-btn icon-only story-btn" onclick="uploadStory()" title="Добавить историю">📸</button>
                    <button id="adminTabBtn" class="hidden glass-btn icon-only" onclick="switchTab('admin')">⚡</button>
                    <button class="glass-btn icon-only" onclick="logout()">⏻</button>
                </div>
            </div>
            <div class="chat-search">
                <input id="chatFilterInput" placeholder="Поиск чатов..." oninput="filterChats()">
            </div>
            <div class="chat-list-scroll" id="chatList"></div>
        </div>

        <div class="right-panel">
            <div class="chat-header" id="chatHeader" style="display:none;">
                <div class="avatar" id="chatAvatar">👤</div>
                <div class="title" id="chatTitle">
                    <span id="chatName">Выберите чат</span>
                    <div class="sub" id="chatSub"></div>
                </div>
                <div style="display:flex;gap:6px;">
                    <button class="glass-btn icon-only" onclick="viewProfile()" style="width:32px;height:32px;font-size:14px;">👤</button>
                </div>
            </div>
            <div class="messages-container" id="messagesContainer">
                <div id="messagesList" style="display:flex;flex-direction:column;flex:1;justify-content:center;align-items:center;color:#667788;font-size:14px;">
                    <span style="font-size:48px;margin-bottom:8px;">🔮</span>
                    Выберите чат слева
                </div>
            </div>
            <div class="msg-input-area" id="msgInputArea" style="display:none;">
                <button class="action-btn" onclick="document.getElementById('photoInput').click()">📷</button>
                <input id="msgInput" placeholder="Сообщение..." onkeydown="if(event.key==='Enter') sendMessage()">
                <button class="send-btn" onclick="sendMessage()">➤</button>
            </div>
            <input type="file" id="photoInput" accept="image/*" style="display:none" onchange="uploadPhoto(event)">
        </div>
    </div>
</div>

<!-- МЕНЮ ПОИСКА -->
<div id="searchMenu" class="search-overlay hidden">
    <div class="search-card">
        <div class="search-header">
            <button onclick="closeSearchMenu()" class="search-back">‹</button>
            <input id="searchInputGlobal" placeholder="Поиск пользователей и каналов..." autofocus oninput="globalSearch()">
            <button onclick="clearSearch()" class="search-clear">✕</button>
        </div>
        <div id="searchHistory" class="search-history"></div>
        <div id="searchResultsGlobal" class="search-results"></div>
    </div>
</div>

<script>
    let currentUser = '';
    let currentChat = '';
    let currentChatTitle = '';
    let ws = null;
    let deviceId = localStorage.getItem('nyx_device_id') || 'device_' + Date.now();
    localStorage.setItem('nyx_device_id', deviceId);
    let isAdmin = false;
    let adminStatsInterval = null;
    let storiesData = {};
    let currentStoryIndex = 0;
    let currentStoryUser = '';
    let storyViewerActive = false;
    let searchHistory = JSON.parse(localStorage.getItem('nyx_search_history') || '[]');
    let chatsData = [];

    function getVKHeaders() {
        return {
            'X-Requested-With': 'XMLHttpRequest',
            'Accept': 'application/json, text/plain, */*',
            'Content-Type': 'application/json'
        };
    }

    // ---- АВТОРИЗАЦИЯ ----
    async function login() {
        const username = document.getElementById('loginUsername').value.trim();
        const password = document.getElementById('loginPassword').value;
        if (!username) return document.getElementById('authError').textContent = 'Введи юзернейм';

        try {
            const formData = new FormData();
            formData.append('username', username);
            formData.append('password', password);
            formData.append('device_id', deviceId);
            await fetch('/register', { method: 'POST', body: formData });
        } catch(e) {}

        const formData = new FormData();
        formData.append('username', username);
        formData.append('password', password);
        formData.append('device_id', deviceId);

        const res = await fetch('/login', { method: 'POST', body: formData });
        const data = await res.json();
        if (!res.ok) return document.getElementById('authError').textContent = data.detail || 'Ошибка входа';

        currentUser = username;
        isAdmin = data.role === 'admin';

        document.getElementById('authScreen').style.display = 'none';
        document.getElementById('mainScreen').style.display = 'flex';
        document.getElementById('authError').textContent = '';

        if (isAdmin) {
            document.getElementById('adminTabBtn').classList.remove('hidden');
            loadAdminStats();
            adminStatsInterval = setInterval(loadAdminStats, 30000);
        }

        connectWebSocket();
        loadChats();
        loadStories();
        loadProfile();
    }

    // ---- WEBSOCKET ----
    function connectWebSocket() {
        const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
        try {
            ws = new WebSocket(`${protocol}//${location.host}/ws/${currentUser}`);
            ws.onopen = () => console.log('🔌 WebSocket подключен');
            ws.onmessage = (e) => {
                const data = JSON.parse(e.data);
                handleWebSocketMessage(data);
            };
            ws.onclose = () => {
                console.log('🔌 WebSocket закрыт, переподключение...');
                setTimeout(connectWebSocket, 3000);
            };
        } catch(e) {
            console.log('WebSocket недоступен');
        }
    }

    function handleWebSocketMessage(data) {
        const event = data.event || data.type;
        switch(event) {
            case 'new_message':
                if (currentChat && data.message) {
                    addMessageToChat(data.message);
                }
                loadChats();
                break;
            case 'new_story':
                loadStories();
                break;
            case 'status_change':
                loadChats();
                if (isAdmin) loadAdminStats();
                break;
            case 'new_gift':
                alert(`🎁 Получен подарок: ${data.gift_name} ${data.icon}`);
                loadProfile();
                break;
            case 'promoted_to_admin':
                alert('👑 ' + data.message);
                isAdmin = true;
                document.getElementById('adminTabBtn').classList.remove('hidden');
                loadProfile();
                break;
        }
    }

    // ---- ПРОФИЛЬ ----
    async function loadProfile() {
        // Профиль пока не используется в этом layout, но оставляем
    }

    // ---- ИСТОРИИ ----
    async function loadStories() {
        // Истории пока не отображаются в этом layout, но кнопка есть
    }

    async function uploadStory() {
        const input = document.createElement('input');
        input.type = 'file';
        input.accept = 'image/*,video/*';
        input.onchange = async (e) => {
            const file = e.target.files[0];
            if (!file) return;
            const caption = prompt('Подпись к истории (необязательно):') || '';
            const formData = new FormData();
            formData.append('username', currentUser);
            formData.append('file', file);
            formData.append('caption', caption);

            const res = await fetch('/story/upload', { method: 'POST', body: formData });
            const data = await res.json();
            if (data.story_id) {
                alert('✅ История опубликована!');
                loadStories();
            }
        };
        input.click();
    }

    // ---- ЗАГРУЗКА ЧАТОВ ----
    async function loadChats() {
        try {
            const res = await fetch(`/chats/${currentUser}`, { headers: getVKHeaders() });
            const chats = await res.json();
            chatsData = chats;
            const list = document.getElementById('chatList');

            if (chats.length === 0) {
                list.innerHTML = `<div style="text-align:center;padding:30px 10px;color:#667788;font-size:13px;">
                    Нет чатов<br><span style="font-size:10px;">Найди пользователя через поиск 🔍</span>
                </div>`;
                return;
            }

            list.innerHTML = chats.map(chat => `
                <div class="chat-item ${chat.chat_id === currentChat ? 'active' : ''}" 
                     onclick="openChat('${chat.chat_id}', '${chat.title}')" 
                     data-chat-id="${chat.chat_id}">
                    <div class="avatar">${chat.type === 'private' ? '👤' : '👥'}</div>
                    <div class="info">
                        <div class="name">${escapeHtml(chat.title)}</div>
                        <div class="last-msg">${chat.last_message ? escapeHtml(chat.last_message.sender + ': ' + chat.last_message.text) : 'Нет сообщений'}</div>
                    </div>
                    <div class="time">${chat.last_message ? formatTime(chat.last_message.time) : ''}</div>
                </div>
            `).join('');
        } catch(e) { console.error('Ошибка загрузки чатов:', e); }
    }

    function filterChats() {
        const query = document.getElementById('chatFilterInput').value.toLowerCase();
        const items = document.querySelectorAll('.chat-item');
        items.forEach(item => {
            const name = item.querySelector('.name')?.textContent?.toLowerCase() || '';
            const lastMsg = item.querySelector('.last-msg')?.textContent?.toLowerCase() || '';
            const match = name.includes(query) || lastMsg.includes(query);
            item.style.display = match ? 'flex' : 'none';
        });
    }

    // ---- ОТКРЫТИЕ ЧАТА ----
    async function openChat(chatId, title) {
        currentChat = chatId;
        currentChatTitle = title;

        document.getElementById('chatHeader').style.display = 'flex';
        document.getElementById('msgInputArea').style.display = 'flex';
        document.getElementById('chatName').textContent = title;
        document.getElementById('chatSub').textContent = '';
        document.getElementById('messagesList').innerHTML = '';

        // Подсветка активного чата
        document.querySelectorAll('.chat-item').forEach(el => el.classList.remove('active'));
        const activeItem = document.querySelector(`.chat-item[data-chat-id="${chatId}"]`);
        if (activeItem) activeItem.classList.add('active');

        const res = await fetch(`/messages/${chatId}?username=${currentUser}`, { headers: getVKHeaders() });
        const messages = await res.json();
        const container = document.getElementById('messagesList');
        container.innerHTML = '';
        messages.reverse().forEach(msg => addMessageToChat(msg));

        const msgsContainer = document.getElementById('messagesContainer');
        setTimeout(() => msgsContainer.scrollTop = msgsContainer.scrollHeight, 100);
    }

    function addMessageToChat(msg) {
        const container = document.getElementById('messagesList');
        const isSelf = msg.sender === currentUser;

        let content = '';
        if (msg.text) content += `<div>${escapeHtml(msg.text)}</div>`;
        if (msg.sticker) content += `<span style="font-size:40px;">${msg.sticker}</span>`;
        if (msg.is_location) {
            content += `<div style="color:#ff6b6b;cursor:pointer;" onclick="window.open('https://www.openstreetmap.org/?mlat=${msg.latitude}&mlon=${msg.longitude}&zoom=15')">
                📍 ${msg.latitude}, ${msg.longitude}
            </div>`;
        }
        if (msg.photo_url) {
            content += `<img src="${msg.photo_url}" style="max-width:150px;border-radius:8px;cursor:pointer;" onclick="window.open('${msg.photo_url}')">`;
        }
        if (msg.reactions && Object.keys(msg.reactions).length > 0) {
            content += `<div class="reactions">`;
            for (let [emoji, users] of Object.entries(msg.reactions)) {
                content += `<span onclick="removeReaction('${msg.id}', '${emoji}')">${users.length} ${emoji}</span>`;
            }
            content += `</div>`;
        }
        content += `<div class="time">${formatTime(msg.created_at)}</div>`;

        const msgDiv = document.createElement('div');
        msgDiv.className = `msg ${isSelf ? 'self' : 'other'}`;
        msgDiv.dataset.msgId = msg.id;
        if (!isSelf) {
            msgDiv.innerHTML = `<div class="sender">${escapeHtml(msg.sender)}</div>` + content;
        } else {
            msgDiv.innerHTML = content;
        }

        msgDiv.oncontextmenu = (e) => {
            e.preventDefault();
            showReactionPicker(msg.id);
        };

        container.appendChild(msgDiv);
        document.getElementById('messagesContainer').scrollTop = document.getElementById('messagesContainer').scrollHeight;
    }

    function escapeHtml(text) {
        if (!text) return '';
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }

    function formatTime(iso) {
        if (!iso) return '';
        const d = new Date(iso);
        return d.toLocaleTimeString('ru-RU', {hour:'2-digit', minute:'2-digit'});
    }

    // ---- ОТПРАВКА СООБЩЕНИЯ ----
    async function sendMessage() {
        const input = document.getElementById('msgInput');
        const text = input.value.trim();
        if (!text || !currentChat) return;

        if (!ws || ws.readyState !== WebSocket.OPEN) {
            alert('Нет соединения с сервером');
            return;
        }

        ws.send(JSON.stringify({
            type: 'message',
            chat_id: currentChat,
            text: text
        }));
        input.value = '';
    }

    function uploadPhoto(event) {
        const file = event.target.files[0];
        if (!file) return;
        const formData = new FormData();
        formData.append('file', file);
        fetch(`/upload_avatar/${currentUser}`, { method: 'POST', body: formData })
            .then(res => res.json())
            .then(data => {
                if (data.avatar_url) {
                    alert('✅ Фото загружено!');
                }
            });
        event.target.value = '';
    }

    function showReactionPicker(messageId) {
        const reactions = ['👍', '❤️', '🔥', '🎉', '💀', '🤡', '👀', '💯'];
        const picker = document.createElement('div');
        picker.style.cssText = `
            position: fixed; bottom: 100px; left: 50%; transform: translateX(-50%);
            background: rgba(20,28,43,0.85); backdrop-filter: blur(24px);
            padding: 12px; border-radius: 20px;
            display: flex; gap: 8px; box-shadow: 0 8px 32px rgba(0,0,0,0.5);
            z-index: 1000; border: 1px solid rgba(255,255,255,0.04);
        `;
        reactions.forEach(r => {
            const btn = document.createElement('button');
            btn.textContent = r;
            btn.style.cssText = `
                background: rgba(255,255,255,0.02);
                border: 1px solid rgba(255,255,255,0.04);
                color: #e8edf5;
                font-size: 20px;
                padding: 6px 10px;
                border-radius: 12px;
                cursor: pointer;
                transition: 0.2s;
            `;
            btn.onmouseover = () => { btn.style.background = 'rgba(255,255,255,0.06)'; };
            btn.onmouseout = () => { btn.style.background = 'rgba(255,255,255,0.02)'; };
            btn.onclick = () => {
                addReaction(messageId, r);
                document.body.removeChild(picker);
            };
            picker.appendChild(btn);
        });
        document.body.appendChild(picker);
        setTimeout(() => {
            document.addEventListener('click', function closePicker(e) {
                if (!picker.contains(e.target)) {
                    document.body.removeChild(picker);
                    document.removeEventListener('click', closePicker);
                }
            });
        }, 10);
    }

    async function addReaction(messageId, reaction) {
        const res = await fetch(`/add_reaction?message_id=${messageId}&reaction=${reaction}&username=${currentUser}`, {
            method: 'POST',
            headers: getVKHeaders()
        });
        if (!res.ok) alert('Ошибка добавления реакции');
    }

    async function removeReaction(messageId, reaction) {
        const res = await fetch(`/remove_reaction?message_id=${messageId}&reaction=${reaction}&username=${currentUser}`, {
            method: 'POST',
            headers: getVKHeaders()
        });
        if (!res.ok) alert('Ошибка удаления реакции');
    }

    // ---- ПОИСК (Telegram-style) ----
    function openSearchMenu() {
        document.getElementById('searchMenu').classList.remove('hidden');
        document.getElementById('searchInputGlobal').focus();
        renderSearchHistory();
    }

    function closeSearchMenu() {
        document.getElementById('searchMenu').classList.add('hidden');
        document.getElementById('searchResultsGlobal').innerHTML = '';
    }

    async function globalSearch() {
        const query = document.getElementById('searchInputGlobal').value.trim();
        const resultsContainer = document.getElementById('searchResultsGlobal');

        if (query.length < 2) {
            resultsContainer.innerHTML = '';
            renderSearchHistory();
            return;
        }

        if (!searchHistory.includes(query)) {
            searchHistory.unshift(query);
            if (searchHistory.length > 10) searchHistory.pop();
            localStorage.setItem('nyx_search_history', JSON.stringify(searchHistory));
        }

        const usersRes = await fetch(`/search_users?query=${query}&username=${currentUser}`, { headers: getVKHeaders() });
        const users = await usersRes.json();

        const channelsRes = await fetch(`/search_channels?query=${query}&username=${currentUser}`, { headers: getVKHeaders() });
        const channels = await channelsRes.json();

        let html = '';

        if (users.length > 0) {
            html += `<div class="history-title">👤 Пользователи</div>`;
            users.forEach(u => {
                html += `
                    <div class="search-item" onclick="startChat('${u.username}'); closeSearchMenu();">
                        <div class="icon">${u.online ? '🟢' : '👤'}</div>
                        <div class="info">
                            <div class="name">${u.username}</div>
                            <div class="sub">${u.emoji || '𖤐'} • ${u.premium ? 'Premium' : 'Пользователь'}</div>
                        </div>
                        <div class="badge">${u.online ? 'Онлайн' : 'Оффлайн'}</div>
                    </div>
                `;
            });
        }

        if (channels.length > 0) {
            html += `<div class="history-title">📢 Каналы</div>`;
            channels.forEach(ch => {
                html += `
                    <div class="search-item" onclick="openChat('${ch.channel_id}', '${ch.title}'); closeSearchMenu();">
                        <div class="icon">📢</div>
                        <div class="info">
                            <div class="name">${ch.title}</div>
                            <div class="sub">@${ch.username} • ${ch.subscribers} подписчиков</div>
                        </div>
                        <div class="badge">${ch.admin === currentUser ? 'Владелец' : ''}</div>
                    </div>
                `;
            });
        }

        if (!html) {
            html = `<div style="padding:20px;text-align:center;color:#667788;">Ничего не найдено</div>`;
        }

        resultsContainer.innerHTML = html;
        document.getElementById('searchHistory').innerHTML = '';
    }

    function renderSearchHistory() {
        const container = document.getElementById('searchHistory');
        if (searchHistory.length === 0) {
            container.innerHTML = `<div style="padding:16px;text-align:center;color:#667788;font-size:13px;">Нет истории поиска</div>`;
            return;
        }
        container.innerHTML = `
            <div class="history-title">🕐 Недавние запросы</div>
            ${searchHistory.map(q => `
                <div class="search-item" onclick="document.getElementById('searchInputGlobal').value='${q}'; globalSearch();">
                    <div class="icon">🔍</div>
                    <div class="info">
                        <div class="name">${q}</div>
                    </div>
                    <div class="badge" onclick="event.stopPropagation(); removeFromHistory('${q}')" style="cursor:pointer;">✕</div>
                </div>
            `).join('')}
        `;
    }

    function removeFromHistory(query) {
        searchHistory = searchHistory.filter(q => q !== query);
        localStorage.setItem('nyx_search_history', JSON.stringify(searchHistory));
        renderSearchHistory();
    }

    function clearSearch() {
        document.getElementById('searchInputGlobal').value = '';
        document.getElementById('searchResultsGlobal').innerHTML = '';
        renderSearchHistory();
    }

    // ---- НАЧАТЬ ЧАТ (ИСПРАВЛЕНО) ----
    async function startChat(username) {
        if (!username || username === currentUser) {
            alert('Нельзя создать чат с самим собой');
            return;
        }
        try {
            const res = await fetch('/create_chat', {
                method: 'POST',
                headers: getVKHeaders(),
                body: JSON.stringify({target_username: username, username: currentUser})
            });
            const data = await res.json();
            if (data.chat_id) {
                openChat(data.chat_id, username);
                loadChats();
                closeSearchMenu();
            } else {
                alert('Ошибка: ' + (data.detail || 'Не удалось создать чат'));
            }
        } catch(e) {
            alert('Ошибка сети');
        }
    }

    // ---- ПРОФИЛЬ ПОЛЬЗОВАТЕЛЯ (ИСПРАВЛЕНО) ----
    async function viewProfile() {
        const title = document.getElementById('chatName').textContent;
        if (!title || title === 'Выберите чат') return;

        const res = await fetch(`/profile/${title}?viewer=${currentUser}`, { headers: getVKHeaders() });
        const data = await res.json();

        const modal = document.createElement('div');
        modal.className = 'modal-overlay';
        modal.innerHTML = `
            <div class="modal-content">
                <button class="modal-close" onclick="this.parentElement.parentElement.remove()">✕</button>
                <div style="text-align:center;">
                    <div style="font-size:64px;margin-bottom:8px;">${data.avatar ? `<img src="${data.avatar}" style="width:80px;height:80px;border-radius:50%;">` : '👤'}</div>
                    <div style="font-size:22px;font-weight:600;">${data.username}</div>
                    <div style="font-size:14px;color:#8899bb;margin:4px 0;">
                        ${data.emoji_status || '𖤐'} ${data.role === 'admin' ? '👑 Администратор' : '👤 Пользователь'}
                    </div>
                    <div style="font-size:14px;color:#8899bb;margin:4px 0;">
                        ${data.is_online ? '🟢 Онлайн' : '⚪ Оффлайн'}
                        ${data.last_seen ? ' • был ' + formatTime(data.last_seen) : ''}
                    </div>
                    ${data.bio ? `<div style="font-size:13px;color:#8899bb;margin:8px 0;padding:8px;background:rgba(255,255,255,0.02);border-radius:8px;">${escapeHtml(data.bio)}</div>` : ''}
                    ${data.is_owner && !data.is_current_user && data.role !== 'admin' ? `
                        <button onclick="makeAdmin('${data.username}')" style="margin-top:8px;padding:8px 24px;background:linear-gradient(135deg,#00ff9d,#00cc7d);color:#0a0d14;border:none;border-radius:8px;cursor:pointer;font-size:14px;font-weight:500;">
                            👑 Сделать администратором
                        </button>
                    ` : ''}
                    ${data.is_owner && !data.is_current_user && data.role === 'admin' ? `
                        <button onclick="removeAdmin('${data.username}')" style="margin-top:8px;padding:8px 24px;background:rgba(255,71,87,0.1);color:#ff4757;border:1px solid rgba(255,71,87,0.1);border-radius:8px;cursor:pointer;font-size:14px;font-weight:500;">
                            ⚡ Снять администратора
                        </button>
                    ` : ''}
                </div>
            </div>
        `;
        document.body.appendChild(modal);
    }

    async function makeAdmin(username) {
        if (!confirm(`Назначить ${username} администратором системы?`)) return;
        const res = await fetch('/make_admin', {
            method: 'POST',
            headers: getVKHeaders(),
            body: JSON.stringify({target_username: username, username: currentUser})
        });
        const data = await res.json();
        if (data.status === 'ok') {
            alert('✅ ' + data.message);
            document.querySelector('.modal-overlay')?.remove();
        } else {
            alert('❌ ' + (data.message || 'Ошибка'));
        }
    }

    async function removeAdmin(username) {
        if (!confirm(`Снять администратора ${username}?`)) return;
        const res = await fetch('/remove_admin', {
            method: 'POST',
            headers: getVKHeaders(),
            body: JSON.stringify({target_username: username, username: currentUser})
        });
        const data = await res.json();
        if (data.status === 'ok') {
            alert('✅ ' + data.message);
            document.querySelector('.modal-overlay')?.remove();
        } else {
            alert('❌ ' + (data.message || 'Ошибка'));
        }
    }

    // ---- АДМИН ----
    async function loadAdminStats() {
        if (!isAdmin) return;
        try {
            const res = await fetch(`/admin/stats?username=${currentUser}`, { headers: getVKHeaders() });
            const data = await res.json();
            // Показываем в консоли, в этом layout админка не отображается
            console.log('📊 Админ-статистика:', data);
        } catch(e) { console.error('Ошибка загрузки админ-статистики:', e); }
    }

    function switchTab(tab) {
        // В этом layout нет вкладок, просто заглушка
        if (tab === 'admin' && isAdmin) loadAdminStats();
    }

    // ---- ВЫХОД ----
    function logout() {
        if (ws) ws.close();
        if (adminStatsInterval) clearInterval(adminStatsInterval);
        localStorage.clear();
        location.reload();
    }

    window.onload = function() {
        // ПОЛЯ ПУСТЫЕ — АВТО-ВХОДА НЕТ
    };
</script>
</body>
</html>"""

if __name__ == "__main__":
    port = int(os.getenv("PORT", 8000))
    asyncio.create_task(cleanup_expired_stories())
    uvicorn.run(app, host="0.0.0.0", port=port)
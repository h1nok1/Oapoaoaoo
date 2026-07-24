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
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.asymmetric import rsa, padding
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.backends import default_backend

# ----- ОТКЛЮЧАЕМ ЛОГИРОВАНИЕ IP -----
logging.getLogger("uvicorn.access").handlers = []
logging.getLogger("uvicorn.access").propagate = False

app = FastAPI(title="Delta - Fsociety Edition")

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
conn = sqlite3.connect("delta_tg_clone.db", check_same_thread=False)
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
    ok_id TEXT DEFAULT '',
    public_key TEXT DEFAULT '',
    private_key_encrypted TEXT DEFAULT ''
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
    encrypted_text TEXT,
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

# История звонков
c.execute('''CREATE TABLE IF NOT EXISTS call_history (
    id TEXT PRIMARY KEY,
    caller TEXT NOT NULL,
    callee TEXT NOT NULL,
    duration INTEGER DEFAULT 0,
    call_type TEXT DEFAULT 'outgoing',
    status TEXT DEFAULT 'missed',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)''')

# Муты и баны
c.execute('''CREATE TABLE IF NOT EXISTS chat_bans (
    id TEXT PRIMARY KEY,
    chat_id TEXT NOT NULL,
    username TEXT NOT NULL,
    type TEXT DEFAULT 'mute',
    expires_at TIMESTAMP DEFAULT NULL,
    reason TEXT DEFAULT '',
    created_by TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(chat_id, username)
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

# ----- ФУНКЦИИ ХЕШИРОВАНИЯ И КЛЮЧЕЙ -----
def hash_password(password: str, salt: str = None) -> tuple:
    if not salt:
        salt = secrets.token_hex(16)
    h = hashlib.sha256((salt + password).encode()).hexdigest()
    return h, salt

def generate_key_pair():
    private_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
        backend=default_backend()
    )
    public_key = private_key.public_key()
    return private_key, public_key

# ----- ПРИНУДИТЕЛЬНАЯ УСТАНОВКА РОЛИ OWNER ДЛЯ seconddurov -----
def ensure_owner():
    c.execute("SELECT role FROM users WHERE username = 'seconddurov'")
    row = c.fetchone()
    if row:
        if row[0] != "owner":
            c.execute("UPDATE users SET role = 'owner' WHERE username = 'seconddurov'")
            conn.commit()
            print("✅ Роль seconddurov обновлена на 'owner'")
    else:
        pwd_hash, salt = hash_password("020112")
        user_id = str(uuid.uuid4())
        priv, pub = generate_key_pair()
        pub_pem = pub.public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo
        ).decode()
        c.execute("""INSERT INTO users
                     (id, username, password_hash, salt, premium, profile_emoji, role, nft_collection, bio, vk_id, phone, public_key)
                     VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                  (user_id, "seconddurov", pwd_hash, salt, 1, "👑", "owner",
                   json.dumps(["Корона Даркнета"]), "Владыка подполья", str(random.randint(100000000, 999999999)), "+1 343 438 5452", pub_pem))
        conn.commit()
        print("✅ Аккаунт seconddurov (OWNER) создан")

ensure_owner()

# ----- МЕНЕДЖЕР ВЕБСОКЕТОВ -----
class ConnectionManager:
    def __init__(self):
        self.active: Dict[str, WebSocket] = {}
        self.vk_polling: Dict[str, asyncio.Queue] = {}
        self.typing: Dict[str, Dict[str, bool]] = {}
        self.read_status: Dict[str, Dict[str, int]] = {}
        self.calls: Dict[str, Dict] = {}

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
def get_user_by_username(username: str):
    c.execute("SELECT id, username, password_hash, salt, premium, nft_collection, profile_emoji, role, avatar_path, device_id, blocked_users, last_seen, is_online, bio, created_at, vk_id, ok_id, phone, public_key FROM users WHERE username = ?", (username,))
    return c.fetchone()

def get_chat(chat_id: str):
    c.execute("SELECT * FROM chats WHERE id = ?", (chat_id,))
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

def is_banned(chat_id: str, username: str):
    c.execute("SELECT * FROM chat_bans WHERE chat_id = ? AND username = ? AND type = 'ban' AND (expires_at IS NULL OR expires_at > datetime('now'))", (chat_id, username))
    return c.fetchone() is not None

def is_muted(chat_id: str, username: str):
    c.execute("SELECT * FROM chat_bans WHERE chat_id = ? AND username = ? AND type = 'mute' AND (expires_at IS NULL OR expires_at > datetime('now'))", (chat_id, username))
    return c.fetchone() is not None

def is_owner(username: str) -> bool:
    user = get_user_by_username(username)
    return username == "seconddurov" or (user and user[7] == "owner")

def is_admin(username: str) -> bool:
    user = get_user_by_username(username)
    return user and user[7] in ["owner", "admin"]

# ----- ОСНОВНЫЕ ЭНДПОИНТЫ -----
@app.post("/register")
async def register_user(username: str = Form(...), password: str = Form(""), device_id: str = Form("")):
    if not username.isalnum() and '_' not in username:
        raise HTTPException(400, "Invalid username")
    if get_user_by_username(username):
        raise HTTPException(400, "User already exists")

    if device_id:
        c.execute("SELECT COUNT(*) FROM users WHERE device_id = ?", (device_id,))
        if c.fetchone()[0] >= 5:
            raise HTTPException(400, "Max 5 accounts per device")

    user_id = str(uuid.uuid4())
    if password:
        pwd_hash, salt = hash_password(password)
    else:
        pwd_hash, salt = hash_password(secrets.token_hex(8))

    priv, pub = generate_key_pair()
    pub_pem = pub.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo
    ).decode()

    c.execute("""INSERT INTO users
                 (id, username, password_hash, salt, profile_emoji, role, device_id, public_key)
                 VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
              (user_id, username, pwd_hash, salt, "𖤐", "user", device_id or "", pub_pem))
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
        "vk_id": user[15] or "",
        "phone": user[17] or ""
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
        "is_current_user": (viewer == username),
        "phone": user[17] or "",
        "vk_id": user[15] or ""
    }

@app.post("/make_admin")
async def make_admin(target_username: str, username: str):
    if not is_owner(username):
        raise HTTPException(403, "Доступ запрещен. Только владелец системы (seconddurov) может назначать админов")

    target = get_user_by_username(target_username)
    if not target:
        raise HTTPException(404, "Пользователь не найден")

    if target[7] in ["owner", "admin"]:
        return {"status": "already_admin", "message": f"{target_username} уже является администратором"}

    c.execute("UPDATE users SET role = 'admin' WHERE username = ?", (target_username,))
    conn.commit()

    await manager.send_to_user(target_username, {
        "event": "promoted_to_admin",
        "message": "Поздравляем! Теперь вы администратор DELTA. Вам доступна админ-панель."
    })

    return {"status": "ok", "message": f"{target_username} теперь администратор"}

@app.post("/remove_admin")
async def remove_admin(target_username: str, username: str):
    if not is_owner(username):
        raise HTTPException(403, "Доступ запрещен. Только владелец системы (seconddurov) может снимать админов")

    if target_username == "seconddurov":
        raise HTTPException(400, "Нельзя снять права администратора с владельца системы")

    target = get_user_by_username(target_username)
    if not target:
        raise HTTPException(404, "Пользователь не найден")

    if target[7] != "admin":
        return {"status": "not_admin", "message": f"{target_username} не является администратором"}

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

@app.post("/create_group")
async def create_group(title: str, participants: str, username: str):
    user = get_user_by_username(username)
    if not user:
        raise HTTPException(404, "User not found")

    parts = [p.strip() for p in participants.split(',') if p.strip()]
    if not parts:
        raise HTTPException(400, "No participants specified")

    if username not in parts:
        parts.append(username)

    if len(parts) < 2:
        raise HTTPException(400, "At least 2 participants required")

    chat_id = str(uuid.uuid4())
    c.execute("""INSERT INTO chats
                 (id, type, title, participants, admin_username)
                 VALUES (?, ?, ?, ?, ?)""",
              (chat_id, "group", title, json.dumps(parts), username))
    conn.commit()

    await manager.broadcast_to_chat(chat_id, {
        "event": "new_group",
        "chat_id": chat_id,
        "title": title
    }, [username])

    return {"chat_id": chat_id, "title": title}

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

# ----- БАНЫ И МУТЫ -----
@app.post("/chat/ban")
async def ban_user(chat_id: str, target_username: str, username: str, duration: int = None, reason: str = ""):
    chat = get_chat(chat_id)
    if not chat:
        raise HTTPException(404, "Chat not found")
    
    if chat[5] != username and not is_owner(username):
        raise HTTPException(403, "Only chat admin can ban users")
    
    target = get_user_by_username(target_username)
    if not target:
        raise HTTPException(404, "User not found")
    
    if target_username == username:
        raise HTTPException(400, "Cannot ban yourself")
    
    expires = None
    if duration:
        expires = (datetime.now() + timedelta(seconds=duration)).isoformat()
    
    c.execute("INSERT OR REPLACE INTO chat_bans (id, chat_id, username, type, expires_at, reason, created_by) VALUES (?, ?, ?, ?, ?, ?, ?)",
              (str(uuid.uuid4()), chat_id, target_username, "ban", expires, reason, username))
    conn.commit()
    
    return {"status": "banned", "expires": expires}

@app.post("/chat/mute")
async def mute_user(chat_id: str, target_username: str, username: str, duration: int = None, reason: str = ""):
    chat = get_chat(chat_id)
    if not chat:
        raise HTTPException(404, "Chat not found")
    
    if chat[5] != username and not is_owner(username):
        raise HTTPException(403, "Only chat admin can mute users")
    
    target = get_user_by_username(target_username)
    if not target:
        raise HTTPException(404, "User not found")
    
    if target_username == username:
        raise HTTPException(400, "Cannot mute yourself")
    
    expires = None
    if duration:
        expires = (datetime.now() + timedelta(seconds=duration)).isoformat()
    
    c.execute("INSERT OR REPLACE INTO chat_bans (id, chat_id, username, type, expires_at, reason, created_by) VALUES (?, ?, ?, ?, ?, ?, ?)",
              (str(uuid.uuid4()), chat_id, target_username, "mute", expires, reason, username))
    conn.commit()
    
    return {"status": "muted", "expires": expires}

@app.post("/chat/unban")
async def unban_user(chat_id: str, target_username: str, username: str):
    chat = get_chat(chat_id)
    if not chat:
        raise HTTPException(404, "Chat not found")
    
    if chat[5] != username and not is_owner(username):
        raise HTTPException(403, "Only chat admin can unban users")
    
    c.execute("DELETE FROM chat_bans WHERE chat_id = ? AND username = ?", (chat_id, target_username))
    conn.commit()
    
    return {"status": "unbanned"}

# ----- ИСТОРИЯ ЗВОНКОВ -----
@app.post("/call/log")
async def log_call(caller: str, callee: str, duration: int = 0, call_type: str = "outgoing", status: str = "completed"):
    call_id = str(uuid.uuid4())
    c.execute("INSERT INTO call_history (id, caller, callee, duration, call_type, status) VALUES (?, ?, ?, ?, ?, ?)",
              (call_id, caller, callee, duration, call_type, status))
    conn.commit()
    return {"call_id": call_id}

@app.get("/call/history/{username}")
async def get_call_history(username: str):
    c.execute("""SELECT id, caller, callee, duration, call_type, status, created_at
                 FROM call_history
                 WHERE caller = ? OR callee = ?
                 ORDER BY created_at DESC
                 LIMIT 100""", (username, username))
    calls = c.fetchall()
    return [{
        "id": c[0],
        "caller": c[1],
        "callee": c[2],
        "duration": c[3],
        "type": c[4],
        "status": c[5],
        "time": c[6]
    } for c in calls]

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

# ----- АДМИН ЭНДПОИНТЫ (только для owner и admin) -----
@app.get("/admin/stats")
async def admin_stats(username: str):
    user = get_user_by_username(username)
    if not user or user[7] not in ["owner", "admin"]:
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

    c.execute("SELECT COUNT(*) FROM call_history")
    total_calls = c.fetchone()[0]

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
        "total_calls": total_calls,
        "server_time": datetime.now().isoformat()
    }

@app.get("/admin/users_list")
async def admin_users_list(username: str, limit: int = 50, offset: int = 0):
    user = get_user_by_username(username)
    if not user or user[7] not in ["owner", "admin"]:
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
    if not user or user[7] not in ["owner", "admin"]:
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

                if is_banned(chat_id, username):
                    await websocket.send_json({"event": "error", "message": "Вы забанены в этом чате"})
                    continue
                if is_muted(chat_id, username):
                    await websocket.send_json({"event": "error", "message": "Вы заглушены в этом чате"})
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

            elif event_type == "call":
                call_type = data.get("call_type")
                target = data.get("target")

                if call_type == "offer":
                    await manager.send_to_user(target, {
                        "event": "incoming_call",
                        "from": username,
                        "offer": data.get("offer"),
                        "call_id": data.get("call_id")
                    })
                elif call_type == "answer":
                    await manager.send_to_user(target, {
                        "event": "call_answer",
                        "from": username,
                        "answer": data.get("answer"),
                        "call_id": data.get("call_id")
                    })
                elif call_type == "ice":
                    await manager.send_to_user(target, {
                        "event": "call_ice",
                        "from": username,
                        "candidate": data.get("candidate"),
                        "call_id": data.get("call_id")
                    })
                elif call_type == "hangup":
                    await manager.send_to_user(target, {
                        "event": "call_hangup",
                        "from": username,
                        "call_id": data.get("call_id")
                    })
                    await log_call(username, target, 0, "outgoing", "completed")
                elif call_type == "busy":
                    await manager.send_to_user(target, {
                        "event": "call_busy",
                        "from": username,
                        "call_id": data.get("call_id")
                    })
                    await log_call(username, target, 0, "outgoing", "missed")

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

# ----- ГЛАВНАЯ СТРАНИЦА (ТОЧНАЯ КОПИЯ ТВОЕГО ДИЗАЙНА) -----
@app.get("/", response_class=HTMLResponse)
async def get_index():
    return """<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <title>DELTA</title>
    <style>
        /* ===== ТОЧНАЯ КОПИЯ ТВОЕГО ДИЗАЙНА ===== */
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: 'Segoe UI', 'Arial', sans-serif;
            background: #05080f;
            color: #ffffff;
            height: 100vh;
            overflow: hidden;
            display: flex;
            justify-content: center;
            align-items: center;
        }

        #app {
            width: 100%;
            max-width: 420px;
            height: 100vh;
            max-height: 850px;
            background: #08111f;
            display: flex;
            flex-direction: column;
            overflow: hidden;
            position: relative;
            border-radius: 0;
        }
        @media (min-width: 480px) {
            #app { border-radius: 24px; height: 95vh; }
        }

        /* ===== АВТОРИЗАЦИЯ (ТВОЙ СТИЛЬ) ===== */
        #authScreen {
            display: flex;
            flex-direction: column;
            justify-content: center;
            align-items: center;
            flex: 1;
            padding: 40px 28px;
            gap: 12px;
            position: relative;
        }

        #authScreen .logo {
            font-size: 48px;
            margin-bottom: 4px;
        }

        #authScreen .app-name {
            font-size: 32px;
            font-weight: 700;
            color: #3ea6ff;
            font-family: 'Segoe UI', sans-serif;
        }

        #authScreen .app-slogan {
            font-size: 11px;
            color: rgba(255,255,255,0.2);
            letter-spacing: 2px;
            margin-bottom: 16px;
        }

        #authScreen input {
            width: 100%;
            padding: 14px 18px;
            background: rgba(255,255,255,0.04);
            border: 1px solid rgba(255,255,255,0.06);
            border-radius: 12px;
            color: #ffffff;
            font-size: 14px;
            outline: none;
            transition: all 0.3s ease;
        }

        #authScreen input:focus {
            border-color: #3ea6ff;
            background: rgba(255,255,255,0.06);
        }

        #authScreen input::placeholder {
            color: rgba(255,255,255,0.15);
        }

        #authScreen button {
            width: 100%;
            padding: 14px;
            background: #0d1d33;
            border: 1px solid #1f8fff;
            border-radius: 12px;
            color: #ffffff;
            font-size: 14px;
            font-weight: 600;
            cursor: pointer;
            transition: all 0.3s ease;
            letter-spacing: 1px;
        }

        #authScreen button:hover {
            background: #12223d;
            border-color: #3ea6ff;
            transform: scale(1.01);
        }

        #authError {
            color: #ff4757;
            font-size: 12px;
            text-align: center;
            margin-top: 6px;
        }

        /* ===== ОСНОВНОЙ ИНТЕРФЕЙС ===== */
        #mainScreen {
            display: none;
            flex-direction: column;
            flex: 1;
            height: 100%;
        }

        /* ----- ХЕДЕР (ТВОЙ СТИЛЬ) ----- */
        .header {
            background: #0d1d33;
            padding: 12px 18px;
            border-bottom: 1px solid rgba(255,255,255,0.03);
            display: flex;
            align-items: center;
            justify-content: space-between;
            flex-shrink: 0;
            height: 60px;
        }

        .header .logo {
            font-size: 22px;
            font-weight: 700;
            color: #3ea6ff;
        }

        .header .right {
            display: flex;
            align-items: center;
            gap: 8px;
        }

        .header .right .btn {
            background: rgba(255,255,255,0.04);
            border: 1px solid rgba(255,255,255,0.06);
            border-radius: 10px;
            width: 34px;
            height: 34px;
            color: rgba(255,255,255,0.3);
            font-size: 14px;
            cursor: pointer;
            transition: all 0.3s ease;
            display: flex;
            align-items: center;
            justify-content: center;
        }

        .header .right .btn:hover {
            background: rgba(255,255,255,0.08);
            color: #ffffff;
        }

        /* ----- ПОИСК (ТВОЙ СТИЛЬ) ----- */
        .search-input {
            padding: 8px 16px;
            flex-shrink: 0;
        }

        .search-input input {
            width: 100%;
            padding: 10px 16px;
            background: rgba(255,255,255,0.04);
            border: 1px solid rgba(255,255,255,0.06);
            border-radius: 12px;
            color: #ffffff;
            font-size: 13px;
            outline: none;
            transition: all 0.3s ease;
        }

        .search-input input:focus {
            border-color: #3ea6ff;
            background: rgba(255,255,255,0.06);
        }

        .search-input input::placeholder {
            color: rgba(255,255,255,0.15);
        }

        /* ----- СПИСОК ЧАТОВ (ТВОЙ СТИЛЬ) ----- */
        .chat-list {
            flex: 1;
            overflow-y: auto;
            padding: 8px 12px;
        }
        .chat-list::-webkit-scrollbar { width: 3px; }
        .chat-list::-webkit-scrollbar-thumb { background: rgba(255,255,255,0.04); border-radius: 4px; }

        /* ТОЧНО ТАКАЯ ЖЕ КАРТОЧКА, КАК В ТВОЁМ ДИЗАЙНЕ */
        .chat-item {
            background: #12223d;
            border-radius: 15px;
            padding: 12px 16px;
            margin-bottom: 8px;
            cursor: pointer;
            transition: all 0.3s ease;
            border: 1px solid rgba(255,255,255,0.02);
        }

        .chat-item:hover {
            background: #16284a;
            transform: translateX(4px);
            border-color: rgba(255,255,255,0.04);
        }

        .chat-item .name {
            font-size: 16px;
            font-weight: 600;
            color: #ffffff;
        }

        .chat-item .last-msg {
            font-size: 13px;
            color: rgba(255,255,255,0.3);
            margin-top: 2px;
        }

        .chat-item .time {
            font-size: 11px;
            color: rgba(255,255,255,0.1);
            float: right;
        }

        /* ----- КНОПКА "+ НОВЫЙ ЧАТ" (ТВОЙ СТИЛЬ) ----- */
        .new-chat-btn {
            background: #0d1d33;
            border: 1px solid #1f8fff;
            border-radius: 20px;
            padding: 14px;
            margin: 0 12px 12px 12px;
            color: #ffffff;
            font-size: 14px;
            font-weight: 600;
            cursor: pointer;
            transition: all 0.3s ease;
            text-align: center;
            letter-spacing: 0.5px;
        }

        .new-chat-btn:hover {
            background: #12223d;
            border-color: #3ea6ff;
            transform: translateY(-1px);
        }

        /* ===== ЧАТ (ПОВЕРХ) ===== */
        #chatView {
            display: none;
            flex-direction: column;
            flex: 1;
            background: #08111f;
            position: absolute;
            top: 0;
            left: 0;
            right: 0;
            bottom: 0;
            z-index: 100;
            border-radius: inherit;
        }

        .chat-header {
            background: #0d1d33;
            padding: 12px 16px;
            border-bottom: 1px solid rgba(255,255,255,0.03);
            display: flex;
            align-items: center;
            flex-shrink: 0;
            height: 60px;
        }

        .chat-header .back {
            background: none;
            border: none;
            font-size: 24px;
            cursor: pointer;
            color: rgba(255,255,255,0.3);
            padding: 0 8px;
        }

        .chat-header .back:hover {
            color: #ffffff;
        }

        .chat-header .title {
            flex: 1;
            font-weight: 600;
            font-size: 16px;
            margin-left: 8px;
            color: #ffffff;
        }

        .chat-header .actions {
            display: flex;
            gap: 6px;
        }

        .chat-header .actions button {
            background: none;
            border: none;
            color: rgba(255,255,255,0.2);
            font-size: 16px;
            cursor: pointer;
            padding: 4px 6px;
        }

        .chat-header .actions button:hover {
            color: #ffffff;
        }

        .messages-container {
            flex: 1;
            overflow-y: auto;
            padding: 16px;
            display: flex;
            flex-direction: column;
        }

        .messages-container::-webkit-scrollbar { width: 3px; }
        .messages-container::-webkit-scrollbar-thumb { background: rgba(255,255,255,0.04); border-radius: 4px; }

        .msg {
            max-width: 80%;
            padding: 8px 14px;
            border-radius: 14px;
            margin-bottom: 4px;
            word-wrap: break-word;
            font-size: 13px;
            line-height: 1.5;
            animation: fadeIn 0.2s ease;
        }

        .msg.self {
            align-self: flex-end;
            background: #1f8fff;
            color: #ffffff;
            border-bottom-right-radius: 4px;
        }

        .msg.other {
            align-self: flex-start;
            background: #12223d;
            color: rgba(255,255,255,0.7);
            border-bottom-left-radius: 4px;
        }

        .msg .sender {
            font-size: 11px;
            font-weight: 600;
            color: #3ea6ff;
            margin-bottom: 2px;
        }

        .msg .time {
            font-size: 9px;
            color: rgba(255,255,255,0.1);
            margin-top: 4px;
            text-align: right;
        }

        .msg .reactions {
            display: flex;
            gap: 4px;
            margin-top: 4px;
            flex-wrap: wrap;
        }

        .msg .reactions span {
            background: rgba(255,255,255,0.04);
            padding: 1px 6px;
            border-radius: 8px;
            font-size: 10px;
            cursor: pointer;
        }

        .msg .reactions span:hover {
            background: rgba(255,255,255,0.08);
        }

        .msg-input-area {
            display: flex;
            align-items: center;
            padding: 8px 12px;
            background: #0d1d33;
            border-top: 1px solid rgba(255,255,255,0.03);
            gap: 8px;
            flex-shrink: 0;
        }

        .msg-input-area input {
            flex: 1;
            padding: 10px 16px;
            background: rgba(255,255,255,0.04);
            border: 1px solid rgba(255,255,255,0.06);
            border-radius: 20px;
            color: #ffffff;
            font-size: 13px;
            outline: none;
            transition: all 0.3s ease;
        }

        .msg-input-area input:focus {
            border-color: #3ea6ff;
            background: rgba(255,255,255,0.06);
        }

        .msg-input-area input::placeholder {
            color: rgba(255,255,255,0.1);
        }

        .msg-input-area .send-btn {
            background: #1f8fff;
            color: #ffffff;
            border: none;
            border-radius: 50%;
            width: 36px;
            height: 36px;
            display: flex;
            align-items: center;
            justify-content: center;
            cursor: pointer;
            font-size: 16px;
            flex-shrink: 0;
            transition: all 0.3s ease;
        }

        .msg-input-area .send-btn:hover {
            background: #3ea6ff;
            transform: scale(1.05);
        }

        .msg-input-area .action-btn {
            background: rgba(255,255,255,0.04);
            border: 1px solid rgba(255,255,255,0.06);
            border-radius: 50%;
            width: 36px;
            height: 36px;
            color: rgba(255,255,255,0.2);
            cursor: pointer;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 16px;
            transition: all 0.3s ease;
            flex-shrink: 0;
        }

        .msg-input-area .action-btn:hover {
            background: rgba(255,255,255,0.08);
            color: #ffffff;
        }

        /* ===== ПРОФИЛЬ ===== */
        .profile-content {
            padding: 20px 16px;
            text-align: center;
        }

        .profile-avatar {
            width: 80px;
            height: 80px;
            border-radius: 50%;
            background: rgba(255,255,255,0.04);
            border: 1px solid rgba(255,255,255,0.06);
            margin: 0 auto;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 32px;
            overflow: hidden;
            cursor: pointer;
            transition: all 0.3s ease;
        }

        .profile-avatar:hover {
            border-color: #3ea6ff;
        }

        .profile-avatar img {
            width: 100%;
            height: 100%;
            object-fit: cover;
        }

        .profile-name {
            font-size: 20px;
            font-weight: 600;
            margin-top: 10px;
            color: #ffffff;
        }

        .profile-name .sub {
            font-size: 12px;
            color: rgba(255,255,255,0.2);
            font-weight: 400;
            display: block;
            margin-top: 2px;
        }

        .profile-bio {
            font-size: 13px;
            color: rgba(255,255,255,0.2);
            margin: 8px 0;
            padding: 6px 16px;
            background: rgba(255,255,255,0.04);
            border-radius: 8px;
            display: inline-block;
        }

        .profile-accounts {
            margin: 12px 0;
            text-align: left;
        }

        .profile-accounts .account-item {
            display: flex;
            align-items: center;
            justify-content: space-between;
            padding: 8px 12px;
            background: rgba(255,255,255,0.04);
            border-radius: 10px;
            margin-bottom: 4px;
            border: 1px solid rgba(255,255,255,0.04);
            cursor: pointer;
            transition: all 0.3s ease;
        }

        .profile-accounts .account-item:hover {
            background: rgba(255,255,255,0.06);
        }

        .profile-accounts .account-item .acc-name {
            font-size: 13px;
            color: rgba(255,255,255,0.4);
        }

        .profile-accounts .account-item .acc-status {
            font-size: 10px;
            color: rgba(255,255,255,0.1);
        }

        .profile-accounts .account-item .acc-switch {
            font-size: 11px;
            color: #3ea6ff;
            background: rgba(62, 166, 255, 0.04);
            padding: 2px 10px;
            border-radius: 8px;
            border: 1px solid rgba(62, 166, 255, 0.04);
        }

        .profile-actions {
            margin-top: 12px;
            display: flex;
            flex-direction: column;
            gap: 6px;
        }

        .profile-actions button {
            padding: 10px;
            background: rgba(255,255,255,0.04);
            border: 1px solid rgba(255,255,255,0.04);
            border-radius: 10px;
            color: rgba(255,255,255,0.2);
            font-size: 12px;
            cursor: pointer;
            transition: all 0.3s ease;
            text-align: center;
        }

        .profile-actions button:hover {
            background: rgba(255,255,255,0.06);
            color: #ffffff;
        }

        .profile-actions .add-account-btn {
            color: #3ea6ff;
            border-color: rgba(62, 166, 255, 0.04);
        }

        .profile-actions .add-account-btn:hover {
            background: rgba(62, 166, 255, 0.04);
        }

        .profile-actions .admin-btn {
            color: #3ea6ff;
            border-color: rgba(62, 166, 255, 0.04);
        }

        .profile-actions .admin-btn:hover {
            background: rgba(62, 166, 255, 0.04);
        }

        .profile-actions .logout-btn {
            color: #ff4757;
            border-color: rgba(255, 71, 87, 0.04);
        }

        .profile-actions .logout-btn:hover {
            background: rgba(255, 71, 87, 0.04);
        }

        /* ===== ВКЛАДКИ (ПРОСТЫЕ) ===== */
        .tabs {
            display: flex;
            background: #0d1d33;
            border-top: 1px solid rgba(255,255,255,0.03);
            flex-shrink: 0;
        }

        .tabs button {
            flex: 1;
            padding: 10px;
            background: transparent;
            border: none;
            color: rgba(255,255,255,0.2);
            font-size: 12px;
            font-weight: 500;
            cursor: pointer;
            transition: all 0.3s ease;
            border-bottom: 2px solid transparent;
        }

        .tabs button.active {
            color: #3ea6ff;
            border-bottom-color: #3ea6ff;
        }

        .tabs button:hover {
            color: #ffffff;
        }

        .tab-content {
            flex: 1;
            display: none;
            flex-direction: column;
            overflow: hidden;
        }

        .tab-content.active {
            display: flex;
        }

        /* ----- ИСТОРИИ (СТРОКА) ----- */
        .story-row {
            display: flex;
            gap: 12px;
            padding: 10px 16px;
            overflow-x: auto;
            flex-shrink: 0;
            border-bottom: 1px solid rgba(255,255,255,0.02);
        }

        .story-row::-webkit-scrollbar { height: 2px; }
        .story-row::-webkit-scrollbar-thumb { background: rgba(255,255,255,0.04); border-radius: 4px; }

        .story-item {
            display: flex;
            flex-direction: column;
            align-items: center;
            cursor: pointer;
            flex-shrink: 0;
            gap: 4px;
        }

        .story-item .ring {
            width: 54px;
            height: 54px;
            border-radius: 50%;
            padding: 2px;
            background: rgba(255,255,255,0.02);
            border: 2px solid rgba(255,255,255,0.04);
            transition: all 0.3s ease;
        }

        .story-item .ring.active {
            border-color: #3ea6ff;
        }

        .story-item .ring.viewed {
            border-color: rgba(255,255,255,0.02);
            opacity: 0.3;
        }

        .story-item .ring .avatar {
            width: 100%;
            height: 100%;
            border-radius: 50%;
            background: rgba(255,255,255,0.02);
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 18px;
            overflow: hidden;
        }

        .story-item .name {
            font-size: 9px;
            color: rgba(255,255,255,0.1);
            max-width: 54px;
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
        }

        /* ===== СТИЛИ ДЛЯ ЧАСТИЦ (CANVAS) ===== */
        #particles-canvas {
            position: fixed;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            z-index: 0;
            pointer-events: none;
        }

        .hidden { display: none !important; }

        @keyframes fadeIn {
            from { opacity: 0; transform: scale(0.96); }
            to { opacity: 1; transform: scale(1); }
        }
    </style>
</head>
<body>
<div id="app">
    <canvas id="particles-canvas"></canvas>

    <!-- АВТОРИЗАЦИЯ -->
    <div id="authScreen">
        <div class="logo">💙</div>
        <div class="app-name">Delta</div>
        <div class="app-slogan">UNDERGROUND MESSENGER</div>
        <input id="loginUsername" placeholder="username">
        <input id="loginPassword" type="password" placeholder="password">
        <button onclick="login()">Войти</button>
        <div id="authError"></div>
    </div>

    <!-- ОСНОВНОЙ ИНТЕРФЕЙС -->
    <div id="mainScreen">
        <!-- ХЕДЕР -->
        <div class="header">
            <div class="logo">💙 Delta</div>
            <div class="right">
                <button class="btn" onclick="uploadStory()">📸</button>
                <button class="btn" onclick="openSearchMenu()">🔍</button>
            </div>
        </div>

        <!-- ПОИСК -->
        <div class="search-input">
            <input id="searchInput" placeholder="Поиск..." oninput="filterChats()">
        </div>

        <!-- ВКЛАДКИ -->
        <div class="tabs">
            <button class="active" data-tab="chats" onclick="switchTab('chats')">💬 Чаты</button>
            <button data-tab="profile" onclick="switchTab('profile')">👤 Профиль</button>
        </div>

        <!-- ВКЛАДКА: ЧАТЫ -->
        <div id="tab-chats" class="tab-content active">
            <div id="storyRow" class="story-row"></div>
            <div class="chat-list" id="chatList"></div>
            <div class="new-chat-btn" onclick="createChatDialog()">+ Новый чат</div>
        </div>

        <!-- ВКЛАДКА: ПРОФИЛЬ -->
        <div id="tab-profile" class="tab-content">
            <div class="profile-content" id="profileContent"></div>
        </div>
    </div>

    <!-- ЧАТ (ПОВЕРХ) -->
    <div id="chatView">
        <div class="chat-header">
            <button class="back" onclick="closeChat()">‹</button>
            <div class="title" id="chatName">Чат</div>
            <div class="actions">
                <button onclick="viewProfile()">👤</button>
            </div>
        </div>
        <div class="messages-container" id="messagesContainer">
            <div id="messagesList"></div>
        </div>
        <div class="msg-input-area">
            <button class="action-btn" onclick="document.getElementById('photoInput').click()">📷</button>
            <input id="msgInput" placeholder="Сообщение..." onkeydown="if(event.key==='Enter') sendMessage()">
            <button class="send-btn" onclick="sendMessage()">➤</button>
        </div>
        <input type="file" id="photoInput" accept="image/*" style="display:none" onchange="uploadPhoto(event)">
    </div>

    <!-- ПОИСК (ОВЕРЛЕЙ) -->
    <div id="searchMenu" class="hidden" style="position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(5,8,15,0.9);z-index:9999;padding:60px 20px;">
        <div style="max-width:420px;margin:0 auto;">
            <input id="searchInputGlobal" placeholder="Поиск пользователей..." style="width:100%;padding:12px 16px;background:rgba(255,255,255,0.04);border:1px solid rgba(255,255,255,0.06);border-radius:12px;color:#ffffff;font-size:14px;outline:none;">
            <div id="searchResultsGlobal" style="margin-top:12px;"></div>
            <button onclick="closeSearchMenu()" style="margin-top:12px;padding:10px;background:rgba(255,255,255,0.04);border:1px solid rgba(255,255,255,0.06);border-radius:12px;color:rgba(255,255,255,0.3);width:100%;cursor:pointer;">Закрыть</button>
        </div>
    </div>
</div>

<script>
    // ============================================================
    // ЧАСТИЦЫ (ТОЧНО КАК В ТВОЁМ ДИЗАЙНЕ)
    // ============================================================
    (function() {
        const canvas = document.getElementById('particles-canvas');
        const ctx = canvas.getContext('2d');
        let width, height;
        let particles = [];
        const PARTICLE_COUNT = 40;

        function resize() {
            width = canvas.width = window.innerWidth;
            height = canvas.height = window.innerHeight;
        }
        window.addEventListener('resize', resize);
        resize();

        class Particle {
            constructor() {
                this.reset();
            }
            reset() {
                this.x = Math.random() * width;
                this.y = Math.random() * height;
                this.size = Math.random() * 2 + 1;
                this.speedX = (Math.random() - 0.5) * 0.5;
                this.speedY = (Math.random() - 0.5) * 0.5;
                this.opacity = Math.random() * 0.3 + 0.05;
            }
            update() {
                this.x += this.speedX;
                this.y += this.speedY;
                if (this.x < 0 || this.x > width || this.y < 0 || this.y > height) {
                    this.reset();
                }
            }
            draw() {
                ctx.beginPath();
                ctx.arc(this.x, this.y, this.size, 0, Math.PI * 2);
                ctx.fillStyle = `rgba(62, 166, 255, ${this.opacity})`;
                ctx.fill();
            }
        }

        for (let i = 0; i < PARTICLE_COUNT; i++) {
            particles.push(new Particle());
        }

        function animateParticles() {
            ctx.clearRect(0, 0, width, height);
            particles.forEach(p => {
                p.update();
                p.draw();
            });
            requestAnimationFrame(animateParticles);
        }
        animateParticles();
    })();

    // ============================================================
    // ОСНОВНОЙ JS
    // ============================================================
    let currentUser = '';
    let currentChat = '';
    let currentChatTarget = '';
    let ws = null;
    let deviceId = localStorage.getItem('delta_device_id') || 'device_' + Date.now();
    localStorage.setItem('delta_device_id', deviceId);
    let isAdmin = false;
    let isOwner = false;
    let chatsData = [];
    let accounts = JSON.parse(localStorage.getItem('delta_accounts') || '[]');
    let storiesData = {};
    let currentStoryIndex = 0;
    let currentStoryUser = '';
    let storyViewerActive = false;
    let searchHistory = JSON.parse(localStorage.getItem('delta_search_history') || '[]');

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
        if (!res.ok) return document.getElementById('authError').textContent = data.detail || 'Ошибка';

        currentUser = username;
        isAdmin = data.role === 'admin' || data.role === 'owner';
        isOwner = data.role === 'owner' || currentUser === 'seconddurov';

        document.getElementById('authScreen').style.display = 'none';
        document.getElementById('mainScreen').style.display = 'flex';
        document.getElementById('authError').textContent = '';

        connectWebSocket();
        loadChats();
        loadStories();
        renderProfile();
    }

    // ---- WEBSOCKET ----
    function connectWebSocket() {
        const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
        try {
            ws = new WebSocket(`${protocol}//${location.host}/ws/${currentUser}`);
            ws.onopen = () => console.log('[+] websocket connected');
            ws.onmessage = (e) => {
                const data = JSON.parse(e.data);
                handleWebSocketMessage(data);
            };
            ws.onclose = () => {
                console.log('[-] websocket closed, reconnecting...');
                setTimeout(connectWebSocket, 3000);
            };
        } catch(e) {
            console.log('[-] websocket unavailable');
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
                break;
            case 'new_gift':
                alert('[+] gift received: ' + data.gift_name);
                break;
        }
    }

    // ---- ЗАГРУЗКА ЧАТОВ ----
    async function loadChats() {
        try {
            const res = await fetch(`/chats/${currentUser}`, { headers: getVKHeaders() });
            const chats = await res.json();
            chatsData = chats;
            const list = document.getElementById('chatList');

            if (chats.length === 0) {
                list.innerHTML = `<div style="text-align:center;padding:40px 20px;color:rgba(255,255,255,0.1);font-size:13px;">
                    <p style="font-size:48px;margin-bottom:8px;">🔮</p>
                    <p>Нет чатов</p>
                </div>`;
                return;
            }

            list.innerHTML = chats.map(chat => `
                <div class="chat-item" onclick="openChat('${chat.chat_id}', '${chat.title}')">
                    <div class="name">${escapeHtml(chat.title)} <span class="time">${chat.last_message ? formatTime(chat.last_message.time) : ''}</span></div>
                    <div class="last-msg">${chat.last_message ? escapeHtml(chat.last_message.sender + ': ' + chat.last_message.text) : 'Нет сообщений'}</div>
                </div>
            `).join('');
        } catch(e) { console.error('[-] load chats error:', e); }
    }

    function filterChats() {
        const query = document.getElementById('searchInput').value.toLowerCase();
        const items = document.querySelectorAll('.chat-item');
        items.forEach(item => {
            const name = item.querySelector('.name')?.textContent?.toLowerCase() || '';
            const lastMsg = item.querySelector('.last-msg')?.textContent?.toLowerCase() || '';
            const match = name.includes(query) || lastMsg.includes(query);
            item.style.display = match ? 'block' : 'none';
        });
    }

    // ---- ЗАГРУЗКА ИСТОРИЙ ----
    async function loadStories() {
        try {
            const res = await fetch(`/stories/feed?username=${currentUser}`, { headers: getVKHeaders() });
            const data = await res.json();
            storiesData = data;
            const container = document.getElementById('storyRow');
            if (!container) return;
            
            const usernames = Object.keys(data);
            if (usernames.length === 0) {
                container.innerHTML = `<div style="color:rgba(255,255,255,0.05);font-size:12px;padding:4px 0;">Нет историй</div>`;
                return;
            }
            
            container.innerHTML = usernames.map(uname => {
                const stories = data[uname];
                const hasUnviewed = stories.some(s => !s.is_viewed);
                return `
                    <div class="story-item" onclick="openStoryViewer('${uname}')">
                        <div class="ring ${!hasUnviewed ? 'viewed' : 'active'}">
                            <div class="avatar">
                                ${stories[0].media_type === 'video' ? '🎬' : '📸'}
                            </div>
                        </div>
                        <div class="name">${uname}</div>
                    </div>
                `;
            }).join('');
        } catch(e) { console.error('[-] load stories error:', e); }
    }

    // ---- ПРОСМОТР ИСТОРИЙ ----
    async function openStoryViewer(username) {
        if (storyViewerActive) return;
        const res = await fetch(`/stories/feed?username=${currentUser}`, { headers: getVKHeaders() });
        const data = await res.json();
        storiesData = data;
        if (!storiesData[username] || storiesData[username].length === 0) return;
        
        storyViewerActive = true;
        currentStoryUser = username;
        currentStoryIndex = 0;
        showStoryViewer();
    }

    function showStoryViewer() {
        const stories = storiesData[currentStoryUser] || [];
        if (currentStoryIndex >= stories.length || currentStoryIndex < 0) {
            closeStoryViewer();
            return;
        }
        
        const story = stories[currentStoryIndex];
        const isVideo = story.media_type === 'video';
        
        fetch('/story/view', {
            method: 'POST',
            headers: getVKHeaders(),
            body: JSON.stringify({story_id: story.id, username: currentUser})
        });
        
        let viewer = document.getElementById('storyViewer');
        if (!viewer) {
            viewer = document.createElement('div');
            viewer.id = 'storyViewer';
            viewer.style.cssText = 'position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,0.92);z-index:9999;display:flex;align-items:center;justify-content:center;flex-direction:column;';
            document.body.appendChild(viewer);
        }
        
        viewer.innerHTML = `
            <button onclick="closeStoryViewer()" style="position:absolute;top:20px;right:20px;color:#fff;font-size:28px;background:none;border:none;cursor:pointer;">✕</button>
            <button onclick="prevStory()" style="position:absolute;left:10px;top:50%;transform:translateY(-50%);color:#fff;font-size:32px;background:none;border:none;cursor:pointer;">‹</button>
            <button onclick="nextStory()" style="position:absolute;right:10px;top:50%;transform:translateY(-50%);color:#fff;font-size:32px;background:none;border:none;cursor:pointer;">›</button>
            <div style="position:absolute;top:20px;left:20%;right:20%;height:2px;background:rgba(255,255,255,0.1);border-radius:2px;overflow:hidden;">
                <div style="height:100%;background:#3ea6ff;transition:width 0.1s linear;width:0%;"></div>
            </div>
            ${isVideo ? 
                `<video src="${story.media_url}" autoplay controls style="max-width:90%;max-height:70%;border-radius:12px;"></video>` :
                `<img src="${story.media_url}" style="max-width:90%;max-height:70%;border-radius:12px;">`
            }
            <div style="color:#fff;font-size:16px;padding:16px;text-align:center;">${story.caption || ''}</div>
            <div style="color:rgba(255,255,255,0.1);font-size:14px;">${currentStoryIndex+1}/${stories.length}</div>
        `;
        
        if (!isVideo) {
            let progress = 0;
            const fill = viewer.querySelector('div[style*="height:2px"] div');
            const interval = setInterval(() => {
                progress += 2;
                if (fill) fill.style.width = progress + '%';
                if (progress >= 100) {
                    clearInterval(interval);
                    nextStory();
                }
            }, 100);
            viewer.dataset.interval = interval;
        }
    }

    function nextStory() {
        const stories = storiesData[currentStoryUser] || [];
        if (currentStoryIndex + 1 < stories.length) {
            currentStoryIndex++;
            showStoryViewer();
        } else {
            const users = Object.keys(storiesData);
            const currentIdx = users.indexOf(currentStoryUser);
            if (currentIdx + 1 < users.length) {
                currentStoryUser = users[currentIdx + 1];
                currentStoryIndex = 0;
                showStoryViewer();
            } else {
                closeStoryViewer();
            }
        }
    }

    function prevStory() {
        if (currentStoryIndex > 0) {
            currentStoryIndex--;
            showStoryViewer();
        } else {
            const users = Object.keys(storiesData);
            const currentIdx = users.indexOf(currentStoryUser);
            if (currentIdx > 0) {
                currentStoryUser = users[currentIdx - 1];
                const stories = storiesData[currentStoryUser] || [];
                currentStoryIndex = stories.length - 1;
                showStoryViewer();
            }
        }
    }

    function closeStoryViewer() {
        storyViewerActive = false;
        const viewer = document.getElementById('storyViewer');
        if (viewer) {
            if (viewer.dataset.interval) clearInterval(viewer.dataset.interval);
            viewer.remove();
        }
    }

    // ---- ОТКРЫТИЕ ЧАТА ----
    async function openChat(chatId, title) {
        currentChat = chatId;
        const chat = chatsData.find(c => c.chat_id === chatId);
        if (chat && chat.type === 'private') {
            const parts = chat.participants || [];
            currentChatTarget = parts.find(p => p !== currentUser) || title;
        } else {
            currentChatTarget = title;
        }

        document.getElementById('chatView').style.display = 'flex';
        document.getElementById('chatName').textContent = title;

        const res = await fetch(`/messages/${chatId}?username=${currentUser}`, { headers: getVKHeaders() });
        const messages = await res.json();
        const container = document.getElementById('messagesList');
        container.innerHTML = '';
        messages.reverse().forEach(msg => addMessageToChat(msg));

        const msgsContainer = document.getElementById('messagesContainer');
        setTimeout(() => msgsContainer.scrollTop = msgsContainer.scrollHeight, 100);
    }

    function closeChat() {
        currentChat = '';
        document.getElementById('chatView').style.display = 'none';
    }

    function addMessageToChat(msg) {
        const container = document.getElementById('messagesList');
        const isSelf = msg.sender === currentUser;

        let content = '';
        if (msg.text) content += `<div>${escapeHtml(msg.text)}</div>`;
        if (msg.sticker) content += `<span style="font-size:40px;">${msg.sticker}</span>`;
        if (msg.is_location) {
            content += `<div style="color:#3ea6ff;cursor:pointer;" onclick="window.open('https://www.openstreetmap.org/?mlat=${msg.latitude}&mlon=${msg.longitude}&zoom=15')">
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
        if (!isSelf) {
            msgDiv.innerHTML = `<div class="sender">${escapeHtml(msg.sender)}</div>` + content;
        } else {
            msgDiv.innerHTML = content;
        }

        container.appendChild(msgDiv);
        document.getElementById('messagesContainer').scrollTop = document.getElementById('messagesContainer').scrollHeight;
    }

    // ---- ОТПРАВКА СООБЩЕНИЯ ----
    async function sendMessage() {
        const input = document.getElementById('msgInput');
        const text = input.value.trim();
        if (!text || !currentChat) return;

        if (!ws || ws.readyState !== WebSocket.OPEN) {
            alert('Нет соединения');
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
                if (data.avatar_url) alert('[+] photo uploaded');
                renderProfile();
            });
        event.target.value = '';
    }

    // ---- ПОИСК ----
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
            localStorage.setItem('delta_search_history', JSON.stringify(searchHistory));
        }

        const usersRes = await fetch(`/search_users?query=${query}&username=${currentUser}`, { headers: getVKHeaders() });
        const users = await usersRes.json();

        let html = '';
        if (users.length > 0) {
            users.forEach(u => {
                html += `
                    <div onclick="startChat('${u.username}'); closeSearchMenu();" style="padding:10px 12px;background:rgba(255,255,255,0.02);border-radius:10px;margin-bottom:4px;cursor:pointer;display:flex;align-items:center;gap:12px;">
                        <span style="font-size:20px;">${u.online ? '🟢' : '👤'}</span>
                        <div>
                            <div style="font-weight:500;color:#ffffff;">${u.username}</div>
                            <div style="font-size:12px;color:rgba(255,255,255,0.1);">${u.premium ? 'Premium' : 'Пользователь'}</div>
                        </div>
                    </div>
                `;
            });
        }

        if (!html) {
            html = `<div style="padding:20px;text-align:center;color:rgba(255,255,255,0.05);">Ничего не найдено</div>`;
        }

        resultsContainer.innerHTML = html;
    }

    function renderSearchHistory() {
        const container = document.getElementById('searchHistory');
        if (searchHistory.length === 0) {
            return;
        }
        container.innerHTML = `
            <div style="font-size:11px;color:rgba(255,255,255,0.05);padding:8px 0;">Недавние:</div>
            ${searchHistory.map(q => `
                <div onclick="document.getElementById('searchInputGlobal').value='${q}'; globalSearch();" style="padding:6px 12px;background:rgba(255,255,255,0.02);border-radius:8px;margin-bottom:2px;cursor:pointer;font-size:13px;color:rgba(255,255,255,0.2);">
                    🔍 ${q}
                </div>
            `).join('')}
        `;
    }

    async function startChat(username) {
        if (!username || username === currentUser) {
            alert('Нельзя чат с собой');
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
            }
        } catch(e) {
            alert('Ошибка');
        }
    }

    function createChatDialog() {
        const target = prompt('👤 Введите имя пользователя:');
        if (target) startChat(target);
    }

    // ---- ПРОФИЛЬ ----
    async function renderProfile() {
        const res = await fetch(`/profile/${currentUser}?viewer=${currentUser}`, { headers: getVKHeaders() });
        const data = await res.json();

        if (!accounts.includes(currentUser)) {
            accounts.push(currentUser);
            localStorage.setItem('delta_accounts', JSON.stringify(accounts));
        }

        const roleDisplay = currentUser === 'seconddurov' ? '👑 OWNER' : (data.role === 'admin' ? '⚡ ADMIN' : '👤 USER');

        const container = document.getElementById('profileContent');
        container.innerHTML = `
            <div class="profile-avatar" onclick="document.getElementById('avatarInputProfile').click()">
                ${data.avatar ? `<img src="${data.avatar}">` : '👤'}
            </div>
            <input type="file" id="avatarInputProfile" accept="image/*" style="display:none" onchange="uploadProfileAvatar(event)">
            <div class="profile-name">
                ${escapeHtml(data.username)}
                <span class="sub">${data.phone || ''} • @${data.username}</span>
            </div>
            <div class="profile-bio">${escapeHtml(data.bio) || 'Без описания'}</div>

            <div class="profile-accounts">
                <div style="font-size:12px;color:rgba(255,255,255,0.05);margin-bottom:6px;text-align:left;">Мои аккаунты</div>
                ${accounts.map(acc => `
                    <div class="account-item" onclick="switchAccountInstant('${acc}')">
                        <span class="acc-name">${acc} ${acc === currentUser ? '✅' : ''}</span>
                        <span class="acc-status">${acc === currentUser ? 'активен' : ''}</span>
                        <span class="acc-switch">${acc === currentUser ? '' : '➜'}</span>
                    </div>
                `).join('')}
            </div>

            <div class="profile-actions">
                <button class="add-account-btn" onclick="addAccount()">➕ Добавить аккаунт</button>
                ${isAdmin ? `<button class="admin-btn" onclick="loadAdminStats()">⚡ Админ-панель</button>` : ''}
                <button onclick="switchTab('chats')">💬 К чатам</button>
                <button class="logout-btn" onclick="logout()">🚪 Выйти</button>
            </div>
        `;
    }

    async function uploadProfileAvatar(event) {
        const file = event.target.files[0];
        if (!file) return;
        const formData = new FormData();
        formData.append('file', file);
        const res = await fetch(`/upload_avatar/${currentUser}`, { method: 'POST', body: formData });
        const data = await res.json();
        if (data.avatar_url) {
            alert('[+] avatar updated');
            renderProfile();
        }
        event.target.value = '';
    }

    async function switchAccountInstant(username) {
        if (username === currentUser) return;
        
        document.getElementById('profileContent').innerHTML = `<div style="text-align:center;padding:40px;color:rgba(255,255,255,0.05);">🔄 Переключение...</div>`;
        
        try {
            const formData = new FormData();
            formData.append('username', username);
            formData.append('password', '');
            formData.append('device_id', deviceId);
            
            const res = await fetch('/login', { method: 'POST', body: formData });
            const data = await res.json();
            
            if (!res.ok) {
                const password = prompt(`Введите пароль для ${username}:`);
                if (!password) {
                    renderProfile();
                    return;
                }
                const formData2 = new FormData();
                formData2.append('username', username);
                formData2.append('password', password);
                formData2.append('device_id', deviceId);
                const res2 = await fetch('/login', { method: 'POST', body: formData2 });
                const data2 = await res2.json();
                if (!res2.ok) {
                    alert('❌ Ошибка: ' + (data2.detail || 'Неверный пароль'));
                    renderProfile();
                    return;
                }
                currentUser = username;
                isAdmin = data2.role === 'admin' || data2.role === 'owner';
                isOwner = data2.role === 'owner' || currentUser === 'seconddurov';
            } else {
                currentUser = username;
                isAdmin = data.role === 'admin' || data.role === 'owner';
                isOwner = data.role === 'owner' || currentUser === 'seconddurov';
            }
            
            if (ws) ws.close();
            connectWebSocket();
            loadChats();
            loadStories();
            renderProfile();
            
            document.querySelector('.header .logo').textContent = `💙 ${currentUser}`;
            alert(`✅ Переключено на ${currentUser}`);
            
        } catch(e) {
            alert('❌ Ошибка: ' + e.message);
            renderProfile();
        }
    }

    async function addAccount() {
        const username = prompt('Введите новый юзернейм:');
        if (!username) return;
        const password = prompt('Введите пароль:') || '';
        
        try {
            const formData = new FormData();
            formData.append('username', username);
            formData.append('password', password);
            formData.append('device_id', deviceId);
            const res = await fetch('/register', { method: 'POST', body: formData });
            if (res.ok) {
                alert('✅ Аккаунт создан! Переключитесь на него');
                accounts.push(username);
                localStorage.setItem('delta_accounts', JSON.stringify(accounts));
                renderProfile();
            } else {
                alert('❌ Ошибка: ' + (await res.text()));
            }
        } catch(e) {
            alert('Ошибка сети');
        }
    }

    // ---- ПРОСМОТР ПРОФИЛЯ ПОЛЬЗОВАТЕЛЯ ----
    async function viewProfile() {
        const title = document.getElementById('chatName').textContent;
        if (!title || title === 'Чат') return;

        const res = await fetch(`/profile/${title}?viewer=${currentUser}`, { headers: getVKHeaders() });
        const data = await res.json();

        const canMakeAdmin = isOwner && data.role !== 'admin' && data.role !== 'owner' && title !== 'seconddurov';

        alert(`👤 ${data.username}
${data.emoji_status || '𖤐'} ${data.role === 'admin' ? '👑 ADMIN' : '👤 USER'}
${data.is_online ? '🟢 Онлайн' : '⚪ Оффлайн'}
${data.bio ? '📝 ' + data.bio : ''}
${canMakeAdmin ? '\nНажмите ОК чтобы сделать администратором' : ''}`);

        if (canMakeAdmin && confirm(`Сделать ${data.username} администратором?`)) {
            makeAdmin(data.username);
        }
    }

    async function makeAdmin(username) {
        const res = await fetch('/make_admin', {
            method: 'POST',
            headers: getVKHeaders(),
            body: JSON.stringify({target_username: username, username: currentUser})
        });
        const data = await res.json();
        if (data.status === 'ok') {
            alert('[+] ' + data.message);
            renderProfile();
        } else {
            alert('[-] ' + (data.message || 'error'));
        }
    }

    // ---- ЗАГРУЗКА ИСТОРИИ (кнопка) ----
    async function uploadStory() {
        const input = document.createElement('input');
        input.type = 'file';
        input.accept = 'image/*,video/*';
        input.onchange = async (e) => {
            const file = e.target.files[0];
            if (!file) return;
            const caption = prompt('[+] caption:') || '';
            const formData = new FormData();
            formData.append('username', currentUser);
            formData.append('file', file);
            formData.append('caption', caption);

            const res = await fetch('/story/upload', { method: 'POST', body: formData });
            const data = await res.json();
            if (data.story_id) alert('[+] story published');
            loadStories();
        };
        input.click();
    }

    // ---- АДМИН-СТАТИСТИКА ----
    async function loadAdminStats() {
        if (!isAdmin) {
            alert('Доступ только для администраторов');
            return;
        }
        const res = await fetch(`/admin/stats?username=${currentUser}`, { headers: getVKHeaders() });
        const data = await res.json();
        alert(`📊 СТАТИСТИКА:
👥 Юзеров: ${data.total_users}
🟢 Онлайн: ${data.online_devices}
📱 Устройств: ${data.total_devices}
💬 Сообщений: ${data.total_messages}
📅 Сегодня: ${data.messages_today}
💭 Чатов: ${data.total_chats}
📸 Историй: ${data.active_stories}
📞 Звонков: ${data.total_calls}`);
    }

    // ---- ПЕРЕКЛЮЧЕНИЕ ВКЛАДОК ----
    function switchTab(tab) {
        document.querySelectorAll('.tabs button').forEach(el => el.classList.remove('active'));
        document.querySelectorAll('.tab-content').forEach(el => el.classList.remove('active'));
        document.querySelector(`.tabs button[data-tab="${tab}"]`).classList.add('active');
        document.getElementById(`tab-${tab}`).classList.add('active');
        
        if (tab === 'profile') renderProfile();
        if (tab === 'chats') {
            loadChats();
            loadStories();
        }
    }

    // ---- ВЫХОД ----
    function logout() {
        if (ws) ws.close();
        localStorage.clear();
        location.reload();
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

    window.onload = function() {
        // ПОЛЯ ПУСТЫЕ
    };
</script>
</body>
</html>"""

if __name__ == "__main__":
    port = int(os.getenv("PORT", 8000))
    asyncio.create_task(cleanup_expired_stories())
    uvicorn.run(app, host="0.0.0.0", port=port)
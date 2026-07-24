import json
import uuid
import hashlib
import secrets
import sqlite3
import os
import logging
import asyncio
import random
import time
import struct
from datetime import datetime, timedelta
from fastapi import FastAPI, WebSocket, HTTPException, Request, File, UploadFile, Form, Query, Response
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from typing import Dict, Optional, List
from PIL import Image
import io
import uvicorn
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.asymmetric import x25519
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.backends import default_backend
import base64

# ----- ЛОГИРОВАНИЕ ОТКЛЮЧЕНО -----
logging.getLogger("uvicorn.access").handlers = []
logging.getLogger("uvicorn.access").propagate = False
logging.getLogger("uvicorn.error").disabled = True

app = FastAPI(title="DELTA", docs_url=None, redoc_url=None)

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
app.mount("/avatars", StaticFiles(directory="avatars"), name="avatars")

# ----- БАЗА ДАННЫХ -----
conn = sqlite3.connect("delta.db", check_same_thread=False)
c = conn.cursor()

c.execute('''CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    username TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    salt TEXT NOT NULL,
    premium INTEGER DEFAULT 1,
    nft_collection TEXT DEFAULT '[]',
    active_nft TEXT DEFAULT '',
    profile_emoji TEXT DEFAULT '𖤐',
    avatar_path TEXT DEFAULT '',
    role TEXT DEFAULT 'user',
    blocked_users TEXT DEFAULT '[]',
    last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    is_online INTEGER DEFAULT 0,
    bio TEXT DEFAULT '',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    public_key TEXT DEFAULT '',
    auto_delete_enabled INTEGER DEFAULT 1,
    auto_delete_time INTEGER DEFAULT 3600
)''')

c.execute('''CREATE TABLE IF NOT EXISTS chats (
    id TEXT PRIMARY KEY,
    type TEXT DEFAULT 'private',
    title TEXT DEFAULT '',
    participants TEXT DEFAULT '[]',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    auto_delete_enabled INTEGER DEFAULT 1,
    auto_delete_time INTEGER DEFAULT 3600
)''')

c.execute('''CREATE TABLE IF NOT EXISTS messages (
    id TEXT PRIMARY KEY,
    chat_id TEXT,
    sender_username TEXT,
    text TEXT,
    encrypted_content TEXT,
    sticker TEXT DEFAULT '',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    auto_delete_after INTEGER DEFAULT NULL,
    is_permanent INTEGER DEFAULT 0
)''')

c.execute('''CREATE TABLE IF NOT EXISTS nft_items (
    id TEXT PRIMARY KEY,
    name TEXT,
    emoji TEXT,
    rarity TEXT,
    description TEXT,
    category TEXT
)''')

c.execute('''CREATE TABLE IF NOT EXISTS encryption_keys (
    user1 TEXT,
    user2 TEXT,
    shared_secret TEXT,
    UNIQUE(user1, user2)
)''')

c.execute('''CREATE TABLE IF NOT EXISTS stickers (
    id TEXT PRIMARY KEY,
    emoji TEXT,
    name TEXT,
    category TEXT
)''')

# ----- ЗАГРУЗКА NFT -----
nft_data = [
    ("ghost_crown", "Корона призрака", "👻👑", "Legendary", "Древняя корона короля призраков", "crowns"),
    ("shadow_blade", "Теневой клинок", "🗡️🌑", "Legendary", "Клинок из чистой тьмы", "weapons"),
    ("diamond_heart", "Алмазное сердце", "💎❤️", "Legendary", "Сердце из чистого алмаза", "gems"),
    ("fire_phoenix", "Огненный феникс", "🔥🦅", "Epic", "Возрождается из пепла", "mythical"),
    ("ice_dragon", "Ледяной дракон", "🐉❄️", "Epic", "Дракон северных земель", "dragons"),
    ("thunder_wolf", "Громовой волк", "⚡🐺", "Epic", "Волк призывающий молнии", "beasts"),
    ("moon_rabbit", "Лунный кролик", "🌙🐰", "Rare", "Кролик с обратной стороны луны", "cute"),
    ("star_unicorn", "Звездный единорог", "⭐🦄", "Rare", "Единорог из созвездия", "mythical"),
    ("ocean_pearl", "Океанская жемчужина", "🌊🫧", "Rare", "Жемчужина глубин", "gems"),
    ("forest_spirit", "Лесной дух", "🌲👻", "Rare", "Дух древнего леса", "spirits"),
    ("golden_scarab", "Золотой скарабей", "🪲✨", "Epic", "Скарабей приносящий удачу", "insects"),
    ("crystal_rose", "Хрустальная роза", "🌹💠", "Rare", "Вечно цветущая роза", "flowers"),
    ("neon_butterfly", "Неоновая бабочка", "🦋💫", "Rare", "Бабочка из неона", "insects"),
    ("void_cat", "Кот пустоты", "🐱🕳️", "Epic", "Кот из другого измерения", "cats"),
    ("rainbow_slime", "Радужный слайм", "🌈🫧", "Common", "Весёлый радужный слайм", "slimes"),
    ("cosmic_whale", "Космический кит", "🐋🌟", "Legendary", "Кит плывущий среди звезд", "space"),
    ("pixel_heart", "Пиксельное сердце", "💖👾", "Common", "Ретро пиксельное сердце", "retro"),
    ("magic_mushroom", "Волшебный гриб", "🍄✨", "Common", "Гриб из волшебного леса", "nature"),
    ("cyber_skull", "Кибер череп", "💀🤖", "Epic", "Череп из будущего", "cyber"),
    ("angel_wing", "Крыло ангела", "🪽😇", "Rare", "Одно крыло ангела", "angelic"),
    ("demon_horn", "Рог демона", "👿🔥", "Rare", "Рог повелителя тьмы", "demonic"),
    ("time_clock", "Часы времени", "⏰🌀", "Legendary", "Часы останавливающие время", "artifacts"),
    ("lucky_clover", "Счастливый клевер", "🍀💚", "Common", "Приносит удачу владельцу", "nature"),
    ("sapphire_eye", "Сапфировый глаз", "👁️💎", "Epic", "Всевидящее око", "artifacts"),
    ("thunder_hammer", "Молот грома", "🔨⚡", "Epic", "Молот бога грома", "weapons"),
    ("mermaid_tear", "Слеза русалки", "🧜‍♀️💧", "Rare", "Слеза морской девы", "gems"),
    ("phoenix_feather", "Перо феникса", "🪶🔥", "Rare", "Перо огненной птицы", "mythical"),
    ("dragon_egg", "Яйцо дракона", "🥚🐉", "Epic", "Яйцо будущего дракона", "dragons"),
    ("shadow_mask", "Маска тени", "🎭🌑", "Legendary", "Маска скрывающая личность", "masks"),
    ("eternal_flame", "Вечный огонь", "🔥♾️", "Legendary", "Огонь который не гаснет", "elements")
]

for nft in nft_data:
    c.execute("INSERT OR IGNORE INTO nft_items (id, name, emoji, rarity, description, category) VALUES (?, ?, ?, ?, ?, ?)",
              (str(uuid.uuid4()), nft[0], nft[1], nft[2], nft[3], nft[4]))
conn.commit()

sticker_data = [
    ("👍", "like"), ("❤️", "heart"), ("😂", "laugh"), ("😢", "cry"), ("🔥", "fire"),
    ("🎉", "party"), ("💀", "skull"), ("👻", "ghost"), ("🎭", "masks"), ("🌑", "moon"),
    ("⭐", "star"), ("💎", "diamond")
]

for sticker in sticker_data:
    c.execute("INSERT OR IGNORE INTO stickers (id, emoji, name, category) VALUES (?, ?, ?, ?)",
              (str(uuid.uuid4()), sticker[0], sticker[1], "default"))
conn.commit()

# ----- МАСКИРОВКА ТРАФИКА ПОД VK -----
class TrafficObfuscator:
    VK_SALT = b"vk_api_obfuscation_salt"
    
    @staticmethod
    def pack(data: bytes) -> bytes:
        timestamp = struct.pack('>Q', int(time.time() * 1000))
        length = struct.pack('>I', len(data))
        header = timestamp + length
        
        packet = header + data
        key = hashlib.pbkdf2_hmac('sha256', TrafficObfuscator.VK_SALT, timestamp, 32)
        iv = secrets.token_bytes(16)
        cipher = AESGCM(key)
        encrypted = cipher.encrypt(iv, packet, None)
        
        return base64.b64encode(iv + encrypted)
    
    @staticmethod
    def unpack(packet: bytes) -> bytes:
        try:
            raw = base64.b64decode(packet)
            iv = raw[:16]
            encrypted = raw[16:]
            
            timestamp = struct.pack('>Q', int(time.time() * 1000))
            key = hashlib.pbkdf2_hmac('sha256', TrafficObfuscator.VK_SALT, timestamp, 32)
            cipher = AESGCM(key)
            decrypted = cipher.decrypt(iv, encrypted, None)
            
            data_length = struct.unpack('>I', decrypted[8:12])[0]
            return decrypted[12:12+data_length]
        except:
            try:
                key = hashlib.pbkdf2_hmac('sha256', TrafficObfuscator.VK_SALT, struct.pack('>Q', int((time.time() - 1) * 1000)), 32)
                cipher = AESGCM(key)
                decrypted = cipher.decrypt(iv, encrypted, None)
                data_length = struct.unpack('>I', decrypted[8:12])[0]
                return decrypted[12:12+data_length]
            except:
                return packet

# ----- E2E ШИФРОВАНИЕ -----
class E2EEncryption:
    @staticmethod
    def generate_keypair():
        private_key = x25519.X25519PrivateKey.generate()
        public_key = private_key.public_key()
        return private_key.private_bytes(encoding=serialization.Encoding.Raw, format=serialization.PrivateFormat.Raw, encryption_algorithm=serialization.NoEncryption()), public_key.public_bytes(encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw)
    
    @staticmethod
    def derive_shared_secret(private_bytes, public_bytes):
        private_key = x25519.X25519PrivateKey.from_private_bytes(private_bytes)
        public_key = x25519.X25519PublicKey.from_public_bytes(public_bytes)
        return private_key.exchange(public_key)
    
    @staticmethod
    def encrypt(message: str, shared_secret: bytes) -> str:
        salt = secrets.token_bytes(16)
        key = HKDF(algorithm=hashes.SHA256(), length=32, salt=salt, info=b'delta-e2e').derive(shared_secret)
        nonce = secrets.token_bytes(12)
        ciphertext = AESGCM(key).encrypt(nonce, message.encode(), None)
        return base64.b64encode(salt + nonce + ciphertext).decode()
    
    @staticmethod
    def decrypt(package: str, shared_secret: bytes) -> str:
        try:
            data = base64.b64decode(package)
            salt, nonce, ciphertext = data[:16], data[16:28], data[28:]
            key = HKDF(algorithm=hashes.SHA256(), length=32, salt=salt, info=b'delta-e2e').derive(shared_secret)
            return AESGCM(key).decrypt(nonce, ciphertext, None).decode()
        except:
            return "[ENCRYPTED]"

# ----- ФУНКЦИИ -----
def hash_password(password: str, salt: str = None):
    if not salt: salt = secrets.token_hex(16)
    return hashlib.sha256((salt + password).encode()).hexdigest(), salt

def get_user(username: str):
    c.execute("SELECT * FROM users WHERE username = ?", (username,))
    return c.fetchone()

def get_chat(chat_id: str):
    c.execute("SELECT * FROM chats WHERE id = ?", (chat_id,))
    return c.fetchone()

def ensure_owner():
    c.execute("SELECT role FROM users WHERE username = 'seconddurov'")
    row = c.fetchone()
    if row and row[0] != "owner":
        c.execute("UPDATE users SET role = 'owner' WHERE username = 'seconddurov'")
        conn.commit()
    elif not row:
        pwd_hash, salt = hash_password("020112")
        _, pub = E2EEncryption.generate_keypair()
        c.execute("INSERT INTO users (id, username, password_hash, salt, profile_emoji, role, nft_collection, bio, public_key) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                  (str(uuid.uuid4()), "seconddurov", pwd_hash, salt, "👑", "owner", json.dumps(["eternal_flame", "shadow_mask", "ghost_crown"]), "🌑 Владыка DELTA", base64.b64encode(pub).decode()))
        conn.commit()

ensure_owner()

# ----- WEBSOCKET MANAGER -----
class ConnectionManager:
    def __init__(self):
        self.active: Dict[str, WebSocket] = {}
        self.keys: Dict[str, tuple] = {}
    
    async def connect(self, username: str, ws: WebSocket):
        await ws.accept()
        self.active[username] = ws
        if username not in self.keys:
            self.keys[username] = E2EEncryption.generate_keypair()
        c.execute("UPDATE users SET is_online = 1, last_seen = CURRENT_TIMESTAMP WHERE username = ?", (username,))
        conn.commit()
    
    def disconnect(self, username: str):
        self.active.pop(username, None)
        c.execute("UPDATE users SET is_online = 0, last_seen = CURRENT_TIMESTAMP WHERE username = ?", (username,))
        conn.commit()
    
    async def send(self, username: str, data: dict):
        if username in self.active:
            try:
                await self.active[username].send_text(json.dumps(data))
                return True
            except:
                self.disconnect(username)
        return False
    
    async def broadcast_to_chat(self, chat_id: str, data: dict, exclude: List[str] = []):
        c.execute("SELECT participants FROM chats WHERE id = ?", (chat_id,))
        row = c.fetchone()
        if row:
            for u in json.loads(row[0]):
                if u not in exclude:
                    await self.send(u, data)
    
    def get_secret(self, u1: str, u2: str):
        c.execute("SELECT shared_secret FROM encryption_keys WHERE (user1=? AND user2=?) OR (user1=? AND user2=?)", (min(u1, u2), max(u1, u2), min(u1, u2), max(u1, u2)))
        row = c.fetchone()
        if row:
            return bytes.fromhex(row[0])
        if u1 in self.keys and u2 in self.keys:
            s1 = E2EEncryption.derive_shared_secret(self.keys[u1][0], self.keys[u2][1])
            s2 = E2EEncryption.derive_shared_secret(self.keys[u2][0], self.keys[u1][1])
            if s1 == s2:
                h = hashlib.sha256(s1).hexdigest()
                c.execute("INSERT INTO encryption_keys (user1, user2, shared_secret) VALUES (?, ?, ?)", (min(u1, u2), max(u1, u2), h))
                conn.commit()
                return s1
        return None

manager = ConnectionManager()

# ----- ЭНДПОИНТЫ -----
@app.post("/vk/data")
async def register(username: str = Form(...), password: str = Form("")):
    if get_user(username):
        raise HTTPException(400, "exists")
    pwd_hash, salt = hash_password(password if password else secrets.token_hex(8))
    _, pub = E2EEncryption.generate_keypair()
    c.execute("INSERT INTO users (id, username, password_hash, salt, profile_emoji, role, public_key) VALUES (?, ?, ?, ?, ?, ?, ?)",
              (str(uuid.uuid4()), username, pwd_hash, salt, "𖤐", "user", base64.b64encode(pub).decode()))
    conn.commit()
    return {"ok": True, "username": username}

@app.post("/vk/auth")
async def login(username: str = Form(...), password: str = Form("")):
    user = get_user(username)
    if not user: raise HTTPException(404, "not found")
    if password:
        if hash_password(password, user[3])[0] != user[2]:
            raise HTTPException(401, "invalid")
    c.execute("UPDATE users SET is_online = 1, last_seen = CURRENT_TIMESTAMP WHERE username = ?", (username,))
    conn.commit()
    av = f"/avatars/{user[0]}.png" if user[8] and os.path.exists(user[8]) else None
    return {
        "username": user[1],
        "role": user[7],
        "emoji": user[6],
        "nfts": json.loads(user[5]),
        "avatar": av,
        "bio": user[12] or "",
        "auto_delete_enabled": bool(user[13]),
        "auto_delete_time": user[14]
    }

@app.get("/vk/user")
async def profile(username: str = Query(...)):
    user = get_user(username)
    if not user: raise HTTPException(404, "")
    nfts = json.loads(user[5]) or []
    nd = []
    for n in nfts:
        c.execute("SELECT * FROM nft_items WHERE name=?", (n,))
        r = c.fetchone()
        if r: nd.append({"name": r[1], "emoji": r[2], "rarity": r[3]})
    return {
        "username": user[1],
        "nfts": nd,
        "role": user[7],
        "emoji": user[6],
        "avatar": f"/avatars/{user[0]}.png" if user[8] else None,
        "bio": user[12],
        "auto_delete_enabled": bool(user[13]),
        "auto_delete_time": user[14]
    }

@app.get("/vk/nfts")
async def all_nfts():
    c.execute("SELECT * FROM nft_items ORDER BY CASE rarity WHEN 'Legendary' THEN 1 WHEN 'Epic' THEN 2 WHEN 'Rare' THEN 3 ELSE 4 END")
    return [{"id": n[0], "name": n[1], "emoji": n[2], "rarity": n[3], "desc": n[4]} for n in c.fetchall()]

@app.post("/vk/nftsend")
async def send_nft(to: str = Form(...), nft: str = Form(...), user: str = Form(...)):
    s = get_user(user); r = get_user(to)
    if not s or not r: raise HTTPException(404, "")
    sn = json.loads(s[5]) or []
    if nft not in sn: raise HTTPException(400, "")
    sn.remove(nft)
    rn = json.loads(r[5]) or []
    rn.append(nft)
    c.execute("UPDATE users SET nft_collection=? WHERE username=?", (json.dumps(sn), user))
    c.execute("UPDATE users SET nft_collection=? WHERE username=?", (json.dumps(rn), to))
    conn.commit()
    await manager.send(to, {"event": "nft", "from": user, "nft": nft})
    return {"ok": True}

@app.post("/vk/nftset")
async def activate_nft(nft: str = Form(...), user: str = Form(...)):
    u = get_user(user)
    if not u or nft not in (json.loads(u[5]) or []): raise HTTPException(400, "")
    c.execute("UPDATE users SET active_nft=? WHERE username=?", (nft, user))
    conn.commit()
    return {"ok": True}

@app.post("/vk/nftremove")
async def deactivate_nft(user: str = Form(...)):
    c.execute("UPDATE users SET active_nft='' WHERE username=?", (user,))
    conn.commit()
    return {"ok": True}

# ----- НАСТРОЙКИ АВТОУДАЛЕНИЯ -----
@app.post("/vk/autodelete/toggle")
async def toggle_auto_delete(user: str = Form(...)):
    u = get_user(user)
    if not u: raise HTTPException(404, "")
    new_state = 0 if u[13] else 1
    c.execute("UPDATE users SET auto_delete_enabled=? WHERE username=?", (new_state, user))
    conn.commit()
    
    # Обновляем все чаты пользователя
    c.execute("SELECT id FROM chats WHERE participants LIKE ?", (f'%"{user}"%',))
    for chat in c.fetchall():
        c.execute("UPDATE chats SET auto_delete_enabled=? WHERE id=?", (new_state, chat[0]))
    conn.commit()
    
    return {"ok": True, "auto_delete_enabled": bool(new_state)}

@app.post("/vk/autodelete/time")
async def set_auto_delete_time(user: str = Form(...), time: int = Form(...)):
    """Установить время автоудаления в секундах (0 = никогда)"""
    if time < 0: raise HTTPException(400, "Invalid time")
    c.execute("UPDATE users SET auto_delete_time=? WHERE username=?", (time, user))
    conn.commit()
    
    # Обновляем все чаты пользователя
    c.execute("SELECT id FROM chats WHERE participants LIKE ?", (f'%"{user}"%',))
    for chat in c.fetchall():
        c.execute("UPDATE chats SET auto_delete_time=? WHERE id=?", (time, chat[0]))
    conn.commit()
    
    return {"ok": True, "auto_delete_time": time}

@app.post("/vk/chat/autodelete")
async def set_chat_auto_delete(chat_id: str = Form(...), user: str = Form(...), enabled: bool = Form(True), time: int = Form(3600)):
    """Настройки автоудаления для конкретного чата"""
    chat = get_chat(chat_id)
    if not chat or user not in json.loads(chat[3]): raise HTTPException(403, "")
    
    c.execute("UPDATE chats SET auto_delete_enabled=?, auto_delete_time=? WHERE id=?", 
              (1 if enabled else 0, time, chat_id))
    conn.commit()
    
    return {"ok": True, "chat_id": chat_id, "auto_delete_enabled": enabled, "auto_delete_time": time}

@app.get("/vk/chat/settings")
async def get_chat_settings(chat_id: str = Query(...), user: str = Query(...)):
    """Получить настройки автоудаления чата"""
    chat = get_chat(chat_id)
    if not chat or user not in json.loads(chat[3]): raise HTTPException(403, "")
    
    return {
        "auto_delete_enabled": bool(chat[6]),
        "auto_delete_time": chat[7]
    }

@app.post("/vk/message/permanent")
async def make_message_permanent(message_id: str = Form(...), user: str = Form(...)):
    """Сделать сообщение постоянным (не удалять)"""
    c.execute("UPDATE messages SET is_permanent=1, auto_delete_after=NULL WHERE id=? AND sender_username=?", 
              (message_id, user))
    conn.commit()
    return {"ok": True}

# ----- АДМИН ЭНДПОИНТЫ -----
@app.post("/vk/owner/makeadmin")
async def make_admin(target: str = Form(...), user: str = Form(...)):
    if get_user(user)[7] != "owner": raise HTTPException(403, "")
    c.execute("UPDATE users SET role='admin' WHERE username=?", (target,))
    conn.commit()
    await manager.send(target, {"event": "admin"})
    return {"ok": True}

@app.post("/vk/owner/rmadmin")
async def remove_admin(target: str = Form(...), user: str = Form(...)):
    if get_user(user)[7] != "owner": raise HTTPException(403, "")
    if target == "seconddurov": raise HTTPException(400, "")
    c.execute("UPDATE users SET role='user' WHERE username=?", (target,))
    conn.commit()
    return {"ok": True}

@app.post("/vk/owner/deluser")
async def delete_user(target: str = Form(...), user: str = Form(...)):
    if get_user(user)[7] != "owner": raise HTTPException(403, "")
    if target == "seconddurov": raise HTTPException(400, "")
    c.execute("DELETE FROM users WHERE username=?", (target,))
    c.execute("DELETE FROM messages WHERE sender_username=?", (target,))
    conn.commit()
    return {"ok": True}

@app.post("/vk/owner/givenft")
async def give_nft_admin(target: str = Form(...), nft: str = Form(...), user: str = Form(...)):
    if get_user(user)[7] not in ["owner", "admin"]: raise HTTPException(403, "")
    r = get_user(target)
    if not r: raise HTTPException(404, "")
    rn = json.loads(r[5]) or []
    rn.append(nft)
    c.execute("UPDATE users SET nft_collection=? WHERE username=?", (json.dumps(rn), target))
    conn.commit()
    return {"ok": True}

@app.get("/vk/admin/stats")
async def admin_stats(user: str = Query(...)):
    if get_user(user)[7] not in ["owner", "admin"]: raise HTTPException(403, "")
    return {
        "users": c.execute("SELECT COUNT(*) FROM users").fetchone()[0],
        "online": c.execute("SELECT COUNT(*) FROM users WHERE is_online=1").fetchone()[0],
        "msgs": c.execute("SELECT COUNT(*) FROM messages").fetchone()[0],
        "chats": c.execute("SELECT COUNT(*) FROM chats").fetchone()[0]
    }

@app.get("/vk/admin/users")
async def admin_users_list(user: str = Query(...)):
    if get_user(user)[7] not in ["owner", "admin"]: raise HTTPException(403, "")
    return [{"username": u[0], "role": u[1], "online": bool(u[2])} for u in c.execute("SELECT username, role, is_online FROM users ORDER BY created_at DESC LIMIT 50").fetchall()]

@app.post("/vk/avatar")
async def upload_avatar(user: str = Form(...), file: UploadFile = File(...)):
    u = get_user(user)
    if not u: raise HTTPException(404, "")
    fp = f"avatars/{u[0]}.png"
    img = Image.open(io.BytesIO(await file.read())).resize((200, 200))
    img.save(fp, "PNG", optimize=True)
    c.execute("UPDATE users SET avatar_path=? WHERE id=?", (fp, u[0]))
    conn.commit()
    return {"ok": True, "url": f"/avatars/{u[0]}.png"}

@app.post("/vk/bio")
async def update_bio(user: str = Form(...), bio: str = Form("")):
    c.execute("UPDATE users SET bio=? WHERE username=?", (bio[:200], user))
    conn.commit()
    return {"ok": True}

@app.get("/vk/chats")
async def get_chats(user: str = Query(...)):
    c.execute("SELECT id, type, title, participants, auto_delete_enabled, auto_delete_time FROM chats WHERE participants LIKE ?", (f'%"{user}"%',))
    r = []
    for ch in c.fetchall():
        p = json.loads(ch[3])
        o = [u for u in p if u != user][0] if ch[1] == "private" else None
        c.execute("SELECT text, sender_username, created_at FROM messages WHERE chat_id=? ORDER BY created_at DESC LIMIT 1", (ch[0],))
        lm = c.fetchone()
        r.append({
            "chat_id": ch[0],
            "title": ch[2] if ch[1] != "private" else o,
            "auto_delete_enabled": bool(ch[4]),
            "auto_delete_time": ch[5],
            "last_msg": {"text": (lm[0] or "")[:50] if lm else "", "sender": lm[1] if lm else ""} if lm else None
        })
    return r

@app.post("/vk/chatnew")
async def create_chat(target: str = Form(...), user: str = Form(...)):
    if user == target: raise HTTPException(400, "")
    c.execute("SELECT id FROM chats WHERE type='private' AND participants LIKE ? AND participants LIKE ?", (f'%"{user}"%', f'%"{target}"%'))
    ex = c.fetchone()
    if ex: return {"chat_id": ex[0]}
    
    # Получаем настройки автоудаления пользователя
    u = get_user(user)
    auto_del = u[13] if u else 1
    auto_time = u[14] if u else 3600
    
    cid = str(uuid.uuid4())
    c.execute("INSERT INTO chats (id, type, participants, auto_delete_enabled, auto_delete_time) VALUES (?, ?, ?, ?, ?)",
              (cid, "private", json.dumps([user, target]), auto_del, auto_time))
    conn.commit()
    return {"chat_id": cid, "auto_delete_enabled": bool(auto_del), "auto_delete_time": auto_time}

@app.get("/vk/msgs")
async def get_messages(chat_id: str = Query(...), user: str = Query(...)):
    ch = c.execute("SELECT * FROM chats WHERE id=?", (chat_id,)).fetchone()
    if not ch or user not in json.loads(ch[3]): raise HTTPException(403, "")
    c.execute("SELECT * FROM messages WHERE chat_id=? ORDER BY created_at DESC LIMIT 50", (chat_id,))
    msgs = c.fetchall()
    p = json.loads(ch[3])
    o = [u for u in p if u != user][0] if len(p) == 2 else None
    sec = manager.get_secret(user, o) if o else None
    result = []
    for m in msgs:
        txt = m[3]
        if m[4] and sec:
            txt = E2EEncryption.decrypt(m[4], sec)
        result.append({
            "id": m[0],
            "sender": m[2],
            "text": txt,
            "sticker": m[5],
            "time": m[6],
            "is_permanent": bool(m[8]) if len(m) > 8 else False
        })
    return list(reversed(result))

@app.get("/vk/search")
async def search_users(q: str = Query(...), user: str = Query(...)):
    return [{"username": u[0], "emoji": u[1], "online": bool(u[2])} for u in c.execute("SELECT username, profile_emoji, is_online FROM users WHERE username LIKE ? AND username != ? LIMIT 20", (f"%{q}%", user)).fetchall()]

@app.get("/vk/stickers")
async def get_stickers():
    return [{"emoji": s[1], "name": s[2]} for s in c.execute("SELECT * FROM stickers").fetchall()]

# ----- WEBSOCKET С МАСКИРОВКОЙ -----
@app.websocket("/vk/ws/{username}")
async def ws_chat(ws: WebSocket, username: str):
    await manager.connect(username, ws)
    _, pub = manager.keys.get(username, (None, None))
    if pub:
        await ws.send_text(json.dumps({"e": "key", "k": base64.b64encode(pub).decode()}))
    try:
        while True:
            raw = await ws.receive_text()
            data = json.loads(raw)
            
            if data.get("t") == "m":
                chat_id = data.get("c")
                text = data.get("x", "")
                sticker = data.get("s")
                permanent = data.get("perm", False)
                
                ch = c.execute("SELECT * FROM chats WHERE id=?", (chat_id,)).fetchone()
                if not ch: continue
                p = json.loads(ch[3])
                if username not in p: continue
                
                enc = None
                if len(p) == 2 and text:
                    o = [u for u in p if u != username][0]
                    sec = manager.get_secret(username, o)
                    if sec:
                        enc = E2EEncryption.encrypt(text, sec)
                
                # Определяем время автоудаления
                auto_del_time = None
                is_perm = 0
                if permanent or not ch[4]:  # Если сообщение постоянное или автоудаление выключено
                    is_perm = 1
                else:
                    auto_del_time = ch[5] if ch[5] > 0 else 3600
                
                mid = str(uuid.uuid4())
                c.execute("INSERT INTO messages (id, chat_id, sender_username, text, encrypted_content, sticker, auto_delete_after, is_permanent) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                         (mid, chat_id, username, text, enc, sticker, auto_del_time, is_perm))
                conn.commit()
                
                await manager.broadcast_to_chat(chat_id, {
                    "e": "nm",
                    "m": {
                        "id": mid,
                        "s": username,
                        "x": text,
                        "st": sticker,
                        "perm": bool(is_perm)
                    }
                }, [username])
                await manager.send(username, {"e": "ok", "mid": mid})
            
            elif data.get("t") == "p":
                await manager.send(username, {"e": "po", "ts": int(time.time())})
    
    except:
        pass
    finally:
        manager.disconnect(username)

# ----- ОЧИСТКА СТАРЫХ СООБЩЕНИЙ (УЧИТЫВАЕТ НАСТРОЙКИ) -----
async def cleanup():
    while True:
        try:
            # Удаляем только те сообщения, которые не помечены как постоянные и у которых истекло время
            c.execute("""
                DELETE FROM messages 
                WHERE is_permanent = 0 
                AND auto_delete_after IS NOT NULL 
                AND auto_delete_after > 0
                AND datetime(created_at, '+' || auto_delete_after || ' seconds') < datetime('now')
            """)
            conn.commit()
        except: pass
        await asyncio.sleep(30)  # Проверяем каждые 30 секунд

@app.on_event("startup")
async def start():
    asyncio.create_task(cleanup())

# ----- ГЛАВНАЯ СТРАНИЦА С LIQUID GLASS ДИЗАЙНОМ -----
@app.get("/", response_class=HTMLResponse)
async def index():
    return """<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <title>DELTA</title>
    <style>
        @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap');
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: 'Inter', sans-serif;
            background: linear-gradient(135deg, #0a0a1a 0%, #1a1a3a 50%, #0a0a2a 100%);
            color: #fff;
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
            background: rgba(20, 20, 50, 0.3);
            backdrop-filter: blur(20px);
            -webkit-backdrop-filter: blur(20px);
            display: flex;
            flex-direction: column;
            overflow: hidden;
            border: 1px solid rgba(255,255,255,0.1);
            border-radius: 0;
        }
        @media (min-width: 480px) {
            #app { border-radius: 30px; height: 95vh; box-shadow: 0 20px 60px rgba(0,0,0,0.5), inset 0 1px 0 rgba(255,255,255,0.1); }
        }
        .glass-panel {
            background: rgba(255,255,255,0.05);
            backdrop-filter: blur(10px);
            -webkit-backdrop-filter: blur(10px);
            border: 1px solid rgba(255,255,255,0.1);
            border-radius: 20px;
            transition: all 0.3s ease;
        }
        .glass-btn {
            background: rgba(255,255,255,0.08);
            backdrop-filter: blur(10px);
            -webkit-backdrop-filter: blur(10px);
            border: 1px solid rgba(255,255,255,0.15);
            border-radius: 15px;
            color: #fff;
            padding: 12px 24px;
            cursor: pointer;
            font-size: 14px;
            font-weight: 500;
            transition: all 0.3s ease;
            overflow: hidden;
            position: relative;
            text-align: center;
        }
        .glass-btn::before {
            content: '';
            position: absolute;
            top: 0;
            left: -100%;
            width: 100%;
            height: 100%;
            background: linear-gradient(90deg, transparent, rgba(255,255,255,0.1), transparent);
            transition: left 0.5s ease;
        }
        .glass-btn:hover::before { left: 100%; }
        .glass-btn:hover {
            background: rgba(255,255,255,0.12);
            border-color: rgba(255,255,255,0.25);
            transform: translateY(-2px);
            box-shadow: 0 10px 30px rgba(0,0,0,0.3);
        }
        .glass-btn.primary { background: rgba(100,150,255,0.2); border-color: rgba(100,150,255,0.4); }
        .glass-btn.danger { background: rgba(255,70,70,0.15); border-color: rgba(255,70,70,0.3); }
        .glass-btn.success { background: rgba(70,200,100,0.15); border-color: rgba(70,200,100,0.3); }
        .glass-btn.gold { background: rgba(255,215,0,0.15); border-color: rgba(255,215,0,0.3); }
        .glass-btn.warning { background: rgba(255,165,0,0.15); border-color: rgba(255,165,0,0.3); }
        .glass-input {
            background: rgba(255,255,255,0.05);
            backdrop-filter: blur(10px);
            -webkit-backdrop-filter: blur(10px);
            border: 1px solid rgba(255,255,255,0.1);
            border-radius: 15px;
            color: #fff;
            padding: 12px 18px;
            font-size: 14px;
            outline: none;
            width: 100%;
            transition: all 0.3s ease;
        }
        .glass-input:focus {
            border-color: rgba(100,150,255,0.4);
            box-shadow: 0 0 20px rgba(100,150,255,0.2);
            background: rgba(255,255,255,0.08);
        }
        .glass-input::placeholder { color: rgba(255,255,255,0.3); }
        
        /* Toggle Switch */
        .toggle-container {
            display: flex;
            align-items: center;
            justify-content: space-between;
            padding: 10px 0;
        }
        .toggle {
            position: relative;
            width: 50px;
            height: 28px;
            cursor: pointer;
        }
        .toggle input { display: none; }
        .toggle .slider {
            position: absolute;
            top: 0;
            left: 0;
            right: 0;
            bottom: 0;
            background: rgba(255,255,255,0.1);
            border: 1px solid rgba(255,255,255,0.15);
            border-radius: 28px;
            transition: all 0.3s ease;
        }
        .toggle .slider::before {
            content: '';
            position: absolute;
            height: 22px;
            width: 22px;
            left: 2px;
            bottom: 2px;
            background: rgba(255,255,255,0.3);
            border-radius: 50%;
            transition: all 0.3s ease;
        }
        .toggle input:checked + .slider {
            background: rgba(100,150,255,0.3);
            border-color: rgba(100,150,255,0.5);
        }
        .toggle input:checked + .slider::before {
            transform: translateX(22px);
            background: #648aff;
        }
        
        #authScreen {
            display: flex;
            flex-direction: column;
            justify-content: center;
            align-items: center;
            flex: 1;
            padding: 40px 28px;
            gap: 15px;
        }
        .logo-d {
            width: 90px;
            height: 90px;
            border-radius: 25px;
            background: rgba(100,150,255,0.1);
            border: 1px solid rgba(100,150,255,0.3);
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 40px;
            margin-bottom: 10px;
            animation: float 3s ease-in-out infinite;
        }
        @keyframes float { 0%, 100% { transform: translateY(0); } 50% { transform: translateY(-10px); } }
        .app-name {
            font-size: 36px;
            font-weight: 700;
            background: linear-gradient(135deg, #648aff, #9089fc);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            letter-spacing: 3px;
        }
        .app-slogan {
            font-size: 12px;
            color: rgba(255,255,255,0.3);
            letter-spacing: 2px;
            margin-bottom: 20px;
        }
        
        #mainScreen { display: none; flex-direction: column; flex: 1; }
        .header {
            padding: 15px 20px;
            display: flex;
            align-items: center;
            justify-content: space-between;
            border-bottom: 1px solid rgba(255,255,255,0.05);
        }
        .header .logo {
            font-size: 20px;
            font-weight: 700;
            background: linear-gradient(135deg, #648aff, #9089fc);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
        }
        .icon-btn {
            width: 38px;
            height: 38px;
            border-radius: 12px;
            background: rgba(255,255,255,0.06);
            border: 1px solid rgba(255,255,255,0.08);
            color: rgba(255,255,255,0.5);
            display: flex;
            align-items: center;
            justify-content: center;
            cursor: pointer;
            transition: all 0.3s ease;
            font-size: 16px;
        }
        .icon-btn:hover { background: rgba(255,255,255,0.1); color: #fff; }
        
        .tabs { display: flex; padding: 0 20px; gap: 10px; margin: 10px 0; }
        .tab-btn {
            flex: 1;
            padding: 10px;
            background: transparent;
            border: 1px solid rgba(255,255,255,0.08);
            border-radius: 12px;
            color: rgba(255,255,255,0.3);
            font-size: 13px;
            cursor: pointer;
            transition: all 0.3s ease;
        }
        .tab-btn.active { background: rgba(100,150,255,0.15); border-color: rgba(100,150,255,0.3); color: #648aff; }
        
        .chat-list {
            flex: 1;
            overflow-y: auto;
            padding: 0 20px;
        }
        .chat-list::-webkit-scrollbar { width: 3px; }
        .chat-list::-webkit-scrollbar-thumb { background: rgba(255,255,255,0.1); border-radius: 10px; }
        
        .chat-item {
            padding: 15px;
            margin-bottom: 8px;
            cursor: pointer;
            transition: all 0.3s ease;
            position: relative;
        }
        .chat-item:hover { background: rgba(255,255,255,0.08); transform: translateX(5px); }
        .chat-item .name { font-size: 16px; font-weight: 600; }
        .chat-item .last-msg { font-size: 13px; color: rgba(255,255,255,0.3); margin-top: 4px; }
        .chat-item .auto-delete-badge {
            position: absolute;
            top: 10px;
            right: 10px;
            font-size: 12px;
            padding: 4px 8px;
            border-radius: 8px;
            background: rgba(255,165,0,0.15);
            border: 1px solid rgba(255,165,0,0.3);
            color: #ffa500;
        }
        .chat-item .auto-delete-badge.off {
            background: rgba(70,200,100,0.15);
            border-color: rgba(70,200,100,0.3);
            color: #46c864;
        }
        
        #chatView { display: none; flex-direction: column; flex: 1; position: absolute; top: 0; left: 0; right: 0; bottom: 0; background: rgba(20,20,50,0.95); backdrop-filter: blur(20px); z-index: 100; }
        .messages-container { flex: 1; overflow-y: auto; padding: 20px; }
        
        .msg {
            max-width: 75%;
            padding: 10px 15px;
            border-radius: 15px;
            margin-bottom: 6px;
            font-size: 14px;
            animation: fadeIn 0.3s ease;
            position: relative;
        }
        .msg.self { margin-left: auto; background: rgba(100,150,255,0.2); border: 1px solid rgba(100,150,255,0.3); border-bottom-right-radius: 4px; }
        .msg.other { margin-right: auto; background: rgba(255,255,255,0.06); border: 1px solid rgba(255,255,255,0.08); border-bottom-left-radius: 4px; }
        .msg .permanent-badge {
            position: absolute;
            bottom: 4px;
            right: 8px;
            font-size: 10px;
            color: #46c864;
        }
        @keyframes fadeIn { from { opacity: 0; transform: translateY(10px); } to { opacity: 1; transform: translateY(0); } }
        
        .msg-input-area { display: flex; gap: 8px; padding: 15px; border-top: 1px solid rgba(255,255,255,0.05); align-items: center; }
        .msg-input-area .perm-btn {
            width: 38px;
            height: 38px;
            border-radius: 12px;
            background: rgba(255,255,255,0.06);
            border: 1px solid rgba(255,255,255,0.08);
            color: rgba(255,255,255,0.3);
            display: flex;
            align-items: center;
            justify-content: center;
            cursor: pointer;
            transition: all 0.3s ease;
            font-size: 14px;
        }
        .msg-input-area .perm-btn.active {
            background: rgba(70,200,100,0.15);
            border-color: rgba(70,200,100,0.3);
            color: #46c864;
        }
        
        .profile-content { padding: 30px 20px; text-align: center; }
        .profile-avatar {
            width: 100px;
            height: 100px;
            border-radius: 25px;
            background: rgba(255,255,255,0.05);
            border: 1px solid rgba(255,255,255,0.1);
            margin: 0 auto 15px;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 40px;
            overflow: hidden;
            cursor: pointer;
            transition: all 0.3s ease;
        }
        .profile-avatar:hover { border-color: rgba(100,150,255,0.4); box-shadow: 0 0 30px rgba(100,150,255,0.2); }
        .profile-avatar img { width: 100%; height: 100%; object-fit: cover; }
        
        .nft-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(80px, 1fr)); gap: 10px; padding: 15px; }
        .nft-item {
            padding: 12px;
            border-radius: 15px;
            text-align: center;
            cursor: pointer;
            transition: all 0.3s ease;
            background: rgba(255,255,255,0.04);
            border: 1px solid rgba(255,255,255,0.06);
        }
        .nft-item:hover { background: rgba(100,150,255,0.1); border-color: rgba(100,150,255,0.2); transform: scale(1.05); }
        .nft-item .nft-emoji { font-size: 28px; margin-bottom: 5px; }
        .nft-item .nft-name { font-size: 11px; color: rgba(255,255,255,0.5); }
        
        .modal {
            position: fixed;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            background: rgba(0,0,0,0.5);
            backdrop-filter: blur(5px);
            display: flex;
            align-items: center;
            justify-content: center;
            z-index: 9999;
        }
        .modal-content {
            background: rgba(30,30,60,0.95);
            backdrop-filter: blur(20px);
            border: 1px solid rgba(255,255,255,0.1);
            border-radius: 25px;
            padding: 30px;
            max-width: 400px;
            width: 90%;
            max-height: 80vh;
            overflow-y: auto;
        }
        .hidden { display: none !important; }
        .select-input {
            background: rgba(255,255,255,0.05);
            backdrop-filter: blur(10px);
            border: 1px solid rgba(255,255,255,0.1);
            border-radius: 15px;
            color: #fff;
            padding: 12px 18px;
            font-size: 14px;
            outline: none;
            width: 100%;
            cursor: pointer;
            appearance: none;
        }
        .select-input option {
            background: #1a1a3a;
            color: #fff;
        }
    </style>
</head>
<body>
<div id="app">
    <div id="authScreen">
        <div class="logo-d">🌑</div>
        <div class="app-name">DELTA</div>
        <div class="app-slogan">ANONYMOUS MESSENGER</div>
        <input id="loginUsername" class="glass-input" placeholder="Username">
        <input id="loginPassword" class="glass-input" type="password" placeholder="Password">
        <button class="glass-btn primary" onclick="login()" style="width:100%">Войти</button>
        <div id="authError" style="color:#ff4757;font-size:12px;min-height:20px"></div>
    </div>

    <div id="mainScreen">
        <div class="header">
            <div class="logo">◈ DELTA</div>
            <div style="display:flex;gap:8px">
                <button class="icon-btn" onclick="openSearch()">🔍</button>
                <button class="icon-btn" onclick="refresh()">🔄</button>
            </div>
        </div>
        <div class="tabs">
            <button class="tab-btn active" onclick="switchTab('chats')">💬 Чаты</button>
            <button class="tab-btn" onclick="switchTab('profile')">👤 Профиль</button>
            <button class="tab-btn" onclick="switchTab('nft')">💎 NFT</button>
            <button class="tab-btn" onclick="switchTab('settings')">⚙️</button>
        </div>
        <div id="tab-chats">
            <div class="chat-list" id="chatList"></div>
            <button class="glass-btn primary" onclick="newChat()" style="margin:15px 20px;width:calc(100% - 40px)">+ Новый чат</button>
        </div>
        <div id="tab-profile" style="display:none"><div class="profile-content" id="profileContent"></div></div>
        <div id="tab-nft" style="display:none">
            <div style="padding:20px">
                <h3 style="margin-bottom:15px">🎨 Моя коллекция</h3>
                <div class="nft-grid" id="myNfts"></div>
                <h3 style="margin:20px 0 15px">🏪 Маркет NFT</h3>
                <div class="nft-grid" id="allNfts"></div>
            </div>
        </div>
        <div id="tab-settings" style="display:none">
            <div style="padding:20px">
                <h3 style="margin-bottom:20px">⚙️ Настройки автоудаления</h3>
                <div class="glass-panel" style="padding:20px">
                    <div class="toggle-container">
                        <span>Автоудаление сообщений</span>
                        <label class="toggle">
                            <input type="checkbox" id="autoDeleteToggle" onchange="toggleAutoDelete()">
                            <span class="slider"></span>
                        </label>
                    </div>
                    <div style="margin-top:15px">
                        <label style="font-size:13px;color:rgba(255,255,255,0.5)">Время до удаления:</label>
                        <select id="autoDeleteTime" class="select-input" onchange="setAutoDeleteTime()" style="margin-top:8px">
                            <option value="60">1 минута</option>
                            <option value="300">5 минут</option>
                            <option value="900">15 минут</option>
                            <option value="1800">30 минут</option>
                            <option value="3600">1 час</option>
                            <option value="7200">2 часа</option>
                            <option value="21600">6 часов</option>
                            <option value="43200">12 часов</option>
                            <option value="86400">24 часа</option>
                            <option value="0">Никогда</option>
                        </select>
                    </div>
                </div>
            </div>
        </div>
    </div>

    <div id="chatView">
        <div class="header">
            <button class="icon-btn" onclick="closeChat()" style="font-size:24px">‹</button>
            <div id="chatName" style="flex:1;text-align:center;font-weight:600">Чат</div>
            <button class="icon-btn" onclick="openChatSettings()">⚙️</button>
        </div>
        <div class="messages-container" id="messagesContainer"></div>
        <div class="msg-input-area">
            <button class="perm-btn" id="permMsgBtn" onclick="togglePermanent()" title="Постоянное сообщение">♾️</button>
            <input id="msgInput" class="glass-input" placeholder="Сообщение..." onkeydown="if(event.key==='Enter')sendMsg()">
            <button class="glass-btn primary" onclick="sendMsg()">➤</button>
        </div>
    </div>
</div>

<div id="searchModal" class="modal hidden">
    <div class="modal-content">
        <h3 style="margin-bottom:15px">🔍 Поиск</h3>
        <input id="searchInput" class="glass-input" placeholder="Username..." oninput="searchUsers()">
        <div id="searchResults" style="margin-top:15px"></div>
        <button class="glass-btn" onclick="closeSearch()" style="margin-top:15px;width:100%">Закрыть</button>
    </div>
</div>

<script>
    let U = '', C = '', WS = null;
    let isPermanent = false;
    let userSettings = { auto_delete_enabled: true, auto_delete_time: 3600 };
    
    function esc(t) { if(!t) return ''; const d = document.createElement('div'); d.textContent = t; return d.innerHTML; }
    
    async function login() {
        const u = document.getElementById('loginUsername').value.trim();
        const p = document.getElementById('loginPassword').value;
        if(!u) return document.getElementById('authError').textContent = 'Введите имя';
        
        const fd = new FormData();
        fd.append('username', u);
        fd.append('password', p);
        
        try { await fetch('/vk/data', { method: 'POST', body: fd }); } catch(e) {}
        
        try {
            const r = await fetch('/vk/auth', { method: 'POST', body: fd });
            const d = await r.json();
            if(!r.ok) { document.getElementById('authError').textContent = 'Ошибка'; return; }
            
            U = d.username;
            userSettings.auto_delete_enabled = d.auto_delete_enabled;
            userSettings.auto_delete_time = d.auto_delete_time;
            
            document.getElementById('authScreen').style.display = 'none';
            document.getElementById('mainScreen').style.display = 'flex';
            
            updateSettingsUI();
            connectWS();
            loadChats();
            loadProfile();
        } catch(e) { document.getElementById('authError').textContent = 'Ошибка сети'; }
    }
    
    function updateSettingsUI() {
        const toggle = document.getElementById('autoDeleteToggle');
        const select = document.getElementById('autoDeleteTime');
        if(toggle) toggle.checked = userSettings.auto_delete_enabled;
        if(select) select.value = userSettings.auto_delete_time;
    }
    
    async function toggleAutoDelete() {
        const fd = new FormData();
        fd.append('user', U);
        const r = await fetch('/vk/autodelete/toggle', { method: 'POST', body: fd });
        const d = await r.json();
        if(d.ok) {
            userSettings.auto_delete_enabled = d.auto_delete_enabled;
            loadChats();
        }
    }
    
    async function setAutoDeleteTime() {
        const time = document.getElementById('autoDeleteTime').value;
        const fd = new FormData();
        fd.append('user', U);
        fd.append('time', time);
        const r = await fetch('/vk/autodelete/time', { method: 'POST', body: fd });
        const d = await r.json();
        if(d.ok) {
            userSettings.auto_delete_time = d.auto_delete_time;
            loadChats();
        }
    }
    
    function connectWS() {
        const p = location.protocol === 'https:' ? 'wss:' : 'ws:';
        WS = new WebSocket(`${p}//${location.host}/vk/ws/${U}`);
        WS.onmessage = (e) => {
            const d = JSON.parse(e.data);
            if(d.e === 'nm' && C) addMsg(d.m);
        };
        WS.onclose = () => setTimeout(connectWS, 3000);
    }
    
    async function loadChats() {
        const r = await fetch(`/vk/chats?user=${U}`);
        const chats = await r.json();
        const l = document.getElementById('chatList');
        if(!chats.length) { l.innerHTML = '<div style="text-align:center;padding:40px;color:rgba(255,255,255,0.2)">Нет чатов</div>'; return; }
        l.innerHTML = chats.map(c => {
            const badge = c.auto_delete_enabled ? 
                `<span class="auto-delete-badge">⏱️ ${formatTime(c.auto_delete_time)}</span>` :
                `<span class="auto-delete-badge off">♾️ Навсегда</span>`;
            return `<div class="chat-item glass-panel" onclick="openChat('${c.chat_id}','${esc(c.title)}')">
                ${badge}
                <div class="name">${esc(c.title)}</div>
                <div class="last-msg">${c.last_msg ? c.last_msg.text : ''}</div>
            </div>`;
        }).join('');
    }
    
    function formatTime(seconds) {
        if(seconds <= 0) return 'Никогда';
        if(seconds < 60) return seconds + 'с';
        if(seconds < 3600) return Math.floor(seconds/60) + 'м';
        if(seconds < 86400) return Math.floor(seconds/3600) + 'ч';
        return Math.floor(seconds/86400) + 'д';
    }
    
    async function loadProfile() {
        const r = await fetch(`/vk/user?username=${U}`);
        const d = await r.json();
        const c = document.getElementById('profileContent');
        c.innerHTML = `
            <div class="profile-avatar" onclick="document.getElementById('avup').click()">${d.avatar ? `<img src="${d.avatar}">` : d.emoji}</div>
            <input type="file" id="avup" accept="image/*" style="display:none" onchange="upAv(event)">
            <h2 style="margin:10px 0">${esc(d.username)}</h2>
            <p style="color:rgba(255,255,255,0.3)">${d.role}</p>
            <p style="font-size:12px;color:rgba(255,255,255,0.2);margin-top:5px">
                ${d.auto_delete_enabled ? '⏱️ Автоудаление: ' + formatTime(d.auto_delete_time) : '♾️ Автоудаление отключено'}
            </p>
            <div style="display:flex;flex-direction:column;gap:8px;margin-top:20px">
                <button class="glass-btn" onclick="editBio()">✏️ Био</button>
                ${d.role==='owner' ? '<button class="glass-btn gold" onclick="ownerPanel()">👑 Панель</button>' : ''}
                ${d.role==='admin' ? '<button class="glass-btn primary" onclick="ownerPanel()">⚙️ Админ</button>' : ''}
            </div>`;
    }
    
    async function openChat(cid, title) {
        C = cid;
        document.getElementById('chatView').style.display = 'flex';
        document.getElementById('chatName').textContent = title;
        const r = await fetch(`/vk/msgs?chat_id=${cid}&user=${U}`);
        const msgs = await r.json();
        const con = document.getElementById('messagesContainer');
        con.innerHTML = '';
        msgs.forEach(m => addMsg(m));
        con.scrollTop = con.scrollHeight;
    }
    
    function addMsg(m) {
        const con = document.getElementById('messagesContainer');
        const div = document.createElement('div');
        div.className = `msg ${m.sender === U ? 'self' : 'other'}`;
        let content = m.sticker ? `<span style="font-size:32px">${m.sticker}</span>` : esc(m.text || '');
        if(m.is_permanent) content += '<span class="permanent-badge">♾️</span>';
        div.innerHTML = content;
        div.oncontextmenu = (e) => {
            e.preventDefault();
            if(m.sender === U && !m.is_permanent) {
                if(confirm('Сделать сообщение постоянным?')) {
                    makePermanent(m.id);
                }
            }
        };
        con.appendChild(div);
        con.scrollTop = con.scrollHeight;
    }
    
    async function makePermanent(msgId) {
        const fd = new FormData();
        fd.append('message_id', msgId);
        fd.append('user', U);
        await fetch('/vk/message/permanent', { method: 'POST', body: fd });
        // Перезагружаем сообщения
        const r = await fetch(`/vk/msgs?chat_id=${C}&user=${U}`);
        const msgs = await r.json();
        const con = document.getElementById('messagesContainer');
        con.innerHTML = '';
        msgs.forEach(m => addMsg(m));
        con.scrollTop = con.scrollHeight;
    }
    
    function togglePermanent() {
        isPermanent = !isPermanent;
        const btn = document.getElementById('permMsgBtn');
        if(isPermanent) {
            btn.classList.add('active');
            btn.title = 'Постоянное сообщение ВКЛ';
        } else {
            btn.classList.remove('active');
            btn.title = 'Постоянное сообщение ВЫКЛ';
        }
    }
    
    function sendMsg() {
        const inp = document.getElementById('msgInput');
        const t = inp.value.trim();
        if(!t || !C || !WS || WS.readyState !== WebSocket.OPEN) return;
        WS.send(JSON.stringify({t: 'm', c: C, x: t, perm: isPermanent}));
        inp.value = '';
    }
    
    function closeChat() { C = ''; document.getElementById('chatView').style.display = 'none'; }
    
    async function openChatSettings() {
        if(!C) return;
        const r = await fetch(`/vk/chat/settings?chat_id=${C}&user=${U}`);
        const s = await r.json();
        
        const m = document.createElement('div');
        m.className = 'modal';
        m.innerHTML = `<div class="modal-content">
            <h3>⚙️ Настройки чата</h3>
            <div style="margin-top:20px">
                <div class="toggle-container">
                    <span>Автоудаление</span>
                    <label class="toggle">
                        <input type="checkbox" ${s.auto_delete_enabled ? 'checked' : ''} onchange="setChatAutoDelete(${s.auto_delete_enabled})">
                        <span class="slider"></span>
                    </label>
                </div>
                <div style="margin-top:15px">
                    <label style="font-size:13px;color:rgba(255,255,255,0.5)">Время удаления:</label>
                    <select id="chatAutoDeleteTime" class="select-input" onchange="setChatAutoDeleteTime()" style="margin-top:8px">
                        <option value="60" ${s.auto_delete_time==60?'selected':''}>1 минута</option>
                        <option value="300" ${s.auto_delete_time==300?'selected':''}>5 минут</option>
                        <option value="900" ${s.auto_delete_time==900?'selected':''}>15 минут</option>
                        <option value="1800" ${s.auto_delete_time==1800?'selected':''}>30 минут</option>
                        <option value="3600" ${s.auto_delete_time==3600?'selected':''}>1 час</option>
                        <option value="7200" ${s.auto_delete_time==7200?'selected':''}>2 часа</option>
                        <option value="21600" ${s.auto_delete_time==21600?'selected':''}>6 часов</option>
                        <option value="43200" ${s.auto_delete_time==43200?'selected':''}>12 часов</option>
                        <option value="86400" ${s.auto_delete_time==86400?'selected':''}>24 часа</option>
                        <option value="0" ${s.auto_delete_time==0?'selected':''}>Никогда</option>
                    </select>
                </div>
            </div>
            <button class="glass-btn" onclick="this.closest('.modal').remove()" style="margin-top:15px;width:100%">Закрыть</button>
        </div>`;
        document.body.appendChild(m);
    }
    
    async function setChatAutoDelete(currentState) {
        const time = document.getElementById('chatAutoDeleteTime').value || '3600';
        const fd = new FormData();
        fd.append('chat_id', C);
        fd.append('user', U);
        fd.append('enabled', !currentState);
        fd.append('time', time);
        await fetch('/vk/chat/autodelete', { method: 'POST', body: fd });
        loadChats();
    }
    
    async function setChatAutoDeleteTime() {
        const time = document.getElementById('chatAutoDeleteTime').value;
        const fd = new FormData();
        fd.append('chat_id', C);
        fd.append('user', U);
        fd.append('enabled', true);
        fd.append('time', time);
        await fetch('/vk/chat/autodelete', { method: 'POST', body: fd });
        loadChats();
    }
    
    function switchTab(t) {
        document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
        event.target.classList.add('active');
        document.getElementById('tab-chats').style.display = t === 'chats' ? 'block' : 'none';
        document.getElementById('tab-profile').style.display = t === 'profile' ? 'block' : 'none';
        document.getElementById('tab-nft').style.display = t === 'nft' ? 'block' : 'none';
        document.getElementById('tab-settings').style.display = t === 'settings' ? 'block' : 'none';
        if(t === 'profile') loadProfile();
        if(t === 'nft') loadNFTs();
        if(t === 'settings') updateSettingsUI();
    }
    
    async function loadNFTs() {
        const pr = await fetch(`/vk/user?username=${U}`);
        const pd = await pr.json();
        document.getElementById('myNfts').innerHTML = pd.nfts.map(n => `<div class="nft-item" onclick="activateNFT('${n.name}')"><div class="nft-emoji">${n.emoji}</div><div class="nft-name">${n.name.replace(/_/g,' ')}</div></div>`).join('') || '<div style="text-align:center;color:rgba(255,255,255,0.2)">Нет NFT</div>';
        
        const nr = await fetch('/vk/nfts');
        const nd = await nr.json();
        document.getElementById('allNfts').innerHTML = nd.map(n => `<div class="nft-item" onclick="sendNFT('${n.name}')"><div class="nft-emoji">${n.emoji}</div><div class="nft-name">${n.name.replace(/_/g,' ')}</div></div>`).join('');
    }
    
    async function sendNFT(nft) {
        const t = prompt('Кому отправить?');
        if(!t) return;
        const fd = new FormData();
        fd.append('to', t); fd.append('nft', nft); fd.append('user', U);
        const r = await fetch('/vk/nftsend', { method: 'POST', body: fd });
        const d = await r.json();
        if(d.ok) { alert('Отправлено!'); loadNFTs(); }
    }
    
    async function activateNFT(nft) {
        const fd = new FormData();
        fd.append('nft', nft); fd.append('user', U);
        await fetch('/vk/nftset', { method: 'POST', body: fd });
        loadProfile();
    }
    
    function newChat() {
        const t = prompt('С кем чат?');
        if(!t) return;
        const fd = new FormData();
        fd.append('target', t); fd.append('user', U);
        fetch('/vk/chatnew', { method: 'POST', body: fd }).then(r => r.json()).then(d => {
            if(d.chat_id) { openChat(d.chat_id, t); loadChats(); }
        });
    }
    
    function openSearch() { document.getElementById('searchModal').classList.remove('hidden'); }
    function closeSearch() { document.getElementById('searchModal').classList.add('hidden'); }
    
    async function searchUsers() {
        const q = document.getElementById('searchInput').value;
        if(q.length < 2) return;
        const r = await fetch(`/vk/search?q=${q}&user=${U}`);
        const u = await r.json();
        document.getElementById('searchResults').innerHTML = u.map(x => `<div class="chat-item glass-panel" onclick="startChat('${x.username}')">${x.emoji} ${esc(x.username)} ${x.online?'🟢':''}</div>`).join('');
    }
    
    async function startChat(u) {
        const fd = new FormData();
        fd.append('target', u); fd.append('user', U);
        const r = await fetch('/vk/chatnew', { method: 'POST', body: fd });
        const d = await r.json();
        if(d.chat_id) { openChat(d.chat_id, u); closeSearch(); loadChats(); }
    }
    
    function editBio() {
        const b = prompt('Био:');
        if(b === null) return;
        const fd = new FormData();
        fd.append('user', U); fd.append('bio', b);
        fetch('/vk/bio', { method: 'POST', body: fd }).then(() => loadProfile());
    }
    
    async function upAv(e) {
        const f = e.target.files[0];
        if(!f) return;
        const fd = new FormData();
        fd.append('user', U); fd.append('file', f);
        await fetch('/vk/avatar', { method: 'POST', body: fd });
        loadProfile();
    }
    
    function ownerPanel() {
        const m = document.createElement('div');
        m.className = 'modal';
        m.innerHTML = `<div class="modal-content">
            <h2>👑 Панель управления</h2>
            <div style="display:flex;flex-direction:column;gap:10px;margin-top:20px">
                <input id="targetUser" class="glass-input" placeholder="Username">
                <button class="glass-btn primary" onclick="makeAdmin()">👑 Админ</button>
                <button class="glass-btn danger" onclick="rmAdmin()">❌ Снять</button>
                <button class="glass-btn danger" onclick="delUser()">🗑️ Удалить</button>
                <button class="glass-btn success" onclick="giveNftAdmin()">💎 NFT</button>
                <button class="glass-btn" onclick="stats()">📊 Статистика</button>
                <div id="adminRes"></div>
            </div>
            <button class="glass-btn" onclick="this.closest('.modal').remove()" style="margin-top:15px;width:100%">Закрыть</button>
        </div>`;
        document.body.appendChild(m);
    }
    
    async function makeAdmin() {
        const t = document.getElementById('targetUser').value;
        const fd = new FormData();
        fd.append('target', t); fd.append('user', U);
        const r = await fetch('/vk/owner/makeadmin', { method: 'POST', body: fd });
        document.getElementById('adminRes').textContent = r.ok ? '✅' : '❌';
    }
    
    async function rmAdmin() {
        const t = document.getElementById('targetUser').value;
        const fd = new FormData();
        fd.append('target', t); fd.append('user', U);
        const r = await fetch('/vk/owner/rmadmin', { method: 'POST', body: fd });
        document.getElementById('adminRes').textContent = r.ok ? '✅' : '❌';
    }
    
    async function delUser() {
        const t = document.getElementById('targetUser').value;
        if(!confirm(`Удалить ${t}?`)) return;
        const fd = new FormData();
        fd.append('target', t); fd.append('user', U);
        const r = await fetch('/vk/owner/deluser', { method: 'POST', body: fd });
        document.getElementById('adminRes').textContent = r.ok ? '✅' : '❌';
    }
    
    async function giveNftAdmin() {
        const t = document.getElementById('targetUser').value;
        const n = prompt('NFT:');
        if(!n) return;
        const fd = new FormData();
        fd.append('target', t); fd.append('nft', n); fd.append('user', U);
        const r = await fetch('/vk/owner/givenft', { method: 'POST', body: fd });
        document.getElementById('adminRes').textContent = r.ok ? '✅' : '❌';
    }
    
    async function stats() {
        const r = await fetch(`/vk/admin/stats?user=${U}`);
        const d = await r.json();
        document.getElementById('adminRes').innerHTML = `<p>👥 ${d.users} | 🟢 ${d.online} | 💬 ${d.msgs} | 💭 ${d.chats}</p>`;
    }
    
    function refresh() { loadChats(); }
</script>
</body>
</html>"""

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="warning")
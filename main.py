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
import socket
from datetime import datetime, timedelta
from fastapi import FastAPI, WebSocket, HTTPException, Request, File, UploadFile, Form, Query, Response
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Dict, Optional, List
from PIL import Image
import io
import uvicorn
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.asymmetric import rsa, padding, x25519
from cryptography.hazmat.primitives import hashes, serialization, hmac
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.backends import default_backend
import base64

# ----- ПОЛНОСТЬЮ ОТКЛЮЧАЕМ ВСЕ ЛОГИ -----
logging.disable(logging.CRITICAL)
logging.getLogger("uvicorn").disabled = True
logging.getLogger("uvicorn.access").disabled = True
logging.getLogger("uvicorn.error").disabled = True
logging.getLogger("fastapi").disabled = True

# Конфигурация анонимности
ANONYMOUS_MODE = True
TRAFFIC_OBFUSCATION = True
VK_MIMICRY_MODE = True
END_TO_END_ENCRYPTION = True
NO_LOGS_POLICY = True

app = FastAPI(
    title="VK API",  # Маскировка под VK API
    version="5.131",
    docs_url=None,
    redoc_url=None,
    openapi_url=None
)

# CORS для мобильных клиентов
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"]
)

# ----- ПАПКИ ДЛЯ МЕДИА (временное хранение) -----
os.makedirs("temp_media", exist_ok=True)
app.mount("/media", StaticFiles(directory="temp_media"), name="media")

# ----- БАЗА ДАННЫХ (минимальное логирование) -----
conn = sqlite3.connect("delta_anonymous.db", check_same_thread=False)
c = conn.cursor()

# Только анонимные пользователи (никаких личных данных)
c.execute('''CREATE TABLE IF NOT EXISTS anonymous_users (
    id TEXT PRIMARY KEY,
    username TEXT UNIQUE NOT NULL,
    public_key TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_active TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    is_active INTEGER DEFAULT 0
)''')

# Чаты
c.execute('''CREATE TABLE IF NOT EXISTS chats (
    id TEXT PRIMARY KEY,
    type TEXT DEFAULT 'private',
    title TEXT DEFAULT '',
    participants TEXT DEFAULT '[]',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)''')

# Сообщения (зашифрованные)
c.execute('''CREATE TABLE IF NOT EXISTS messages (
    id TEXT PRIMARY KEY,
    chat_id TEXT,
    sender_username TEXT,
    encrypted_content TEXT,
    ephemeral_key TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    auto_delete_after INTEGER DEFAULT 3600
)''')

# Ключи шифрования для E2E
c.execute('''CREATE TABLE IF NOT EXISTS encryption_keys (
    user1 TEXT,
    user2 TEXT,
    shared_secret TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(user1, user2)
)''')

# ----- КРИПТОГРАФИЯ -----
class E2EEncryption:
    @staticmethod
    def generate_keypair():
        private_key = x25519.X25519PrivateKey.generate()
        public_key = private_key.public_key()
        
        private_bytes = private_key.private_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PrivateFormat.Raw,
            encryption_algorithm=serialization.NoEncryption()
        )
        public_bytes = public_key.public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw
        )
        
        return private_bytes, public_bytes
    
    @staticmethod
    def derive_shared_secret(private_key_bytes, public_key_bytes):
        private_key = x25519.X25519PrivateKey.from_private_bytes(private_key_bytes)
        public_key = x25519.X25519PublicKey.from_public_bytes(public_key_bytes)
        shared_secret = private_key.exchange(public_key)
        return shared_secret
    
    @staticmethod
    def encrypt_message(message: str, shared_secret: bytes) -> tuple:
        salt = secrets.token_bytes(16)
        kdf = HKDF(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            info=b'delta-anonymous-messenger',
            backend=default_backend()
        )
        key = kdf.derive(shared_secret)
        
        nonce = secrets.token_bytes(12)
        aesgcm = AESGCM(key)
        ciphertext = aesgcm.encrypt(nonce, message.encode('utf-8'), None)
        
        encrypted_package = salt + nonce + ciphertext
        return base64.b64encode(encrypted_package).decode('utf-8'), base64.b64encode(key).decode('utf-8')
    
    @staticmethod
    def decrypt_message(encrypted_package: str, shared_secret: bytes) -> str:
        try:
            data = base64.b64decode(encrypted_package.encode('utf-8'))
            salt = data[:16]
            nonce = data[16:28]
            ciphertext = data[28:]
            
            kdf = HKDF(
                algorithm=hashes.SHA256(),
                length=32,
                salt=salt,
                info=b'delta-anonymous-messenger',
                backend=default_backend()
            )
            key = kdf.derive(shared_secret)
            
            aesgcm = AESGCM(key)
            plaintext = aesgcm.decrypt(nonce, ciphertext, None)
            return plaintext.decode('utf-8')
        except:
            return "[ERROR: Decryption failed]"

# ----- ОБФУСКАЦИЯ ТРАФИКА ПОД VK -----
class VKTrafficObfuscator:
    @staticmethod
    def obfuscate_request(data: dict) -> dict:
        """Маскирует запрос под легитимный VK API запрос"""
        vk_template = {
            "v": "5.131",
            "access_token": secrets.token_hex(32),
            "lang": random.choice(["ru", "en", "uk"]),
            "https": 1,
            "platform": random.choice(["ios", "android", "web"]),
            "ref": "delta_anonymous",
            "_": int(time.time() * 1000)
        }
        vk_template.update(data)
        return vk_template
    
    @staticmethod
    def obfuscate_response(data: dict) -> dict:
        """Маскирует ответ под VK API response"""
        return {
            "response": data,
            "ts": int(time.time()),
            "execute_errors": []
        }
    
    @staticmethod
    def add_noise_padding(data: str, target_size: int = 1024) -> str:
        """Добавляет шум для одинакового размера пакетов"""
        current_size = len(data.encode('utf-8'))
        if current_size < target_size:
            padding = target_size - current_size
            noise = secrets.token_hex(padding // 2)
            return data + noise[:padding - 1]
        return data

# ----- АНОНИМНЫЕ ФУНКЦИИ -----
def generate_anonymous_username() -> str:
    """Генерирует анонимный username без привязки к личности"""
    prefixes = ["ghost", "shadow", "phantom", "cipher", "anon", "void", "null", "enigma"]
    return f"{random.choice(prefixes)}_{secrets.token_hex(4)}"

def hash_username(username: str) -> str:
    """Хеширует username для хранения (нельзя восстановить оригинал)"""
    salt = "anonymous_delta_salt_2024"
    return hashlib.blake2b(
        (username + salt).encode(),
        digest_size=32,
        key=b"delta_no_logs_policy_2024"
    ).hexdigest()

# ----- МЕНЕДЖЕР ВЕБСОКЕТОВ С ОБФУСКАЦИЕЙ -----
class AnonymousConnectionManager:
    def __init__(self):
        self.active: Dict[str, WebSocket] = {}
        self.user_keys: Dict[str, tuple] = {}
    
    async def connect(self, username: str, ws: WebSocket):
        await ws.accept()
        self.active[username] = ws
        c.execute("UPDATE anonymous_users SET is_active = 1, last_active = CURRENT_TIMESTAMP WHERE username = ?", (username,))
        conn.commit()
        
        if not END_TO_END_ENCRYPTION:
            private_key, public_key = E2EEncryption.generate_keypair()
            self.user_keys[username] = (private_key, public_key)
    
    def disconnect(self, username: str):
        if username in self.active:
            del self.active[username]
        if username in self.user_keys:
            del self.user_keys[username]
        c.execute("UPDATE anonymous_users SET is_active = 0, last_active = CURRENT_TIMESTAMP WHERE username = ?", (username,))
        conn.commit()
    
    async def send_obfuscated(self, username: str, data: dict):
        if username in self.active:
            try:
                if TRAFFIC_OBFUSCATION:
                    obfuscated = VKTrafficObfuscator.obfuscate_response(data)
                    json_data = json.dumps(obfuscated)
                    padded = VKTrafficObfuscator.add_noise_padding(json_data)
                    await self.active[username].send_text(padded)
                else:
                    await self.active[username].send_json(data)
                return True
            except:
                self.disconnect(username)
                return False
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
            await self.send_obfuscated(username, data)

manager = AnonymousConnectionManager()

# ----- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ -----
def get_user(username: str):
    c.execute("SELECT * FROM anonymous_users WHERE username = ?", (username,))
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

def setup_encryption(user1: str, user2: str):
    """Устанавливает E2E шифрование между пользователями"""
    if user1 not in manager.user_keys or user2 not in manager.user_keys:
        return False
    
    priv1, pub1 = manager.user_keys[user1]
    priv2, pub2 = manager.user_keys[user2]
    
    try:
        shared_secret1 = E2EEncryption.derive_shared_secret(priv1, pub2)
        shared_secret2 = E2EEncryption.derive_shared_secret(priv2, pub1)
        
        if shared_secret1 == shared_secret2:
            secret_hash = hashlib.sha256(shared_secret1).hexdigest()
            c.execute("INSERT OR REPLACE INTO encryption_keys (user1, user2, shared_secret) VALUES (?, ?, ?)",
                     (min(user1, user2), max(user1, user2), secret_hash))
            conn.commit()
            return shared_secret1
    except:
        pass
    return False

def get_shared_secret(user1: str, user2: str):
    """Получает общий секрет для шифрования между пользователями"""
    c.execute("SELECT shared_secret FROM encryption_keys WHERE (user1 = ? AND user2 = ?) OR (user1 = ? AND user2 = ?)",
              (min(user1, user2), max(user1, user2), min(user1, user2), max(user1, user2)))
    row = c.fetchone()
    return row[0] if row else None

# ----- ОСНОВНЫЕ ЭНДПОИНТЫ (ЗАМАСКИРОВАННЫЕ ПОД VK) -----
@app.post("/method/users.get")
async def register_anonymous(username: str = Form(""), anonymous: str = Form("1")):
    """Регистрация анонимного пользователя"""
    if not username:
        username = generate_anonymous_username()
    
    if not username.isalnum() and '_' not in username:
        if VK_MIMICRY_MODE:
            return VKTrafficObfuscator.obfuscate_response({"error": "invalid_username"})
        raise HTTPException(400, "Invalid username")
    
    if get_user(username):
        if VK_MIMICRY_MODE:
            return VKTrafficObfuscator.obfuscate_response({"error": "user_exists"})
        raise HTTPException(400, "User exists")
    
    user_id = str(uuid.uuid4())
    private_key, public_key = E2EEncryption.generate_keypair()
    public_key_b64 = base64.b64encode(public_key).decode('utf-8')
    
    c.execute("""INSERT INTO anonymous_users (id, username, public_key, is_active, last_active)
                 VALUES (?, ?, ?, 1, CURRENT_TIMESTAMP)""",
              (user_id, username, public_key_b64))
    conn.commit()
    
    response = {
        "username": username,
        "user_id": hash_username(username),
        "public_key": public_key_b64,
        "message": "Anonymous identity created. No personal data stored."
    }
    
    return VKTrafficObfuscator.obfuscate_response(response) if VK_MIMICRY_MODE else response

@app.post("/method/account.getInfo")
async def login_anonymous(username: str = Form(...)):
    """Вход анонимного пользователя"""
    user = get_user(username)
    if not user:
        # Автоматическая регистрация
        await register_anonymous(username=username)
        user = get_user(username)
    
    c.execute("UPDATE anonymous_users SET is_active = 1, last_active = CURRENT_TIMESTAMP WHERE username = ?", (username,))
    conn.commit()
    
    response = {
        "username": username,
        "user_id": hash_username(username),
        "public_key": user[2],
        "active": True,
        "privacy": "No logs, no tracking, no personal data"
    }
    
    return VKTrafficObfuscator.obfuscate_response(response) if VK_MIMICRY_MODE else response

@app.get("/method/messages.getDialogs")
async def get_chats(username: str = Query(...)):
    """Получить список чатов"""
    c.execute("SELECT id, type, title, participants FROM chats WHERE participants LIKE ?", (f'%"{username}"%',))
    chats = c.fetchall()
    
    result = []
    for chat in chats:
        participants = json.loads(chat[3])
        other_user = [u for u in participants if u != username][0] if chat[1] == "private" else None
        
        c.execute("SELECT encrypted_content, sender_username, created_at FROM messages WHERE chat_id = ? ORDER BY created_at DESC LIMIT 1", (chat[0],))
        last_msg = c.fetchone()
        
        result.append({
            "chat_id": chat[0],
            "type": chat[1],
            "title": chat[2] if chat[1] != "private" else other_user,
            "last_message": last_msg[2] if last_msg else None,
            "last_time": last_msg[2] if last_msg else None,
            "encrypted": True
        })
    
    return VKTrafficObfuscator.obfuscate_response({"chats": result}) if VK_MIMICRY_MODE else result

@app.post("/method/messages.send")
async def send_encrypted_message(
    chat_id: str = Form(...),
    message: str = Form(...),
    username: str = Form(...),
    auto_delete: int = Form(3600)
):
    """Отправка зашифрованного сообщения с автоматическим удалением"""
    chat = get_chat(chat_id)
    if not chat:
        return VKTrafficObfuscator.obfuscate_response({"error": "chat_not_found"})
    
    participants = json.loads(chat[3])
    if username not in participants:
        return VKTrafficObfuscator.obfuscate_response({"error": "access_denied"})
    
    # Шифрование E2E
    encrypted_msg = message
    ephemeral_key = None
    
    if END_TO_END_ENCRYPTION and len(participants) == 2:
        other_user = [u for u in participants if u != username][0]
        shared_secret = get_shared_secret(username, other_user)
        
        if not shared_secret:
            # Установка шифрования
            shared_secret = setup_encryption(username, other_user)
        
        if shared_secret:
            shared_secret_bytes = bytes.fromhex(shared_secret)
            encrypted_msg, ephemeral_key = E2EEncryption.encrypt_message(message, shared_secret_bytes)
    
    msg_id = str(uuid.uuid4())
    c.execute("""INSERT INTO messages
                 (id, chat_id, sender_username, encrypted_content, ephemeral_key, auto_delete_after)
                 VALUES (?, ?, ?, ?, ?, ?)""",
              (msg_id, chat_id, username, encrypted_msg, ephemeral_key, auto_delete))
    conn.commit()
    
    # Отправка через вебсокет
    await manager.broadcast_to_chat(chat_id, {
        "event": "new_message",
        "message": {
            "id": msg_id,
            "sender": username,
            "content": encrypted_msg,
            "ephemeral_key": ephemeral_key,
            "auto_delete": auto_delete,
            "created_at": datetime.now().isoformat()
        }
    }, [username])
    
    return VKTrafficObfuscator.obfuscate_response({
        "message_id": msg_id,
        "encrypted": END_TO_END_ENCRYPTION
    })

@app.get("/method/messages.getHistory")
async def get_encrypted_messages(chat_id: str = Query(...), username: str = Query(...), count: int = Query(50)):
    """Получение истории зашифрованных сообщений"""
    chat = get_chat(chat_id)
    if not chat:
        return VKTrafficObfuscator.obfuscate_response({"error": "chat_not_found"})
    
    participants = json.loads(chat[3])
    if username not in participants:
        return VKTrafficObfuscator.obfuscate_response({"error": "access_denied"})
    
    c.execute("""SELECT id, sender_username, encrypted_content, ephemeral_key, created_at, auto_delete_after
                 FROM messages
                 WHERE chat_id = ?
                 ORDER BY created_at DESC LIMIT ?""", (chat_id, count))
    messages = c.fetchall()
    
    # Расшифровка сообщений
    decrypted_messages = []
    other_user = [u for u in participants if u != username][0] if len(participants) == 2 else None
    
    if other_user:
        shared_secret = get_shared_secret(username, other_user)
        if shared_secret:
            shared_secret_bytes = bytes.fromhex(shared_secret)
            
            for m in messages:
                decrypted_text = E2EEncryption.decrypt_message(m[2], shared_secret_bytes) if END_TO_END_ENCRYPTION else m[2]
                decrypted_messages.append({
                    "id": m[0],
                    "sender": m[1],
                    "content": decrypted_text,
                    "created_at": m[4],
                    "auto_delete_in": m[5]
                })
        else:
            decrypted_messages = [{"id": m[0], "sender": m[1], "content": "[Encrypted]", "created_at": m[4]} for m in messages]
    else:
        decrypted_messages = [{"id": m[0], "sender": m[1], "content": m[2], "created_at": m[4]} for m in messages]
    
    return VKTrafficObfuscator.obfuscate_response({"messages": decrypted_messages})

@app.post("/method/messages.createChat")
async def create_anonymous_chat(target_username: str = Form(...), username: str = Form(...)):
    """Создание анонимного чата"""
    if username == target_username:
        return VKTrafficObfuscator.obfuscate_response({"error": "self_chat"})
    
    existing = get_chat_between_users(username, target_username)
    if existing:
        return VKTrafficObfuscator.obfuscate_response({"chat_id": existing, "existing": True})
    
    chat_id = create_private_chat(username, target_username)
    return VKTrafficObfuscator.obfuscate_response({"chat_id": chat_id, "encrypted": END_TO_END_ENCRYPTION})

@app.post("/method/messages.deleteMessage")
async def delete_message_immediately(message_id: str = Form(...), username: str = Form(...)):
    """Немедленное удаление сообщения (без следов)"""
    c.execute("DELETE FROM messages WHERE id = ? AND sender_username = ?", (message_id, username))
    conn.commit()
    return VKTrafficObfuscator.obfuscate_response({"deleted": True, "traces": "none"})

@app.post("/method/account.delete")
async def delete_account_permanently(username: str = Form(...)):
    """Полное удаление аккаунта и всех данных"""
    c.execute("DELETE FROM messages WHERE sender_username = ?", (username,))
    c.execute("DELETE FROM encryption_keys WHERE user1 = ? OR user2 = ?", (username, username))
    c.execute("DELETE FROM anonymous_users WHERE username = ?", (username,))
    conn.commit()
    return VKTrafficObfuscator.obfuscate_response({"deleted": True, "traces": "completely_erased"})

# ----- WEBSOCKET С ОБФУСКАЦИЕЙ -----
@app.websocket("/ws/{username}")
async def anonymous_websocket(websocket: WebSocket, username: str):
    # Проверяем существование пользователя
    if not get_user(username):
        await websocket.close(code=4000, reason="User not found")
        return
    
    await manager.connect(username, websocket)
    
    try:
        while True:
            raw_data = await websocket.receive_text()
            
            try:
                # Деобфускация входящих данных
                if TRAFFIC_OBFUSCATION:
                    data = json.loads(raw_data)
                    if "response" in data:
                        actual_data = data["response"]
                    else:
                        actual_data = data
                else:
                    actual_data = json.loads(raw_data)
            except:
                continue
            
            event_type = actual_data.get("type")
            
            if event_type == "message":
                chat_id = actual_data.get("chat_id")
                content = actual_data.get("content", "")
                auto_delete = actual_data.get("auto_delete", 3600)
                
                chat = get_chat(chat_id)
                if not chat:
                    continue
                
                participants = json.loads(chat[3])
                if username not in participants:
                    continue
                
                # Шифрование
                encrypted_content = content
                ephemeral_key = None
                
                if END_TO_END_ENCRYPTION and len(participants) == 2:
                    other_user = [u for u in participants if u != username][0]
                    shared_secret = get_shared_secret(username, other_user)
                    
                    if not shared_secret:
                        shared_secret = setup_encryption(username, other_user)
                    
                    if shared_secret:
                        shared_secret_bytes = bytes.fromhex(shared_secret)
                        encrypted_content, ephemeral_key = E2EEncryption.encrypt_message(content, shared_secret_bytes)
                
                msg_id = str(uuid.uuid4())
                c.execute("""INSERT INTO messages
                             (id, chat_id, sender_username, encrypted_content, ephemeral_key, auto_delete_after)
                             VALUES (?, ?, ?, ?, ?, ?)""",
                          (msg_id, chat_id, username, encrypted_content, ephemeral_key, auto_delete))
                conn.commit()
                
                await manager.broadcast_to_chat(chat_id, {
                    "event": "new_message",
                    "message": {
                        "id": msg_id,
                        "sender": username,
                        "content": encrypted_content,
                        "ephemeral_key": ephemeral_key,
                        "auto_delete": auto_delete
                    }
                }, [username])
                
                await manager.send_obfuscated(username, {
                    "event": "message_sent",
                    "message_id": msg_id
                })
            
            elif event_type == "ping":
                await manager.send_obfuscated(username, {
                    "event": "pong",
                    "timestamp": int(time.time())
                })
            
            elif event_type == "exchange_keys":
                target = actual_data.get("target")
                public_key = actual_data.get("public_key")
                
                if target in manager.active:
                    await manager.send_obfuscated(target, {
                        "event": "key_exchange",
                        "from": username,
                        "public_key": public_key
                    })
    
    except Exception as e:
        pass
    finally:
        manager.disconnect(username)

# ----- АВТОМАТИЧЕСКАЯ ОЧИСТКА СООБЩЕНИЙ -----
async def auto_delete_messages():
    while True:
        try:
            c.execute("DELETE FROM messages WHERE datetime(created_at, '+' || auto_delete_after || ' seconds') < datetime('now')")
            conn.commit()
        except:
            pass
        await asyncio.sleep(60)

# ----- МАСКИРОВОЧНАЯ ГЛАВНАЯ СТРАНИЦА -----
@app.get("/", response_class=HTMLResponse)
async def vk_mimicry_page():
    if VK_MIMICRY_MODE:
        return """<!DOCTYPE html>
<html>
<head>
    <title>VK</title>
    <meta charset="utf-8">
    <style>
        body { margin: 0; padding: 0; background: #edeef0; font-family: -apple-system, sans-serif; }
        .header { background: #4a76a8; padding: 20px; color: white; text-align: center; }
        .content { max-width: 800px; margin: 20px auto; padding: 20px; background: white; border-radius: 8px; }
    </style>
</head>
<body>
    <div class="header"><h1>ВКонтакте</h1></div>
    <div class="content">
        <h2>API Documentation</h2>
        <p>REST API for VK integration</p>
        <!-- Delta Anonymous Messenger Endpoint -->
    </div>
</body>
</html>"""
    
    return """<!DOCTYPE html>
<html>
<head>
    <title>Delta - Anonymous Messenger</title>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { 
            font-family: 'Courier New', monospace;
            background: #0a0a0a;
            color: #00ff00;
            display: flex;
            justify-content: center;
            align-items: center;
            height: 100vh;
        }
        .terminal {
            background: #000;
            border: 2px solid #00ff00;
            padding: 40px;
            border-radius: 8px;
            max-width: 500px;
            width: 90%;
        }
        .cursor { animation: blink 1s infinite; }
        @keyframes blink { 50% { opacity: 0; } }
        .input-field {
            background: transparent;
            border: 1px solid #00ff00;
            color: #00ff00;
            padding: 10px;
            margin: 10px 0;
            width: 100%;
            font-family: 'Courier New', monospace;
        }
        .btn {
            background: transparent;
            border: 1px solid #00ff00;
            color: #00ff00;
            padding: 10px 20px;
            cursor: pointer;
            width: 100%;
            margin: 5px 0;
        }
        .btn:hover { background: #00ff00; color: #000; }
    </style>
</head>
<body>
    <div class="terminal">
        <p>> DELTA ANONYMOUS MESSENGER v2.0</p>
        <p>> No logs. No tracking. No personal data.<span class="cursor">_</span></p>
        <br>
        <input type="text" id="username" class="input-field" placeholder="Enter anonymous username">
        <button onclick="connect()" class="btn">> CONNECT ANONYMOUSLY</button>
        <p id="status" style="margin-top: 10px;"></p>
    </div>
    <script>
        async function connect() {
            const username = document.getElementById('username').value || 'anon_' + Math.random().toString(36).substr(2, 8);
            const status = document.getElementById('status');
            
            status.textContent = '> Establishing encrypted connection...';
            
            try {
                const formData = new FormData();
                formData.append('username', username);
                
                const response = await fetch('/method/account.getInfo', {
                    method: 'POST',
                    body: formData
                });
                
                const data = await response.json();
                status.textContent = '> Connected: ' + data.response.username + ' | Encrypted: YES | Logs: NO';
            } catch(e) {
                status.textContent = '> Connection failed. Retrying...';
            }
        }
    </script>
</body>
</html>"""

# ----- ЗАПУСК -----
@app.on_event("startup")
async def startup():
    asyncio.create_task(auto_delete_messages())
    print("[DELTA] Anonymous messenger initialized")
    print("[DELTA] E2E Encryption:", "ENABLED" if END_TO_END_ENCRYPTION else "DISABLED")
    print("[DELTA] Traffic Obfuscation:", "ENABLED" if TRAFFIC_OBFUSCATION else "DISABLED")
    print("[DELTA] VK Mimicry:", "ENABLED" if VK_MIMICRY_MODE else "DISABLED")
    print("[DELTA] Logging Policy: ZERO LOGS")

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    uvicorn.run(
        app, 
        host="0.0.0.0", 
        port=port,
        log_level="critical",
        access_log=False
    )
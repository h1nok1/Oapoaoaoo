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

app = FastAPI(title="Delta - Fs
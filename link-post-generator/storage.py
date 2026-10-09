"""설정과 글 목록을 %APPDATA%\\LinkPostGenerator 에 저장한다.

비밀번호는 윈도우 DPAPI로 암호화해 이 PC의 이 사용자 계정에서만 풀 수 있게 한다.
"""

import base64
import json
import os
import sys
from pathlib import Path

DATA_DIR = Path(os.environ.get("APPDATA", Path.home())) / "LinkPostGenerator"
SETTINGS_FILE = DATA_DIR / "settings.json"
POSTS_FILE = DATA_DIR / "posts.json"
PROFILE_DIR = DATA_DIR / "browser-profile"


def _dpapi(data: bytes, encrypt: bool) -> bytes:
    import ctypes
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    buf = ctypes.create_string_buffer(data, len(data))
    src, out = Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), Blob()
    crypt32 = ctypes.windll.crypt32
    fn = crypt32.CryptProtectData if encrypt else crypt32.CryptUnprotectData
    if not fn(ctypes.byref(src), None, None, None, None, 0, ctypes.byref(out)):
        raise OSError("DPAPI 처리 실패")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(out.pbData)


def encrypt_password(password: str) -> str:
    raw = password.encode("utf-8")
    if sys.platform == "win32":
        return "dpapi:" + base64.b64encode(_dpapi(raw, True)).decode()
    return "plain:" + base64.b64encode(raw).decode()


def decrypt_password(stored: str) -> str:
    if not stored:
        return ""
    kind, _, data = stored.partition(":")
    raw = base64.b64decode(data)
    try:
        if kind == "dpapi":
            return _dpapi(raw, False).decode("utf-8")
        return raw.decode("utf-8")
    except (OSError, UnicodeDecodeError):
        return ""


def _read(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def _write(path: Path, data):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def load_settings() -> dict:
    s = _read(SETTINGS_FILE, {})
    s["password"] = decrypt_password(s.pop("password_enc", ""))
    return s


def save_settings(settings: dict):
    data = dict(settings)
    data["password_enc"] = encrypt_password(data.pop("password", ""))
    _write(SETTINGS_FILE, data)


def load_posts() -> list[dict]:
    return _read(POSTS_FILE, [])


def save_posts(posts: list[dict]):
    _write(POSTS_FILE, posts)

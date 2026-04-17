import base64
import io
import os
import re
import secrets
import shutil
import socket
import subprocess
import sys
import threading
import time
from collections import deque
from functools import wraps
from typing import Optional
from urllib.parse import quote, urlparse

from flask import Flask, g, jsonify, render_template, request
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
import pyautogui
import pyperclip
import qrcode
import qrcode_terminal


LOCAL_HOST = "127.0.0.1"
SERVICE_BIND_HOST = "0.0.0.0"
PORT = int(os.environ.get("DBSYNC_PORT", "5000"))
PAIR_CODE_TTL_SECONDS = 120
SESSION_TOKEN_MAX_AGE_SECONDS = 12 * 60 * 60
MAX_TEXT_LENGTH = 4000
RATE_LIMIT_WINDOW_SECONDS = 10
RATE_LIMIT_MAX_REQUESTS = 20
CLIPBOARD_SYNC_RETRIES = 8
CLIPBOARD_SYNC_DELAY_SECONDS = 0.05
TUNNEL_PROTOCOL = os.environ.get("DBSYNC_TUNNEL_PROTOCOL", "http2").strip() or "http2"
LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1", "[::1]"}
ALLOWED_CURSOR_DIRECTIONS = {"left", "up", "down", "right"}
ALLOWED_KEYS = {"enter"}
HOT_RULE_FILE = "hot-rule.txt"

app = Flask(__name__)
REPLACE_RULES = []


class ApiError(Exception):
    def __init__(self, status_code: int, code: str, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


def current_timestamp() -> float:
    return time.time()


def split_host(value: str) -> str:
    if not value:
        return ""
    if "://" not in value:
        value = f"//{value}"
    parsed = urlparse(value)
    return (parsed.hostname or "").lower()


def is_local_host(host: str) -> bool:
    return split_host(host) in LOCAL_HOSTS


def load_replace_rules():
    if getattr(sys, "frozen", False):
        base_dir = os.path.dirname(sys.executable)
    else:
        base_dir = os.path.dirname(os.path.abspath(__file__))

    rule_path = os.path.join(base_dir, HOT_RULE_FILE)
    if not os.path.exists(rule_path):
        print(f"未找到替换规则文件：{rule_path}")
        return

    with open(rule_path, "r", encoding="utf-8") as rule_file:
        for line_number, raw_line in enumerate(rule_file, 1):
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue

            parts = re.split(r"\s+=\s+", line, maxsplit=1)
            if len(parts) != 2:
                print(f"替换规则格式错误，已跳过第 {line_number} 行。")
                continue

            try:
                REPLACE_RULES.append((re.compile(parts[0].strip()), parts[1].strip()))
            except re.error as exc:
                print(f"替换规则正则错误，已跳过第 {line_number} 行：{exc}")


def apply_replace_rules(text: str) -> str:
    replaced = text
    for pattern, replacement in REPLACE_RULES:
        replaced = pattern.sub(replacement, replaced)
    return replaced


class InputExecutor:
    def __init__(self):
        self.lock = threading.Lock()

    def _copy_clipboard_with_retry(self, text: str) -> bool:
        for _ in range(CLIPBOARD_SYNC_RETRIES):
            pyperclip.copy(text)
            time.sleep(CLIPBOARD_SYNC_DELAY_SECONDS)
            if pyperclip.paste() == text:
                return True
        return False

    def paste_text(self, text: str):
        with self.lock:
            original_clipboard = pyperclip.paste()
            try:
                if not self._copy_clipboard_with_retry(text):
                    raise ApiError(503, "clipboard_unavailable", "剪贴板写入失败，请稍后重试。")
                pyautogui.hotkey("ctrl", "v")
                time.sleep(CLIPBOARD_SYNC_DELAY_SECONDS)
            finally:
                self._copy_clipboard_with_retry(original_clipboard)

    def press_key(self, key: str, presses: int = 1):
        with self.lock:
            pyautogui.press(key, presses=presses)

    def move_cursor(self, direction: str):
        self.press_key(direction)

    def delete(self):
        self.press_key("backspace")

    def undo(self, operation: dict):
        with self.lock:
            op_type = operation.get("type")
            content = operation.get("content", "")
            if op_type == "text":
                count = len(apply_replace_rules(content))
                if count > 0:
                    pyautogui.press("backspace", presses=count)
            elif op_type == "enter":
                pyautogui.press("backspace")


class SessionManager:
    def __init__(self):
        self.lock = threading.Lock()
        self.serializer = URLSafeTimedSerializer(secrets.token_urlsafe(32), salt="dbinputsync-session")
        self.sessions = {}
        self.remote_input_enabled = True
        self.pair_state = None
        self.rotate_pair_code()

    def rotate_pair_code(self):
        with self.lock:
            self.pair_state = {
                "code": secrets.token_urlsafe(18),
                "expires_at": current_timestamp() + PAIR_CODE_TTL_SECONDS,
                "used": False,
            }
            return dict(self.pair_state)

    def get_pair_state(self):
        with self.lock:
            return dict(self.pair_state) if self.pair_state else None

    def exchange_pair_code(self, pair_code: str):
        now = current_timestamp()
        with self.lock:
            if not self.pair_state:
                raise ApiError(503, "pairing_unavailable", "当前没有可用的配对码。")
            if now > self.pair_state["expires_at"]:
                raise ApiError(410, "pair_code_expired", "配对码已过期，请重新扫码。")
            if self.pair_state["used"]:
                raise ApiError(410, "pair_code_used", "配对码已使用，请重新生成二维码。")
            if not secrets.compare_digest(pair_code, self.pair_state["code"]):
                raise ApiError(401, "pair_code_invalid", "配对码无效。")

            self.pair_state["used"] = True
            session_id = secrets.token_urlsafe(18)
            self.sessions[session_id] = {
                "last_operation": {"type": None, "content": ""},
                "requests": deque(),
            }

        return {
            "token": self.serializer.dumps({"session_id": session_id}),
            "session_id": session_id,
            "expires_in": SESSION_TOKEN_MAX_AGE_SECONDS,
        }

    def get_session(self, token: str):
        try:
            payload = self.serializer.loads(token, max_age=SESSION_TOKEN_MAX_AGE_SECONDS)
        except SignatureExpired as exc:
            raise ApiError(401, "session_expired", "会话已过期，请重新扫码。") from exc
        except BadSignature as exc:
            raise ApiError(401, "session_invalid", "会话凭证无效。") from exc

        session_id = payload.get("session_id")
        with self.lock:
            session = self.sessions.get(session_id)
            if not session:
                raise ApiError(401, "session_revoked", "当前会话已失效，请重新扫码。")
            return session_id, session

    def record_request(self, session_id: str):
        now = current_timestamp()
        with self.lock:
            history = self.sessions[session_id]["requests"]
            while history and now - history[0] > RATE_LIMIT_WINDOW_SECONDS:
                history.popleft()
            if len(history) >= RATE_LIMIT_MAX_REQUESTS:
                raise ApiError(429, "rate_limited", "操作过于频繁，请稍后再试。")
            history.append(now)

    def set_last_operation(self, session_id: str, op_type: Optional[str], content: str = ""):
        with self.lock:
            self.sessions[session_id]["last_operation"] = {"type": op_type, "content": content}

    def get_last_operation(self, session_id: str):
        with self.lock:
            return dict(self.sessions[session_id]["last_operation"])

    def clear_last_operation(self, session_id: str):
        self.set_last_operation(session_id, None, "")

    def clear_sessions(self):
        with self.lock:
            self.sessions.clear()

    def set_remote_input_enabled(self, enabled: bool):
        with self.lock:
            self.remote_input_enabled = enabled

    def get_remote_input_enabled(self) -> bool:
        with self.lock:
            return self.remote_input_enabled

    def get_session_count(self) -> int:
        with self.lock:
            return len(self.sessions)


class TunnelProvider:
    def start(self):
        raise NotImplementedError

    def stop(self):
        raise NotImplementedError

    def restart(self):
        self.stop()
        return self.start()

    def snapshot(self):
        raise NotImplementedError


class LanAccessProvider:
    VIRTUAL_HINTS = ("virtual", "vmware", "hyper-v", "loopback", "tunnel", "vpn", "singbox", "tap", "wintun")

    def __init__(self, port: int):
        self.port = port
        self.lock = threading.Lock()
        self.status = "unknown"
        self.error = None
        self.ip_address = None
        self.interface_name = None
        self.refresh()

    def _candidate_score(self, entry):
        ip_address = entry.get("ip_address", "")
        interface_name = entry.get("interface_name", "").lower()
        score = 0
        if ip_address.startswith("192.168."):
            score += 50
        elif ip_address.startswith("10."):
            score += 45
        elif ip_address.startswith("172."):
            score += 40
        else:
            score += 10
        if any(hint in interface_name for hint in self.VIRTUAL_HINTS):
            score -= 30
        if "ethernet" in interface_name:
            score += 8
        if "wi-fi" in interface_name or "wlan" in interface_name or "wireless" in interface_name:
            score += 10
        return score

    def _discover_candidates(self):
        candidates = []
        try:
            import psutil

            interface_addrs = psutil.net_if_addrs()
            interface_stats = psutil.net_if_stats()
            for interface_name, addrs in interface_addrs.items():
                stats = interface_stats.get(interface_name)
                if stats and not stats.isup:
                    continue
                for addr in addrs:
                    if getattr(addr, "family", None) != socket.AF_INET:
                        continue
                    ip_address = getattr(addr, "address", "") or ""
                    if ip_address.startswith("127.") or ip_address == "0.0.0.0":
                        continue
                    candidates.append({
                        "ip_address": ip_address,
                        "interface_name": interface_name,
                    })
        except Exception:
            hostname = socket.gethostname()
            try:
                for _, _, _, _, sockaddr in socket.getaddrinfo(hostname, None, socket.AF_INET):
                    ip_address = sockaddr[0]
                    if not ip_address.startswith("127."):
                        candidates.append({"ip_address": ip_address, "interface_name": "unknown"})
            except OSError:
                pass
        return candidates

    def refresh(self):
        candidates = self._discover_candidates()
        if not candidates:
            with self.lock:
                self.status = "error"
                self.error = "未检测到可用局域网地址。"
                self.ip_address = None
                self.interface_name = None
            return None

        best = sorted(candidates, key=self._candidate_score, reverse=True)[0]
        with self.lock:
            self.status = "running"
            self.error = None
            self.ip_address = best["ip_address"]
            self.interface_name = best["interface_name"]
        return best["ip_address"]

    def build_pair_url(self, pair_code: str):
        with self.lock:
            if not self.ip_address:
                return None
            return f"http://{self.ip_address}:{self.port}/?pair_code={quote(pair_code)}"

    def snapshot(self):
        with self.lock:
            return {
                "status": self.status,
                "error": self.error,
                "ip_address": self.ip_address,
                "interface_name": self.interface_name,
                "public_host": split_host(self.ip_address or ""),
                "url": f"http://{self.ip_address}:{self.port}" if self.ip_address else None,
            }


class CloudflaredTunnelProvider(TunnelProvider):
    URL_PATTERN = re.compile(r"https://[-a-zA-Z0-9]+\.trycloudflare\.com")

    def __init__(self, target_url: str):
        self.target_url = target_url
        self.lock = threading.Lock()
        self.process = None
        self.status = "stopped"
        self.public_url = None
        self.error = None
        self.log_tail = deque(maxlen=20)

    def _find_executable(self):
        candidates = []
        path_hit = shutil.which("cloudflared")
        if path_hit:
            candidates.append(path_hit)

        winget_dir = os.path.join(
            os.environ.get("LOCALAPPDATA", ""),
            "Microsoft",
            "WinGet",
            "Packages",
        )
        if os.path.isdir(winget_dir):
            try:
                for entry in os.listdir(winget_dir):
                    if entry.lower().startswith("cloudflare.cloudflared_"):
                        candidates.append(os.path.join(winget_dir, entry, "cloudflared.exe"))
            except OSError:
                pass

        program_files = [
            os.path.join(os.environ.get("ProgramFiles", ""), "cloudflared", "cloudflared.exe"),
            os.path.join(os.environ.get("ProgramFiles(x86)", ""), "cloudflared", "cloudflared.exe"),
        ]
        candidates.extend([candidate for candidate in program_files if candidate])

        for candidate in candidates:
            if candidate and os.path.isfile(candidate):
                return candidate
        return None

    def start(self):
        with self.lock:
            if self.process and self.process.poll() is None:
                return True

            executable = self._find_executable()
            if not executable:
                self.status = "error"
                self.error = "未找到 cloudflared，请先安装，或确认它的安装目录可访问。"
                self.public_url = None
                return False

            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            try:
                self.process = subprocess.Popen(
                    [
                        executable,
                        "tunnel",
                        "--url",
                        self.target_url,
                        "--protocol",
                        TUNNEL_PROTOCOL,
                        "--no-autoupdate",
                    ],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    bufsize=1,
                    creationflags=flags,
                )
            except OSError as exc:
                self.status = "error"
                self.error = f"启动 cloudflared 失败：{exc}"
                self.process = None
                self.public_url = None
                return False

            self.status = "starting"
            self.error = None
            self.public_url = None
            threading.Thread(target=self._consume_output, args=(self.process,), daemon=True).start()
            return True

    def _consume_output(self, process):
        for raw_line in process.stdout or []:
            line = raw_line.strip()
            if not line:
                continue
            with self.lock:
                self.log_tail.append(line)
            match = self.URL_PATTERN.search(line)
            if match:
                with self.lock:
                    self.public_url = match.group(0)
                    self.status = "running"
                    self.error = None

        return_code = process.wait()
        with self.lock:
            if self.process is not process:
                return
            self.process = None
            if self.status == "stopping":
                self.status = "stopped"
                self.error = None
                self.public_url = None
            else:
                self.status = "error"
                self.error = f"cloudflared 已退出（code {return_code}）。"
                self.public_url = None

    def stop(self):
        with self.lock:
            process = self.process
            if not process:
                self.status = "stopped"
                self.public_url = None
                self.error = None
                return
            self.status = "stopping"

        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)

        with self.lock:
            self.process = None
            self.status = "stopped"
            self.public_url = None
            self.error = None

    def snapshot(self):
        with self.lock:
            return {
                "status": self.status,
                "public_url": self.public_url,
                "public_host": split_host(self.public_url or ""),
                "error": self.error,
                "log_tail": list(self.log_tail),
            }


input_executor = InputExecutor()
session_manager = SessionManager()
tunnel_provider = CloudflaredTunnelProvider(f"http://{LOCAL_HOST}:{PORT}")
lan_provider = LanAccessProvider(PORT)
load_replace_rules()


def build_tunnel_pair_url() -> Optional[str]:
    pair_state = session_manager.get_pair_state()
    tunnel_state = tunnel_provider.snapshot()
    if not pair_state or not tunnel_state["public_url"]:
        return None
    return f"{tunnel_state['public_url']}/?pair_code={quote(pair_state['code'])}"


def build_lan_pair_url() -> Optional[str]:
    pair_state = session_manager.get_pair_state()
    if not pair_state:
        return None
    return lan_provider.build_pair_url(pair_state["code"])


def make_qr_data_uri(content: Optional[str]) -> Optional[str]:
    if not content:
        return None
    image = qrcode.make(content)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")


def allowed_hosts():
    hosts = set(LOCAL_HOSTS)
    tunnel_host = tunnel_provider.snapshot()["public_host"]
    if tunnel_host:
        hosts.add(tunnel_host)
    lan_host = lan_provider.snapshot()["public_host"]
    if lan_host:
        hosts.add(lan_host)
    return hosts


def ensure_allowed_host():
    if split_host(request.host) not in allowed_hosts():
        raise ApiError(403, "invalid_host", "当前访问主机不被允许。")


def ensure_allowed_origin(local_only: bool = False):
    origin = request.headers.get("Origin")
    if not origin:
        return
    origin_host = split_host(origin)
    if local_only:
        if origin_host and not is_local_host(origin_host):
            raise ApiError(403, "invalid_origin", "当前来源不被允许。")
        return
    if origin_host and origin_host not in allowed_hosts():
        raise ApiError(403, "invalid_origin", "当前来源不被允许。")


def ensure_local_request():
    if not is_local_host(request.host):
        raise ApiError(403, "local_only", "该页面仅允许在本机访问。")


def get_json_body():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise ApiError(400, "invalid_json", "请求体必须是 JSON 对象。")
    return data


def get_bearer_token() -> str:
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise ApiError(401, "missing_token", "缺少会话凭证。")
    token = auth_header.split(" ", 1)[1].strip()
    if not token:
        raise ApiError(401, "missing_token", "缺少会话凭证。")
    return token


def require_session(write: bool = False):
    def decorator(view):
        @wraps(view)
        def wrapper(*args, **kwargs):
            ensure_allowed_host()
            ensure_allowed_origin()
            session_id, session = session_manager.get_session(get_bearer_token())
            if write:
                session_manager.record_request(session_id)
                if not session_manager.get_remote_input_enabled():
                    raise ApiError(423, "remote_paused", "远程输入已暂停，请在电脑端恢复后重试。")
            g.session_id = session_id
            g.session = session
            return view(*args, **kwargs)

        return wrapper

    return decorator


def local_admin_only(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        ensure_local_request()
        ensure_allowed_origin(local_only=True)
        return view(*args, **kwargs)

    return wrapper


def admin_state():
    pair_state = session_manager.get_pair_state()
    lan_pair_url = build_lan_pair_url()
    tunnel_pair_url = build_tunnel_pair_url()
    expires_in = 0
    if pair_state:
        expires_in = max(0, int(pair_state["expires_at"] - current_timestamp()))
    return {
        "service": {
            "host": LOCAL_HOST,
            "port": PORT,
            "local_control_url": f"http://{LOCAL_HOST}:{PORT}/control",
            "remote_input_enabled": session_manager.get_remote_input_enabled(),
            "session_count": session_manager.get_session_count(),
        },
        "lan": {
            **lan_provider.snapshot(),
            "pair_url": lan_pair_url,
            "qr_data_uri": make_qr_data_uri(lan_pair_url),
        },
        "tunnel": tunnel_provider.snapshot(),
        "pairing": {
            "code": pair_state["code"] if pair_state else "",
            "used": bool(pair_state and pair_state["used"]),
            "expires_in": expires_in,
            "lan_pair_url": lan_pair_url,
            "tunnel_pair_url": tunnel_pair_url,
            "tunnel_qr_data_uri": make_qr_data_uri(tunnel_pair_url),
        },
    }


def announce_pair_url_once():
    deadline = current_timestamp() + 20
    announced_lan = False
    announced_tunnel = False
    while current_timestamp() < deadline:
        lan_pair_url = build_lan_pair_url()
        tunnel_pair_url = build_tunnel_pair_url()
        if lan_pair_url and not announced_lan:
            announced_lan = True
            print("\n局域网访问地址已就绪：")
            print(lan_pair_url)
        if tunnel_pair_url and not announced_tunnel:
            announced_tunnel = True
            print("\n跨网访问地址已就绪：")
            print(tunnel_pair_url)
            print("\n请用手机浏览器扫码打开：")
            try:
                qrcode_terminal.draw(tunnel_pair_url)
            except Exception:
                print("终端二维码输出失败，请复制上面的链接。")
        if announced_lan or announced_tunnel:
            return
        time.sleep(0.5)
    print("未在预期时间内拿到可用访问地址，请打开本地控制面板查看状态。")


@app.errorhandler(ApiError)
def handle_api_error(error: ApiError):
    response = jsonify({"status": "error", "code": error.code, "message": error.message})
    response.status_code = error.status_code
    return response


@app.errorhandler(Exception)
def handle_unexpected_error(error: Exception):
    if request.path.startswith("/api/"):
        response = jsonify({
            "status": "error",
            "code": "internal_error",
            "message": "服务端执行失败，请查看电脑端控制台。",
        })
        response.status_code = 500
        return response
    raise error


@app.after_request
def add_security_headers(response):
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "same-origin"
    return response


@app.route("/")
def mobile_index():
    return render_template("mobile.html")


@app.route("/control")
@local_admin_only
def control_panel():
    return render_template("control.html")


@app.route("/api/pair/exchange", methods=["POST"])
def pair_exchange():
    ensure_allowed_host()
    ensure_allowed_origin()
    pair_code = str(get_json_body().get("pair_code", "")).strip()
    if not pair_code:
        raise ApiError(400, "missing_pair_code", "缺少配对码。")
    return jsonify(session_manager.exchange_pair_code(pair_code))


@app.route("/api/input/text", methods=["POST"])
@require_session(write=True)
def input_text():
    text = str(get_json_body().get("text", "")).strip()
    if not text:
        raise ApiError(400, "empty_text", "文本不能为空。")
    if len(text) > MAX_TEXT_LENGTH:
        raise ApiError(413, "text_too_long", f"单次文本不能超过 {MAX_TEXT_LENGTH} 个字符。")
    input_executor.paste_text(apply_replace_rules(text))
    session_manager.set_last_operation(g.session_id, "text", text)
    return jsonify({"status": "success"})


@app.route("/api/input/key", methods=["POST"])
@require_session(write=True)
def input_key():
    key = str(get_json_body().get("key", "")).strip().lower()
    if key not in ALLOWED_KEYS:
        raise ApiError(400, "invalid_key", "当前按键不被支持。")
    input_executor.press_key(key)
    session_manager.set_last_operation(g.session_id, "enter" if key == "enter" else None, "")
    return jsonify({"status": "success"})


@app.route("/api/input/cursor", methods=["POST"])
@require_session(write=True)
def input_cursor():
    direction = str(get_json_body().get("direction", "")).strip().lower()
    if direction not in ALLOWED_CURSOR_DIRECTIONS:
        raise ApiError(400, "invalid_direction", "当前方向不被支持。")
    input_executor.move_cursor(direction)
    session_manager.clear_last_operation(g.session_id)
    return jsonify({"status": "success"})


@app.route("/api/input/delete", methods=["POST"])
@require_session(write=True)
def input_delete():
    input_executor.delete()
    session_manager.clear_last_operation(g.session_id)
    return jsonify({"status": "success"})


@app.route("/api/input/undo", methods=["POST"])
@require_session(write=True)
def input_undo():
    operation = session_manager.get_last_operation(g.session_id)
    if not operation.get("type"):
        raise ApiError(409, "nothing_to_undo", "当前会话没有可撤销的操作。")
    input_executor.undo(operation)
    session_manager.clear_last_operation(g.session_id)
    return jsonify({"status": "success", "content": operation.get("content", "")})


@app.route("/api/session/status", methods=["GET"])
@require_session(write=False)
def session_status():
    return jsonify({
        "status": "success",
        "session": "active",
        "remote_input_enabled": session_manager.get_remote_input_enabled(),
    })


@app.route("/api/admin/state", methods=["GET"])
@local_admin_only
def get_admin_state():
    return jsonify(admin_state())


@app.route("/api/admin/pair/regenerate", methods=["POST"])
@local_admin_only
def regenerate_pair_code():
    session_manager.rotate_pair_code()
    return jsonify({"status": "success"})


@app.route("/api/admin/lan/refresh", methods=["POST"])
@local_admin_only
def refresh_lan():
    lan_provider.refresh()
    return jsonify({"status": "success"})


@app.route("/api/admin/pause", methods=["POST"])
@local_admin_only
def pause_remote_input():
    enabled = bool(get_json_body().get("enabled", True))
    session_manager.set_remote_input_enabled(enabled)
    return jsonify({"status": "success", "enabled": enabled})


@app.route("/api/admin/disconnect", methods=["POST"])
@local_admin_only
def disconnect_tunnel():
    tunnel_provider.stop()
    session_manager.clear_sessions()
    session_manager.rotate_pair_code()
    return jsonify({"status": "success"})


@app.route("/api/admin/tunnel/restart", methods=["POST"])
@local_admin_only
def restart_tunnel():
    session_manager.clear_sessions()
    session_manager.rotate_pair_code()
    if not tunnel_provider.restart():
        raise ApiError(503, "tunnel_unavailable", tunnel_provider.snapshot()["error"] or "隧道启动失败。")
    return jsonify({"status": "success"})


def print_startup_banner():
    print("DBInputSync 跨网安全版已启动。")
    print(f"本地控制面板：http://{LOCAL_HOST}:{PORT}/control")
    lan_url = lan_provider.snapshot().get("url")
    if lan_url:
        print(f"局域网访问地址：{lan_url}")
    print("正在启动 Cloudflare Tunnel 临时地址…")


if __name__ == "__main__":
    print_startup_banner()
    tunnel_provider.start()
    threading.Thread(target=announce_pair_url_once, daemon=True).start()
    app.run(host=SERVICE_BIND_HOST, port=PORT, debug=False, threaded=True)

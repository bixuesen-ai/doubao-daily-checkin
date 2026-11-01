"""配置加载。

所有配置来自环境变量（GitHub Actions 里放 Secrets，本地放 .env）。
这样仓库里永远不会出现任何账号凭据。
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

DEFAULT_SUCCESS_KEYWORDS = ["签到成功", "领取成功", "签到完成", "success"]
DEFAULT_ALREADY_KEYWORDS = ["已签到", "已经签到", "今日已领", "重复签到", "already"]
DEFAULT_AUTH_KEYWORDS = ["未登录", "登录已过期", "not login", "unauthorized", "401", "403"]
DEFAULT_RISK_KEYWORDS = ["风控", "验证", "captcha", "risk", "verify", "频繁"]

DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


class ConfigError(Exception):
    """配置缺失或格式错误。"""


def _load_dotenv() -> None:
    """优先用 python-dotenv，没装则用内置的简易解析，保证零依赖也能跑。"""
    if os.getenv("DOUBAO_NO_DOTENV") == "1":
        return
    env_path = Path(".env")
    if not env_path.exists():
        return
    try:
        from dotenv import load_dotenv  # type: ignore

        load_dotenv(env_path, override=False)
        return
    except ImportError:
        pass

    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip("'\"")
        if key and key not in os.environ:
            os.environ[key] = value


def _get(*names: str, default: str = "") -> str:
    for name in names:
        value = os.getenv(name)
        if value is not None and value.strip() != "":
            return value.strip()
    return default


def _split_list(raw: str) -> list[str]:
    return [item.strip() for item in raw.replace("，", ",").split(",") if item.strip()]


_COOKIE_HEADER_RE = re.compile(
    r"""-H\s+(?:"|')cookie:\s*(?P<value>.*?)(?:"|')(?:\s|$)""",
    re.IGNORECASE | re.DOTALL,
)
_COOKIE_FLAG_RE = re.compile(
    r"""(?:--cookie|-b)\s+(?:"|')(?P<value>.*?)(?:"|')""",
    re.IGNORECASE | re.DOTALL,
)
_HEADER_SEGMENT_RE = re.compile(
    r"""-H\s+("(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*'|[^\s-][^\s]*)""",
    re.IGNORECASE | re.DOTALL,
)


def _normalize_curl(raw: str) -> str:
    """把多行 cURL、bash 反斜杠续行和 Windows cmd 续行折叠成单行。"""
    text = raw.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"\\\s*\n", " ", text)
    text = re.sub(r"\^\s*\n", " ", text)
    text = re.sub(r"\n", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    # Windows cmd 会把命令里的特殊字符写成 ^"、^%、^;、^& 等转义形式。
    text = re.sub(r"\^([\"%&;<>|^!])", r"\1", text)
    if text.startswith("$"):
        text = text[1:].strip()
    return text


def _strip_quoted_value(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        value = value[1:-1]
    # Windows cmd 会把命令字符串里的双引号写成两个双引号。
    return value.replace('""', '"')


def _curl_diagnostics(text: str) -> str:
    """生成不含 Cookie 明文的 cURL 诊断信息，用于判断是否选错请求。"""
    urls = re.findall(r"(?:https?://[^\s'\"^]+)", text)
    host = urlparse(urls[0]).netloc if urls else "未解析到"
    header_count = len(_HEADER_SEGMENT_RE.findall(text))
    has_cookie_flag = bool(re.search(r"(?:--cookie|-b)\s+", text, re.IGNORECASE))
    return f"host={host}，请求头数量={header_count}，--cookie/-b={'是' if has_cookie_flag else '否'}"


def _extract_cookie(raw: str) -> str:
    """支持直接粘贴 DevTools 复制出的 cURL 命令，自动提取 Cookie 请求头。

    普通 Cookie 字符串原样返回；cURL 命令则从 `-H "cookie: ..."`、
    `--cookie ...` 或 `-b ...` 中提取实际 Cookie。
    """
    text = _normalize_curl(raw)
    looks_like_curl = (
        text.lower().startswith("curl ")
        or "curl " in text.lower()
        or "-h " in text.lower()
        or "--cookie " in text.lower()
        or "-b " in text.lower()
    )
    if not looks_like_curl:
        return text

    match = _COOKIE_HEADER_RE.search(text) or _COOKIE_FLAG_RE.search(text)
    if match:
        return _strip_quoted_value(match.group("value"))

    # 兜底：逐条解析 -H 请求头，兼容不同 DevTools 复制出的引号和换行格式。
    for segment in _HEADER_SEGMENT_RE.finditer(text):
        header = _strip_quoted_value(segment.group(1))
        if header.lower().startswith("cookie:"):
            return header.split(":", 1)[1].strip()

    diagnostics = _curl_diagnostics(text)
    raise ConfigError(
        "已识别为 cURL 命令，但没有在其中找到 Cookie 请求头。"
        f"诊断信息（不含 Cookie 明文）：{diagnostics}。"
        "请选择一条已登录状态下发给 www.doubao.com 的 Fetch/XHR 请求；"
        "先在右侧 Request Headers 中看到 cookie: 行，再右键 Copy as cURL (cmd)。"
    )


def _parse_json_env(raw: str, name: str) -> dict:
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ConfigError(f"{name} 不是合法 JSON：{exc}") from exc
    if not isinstance(value, dict):
        raise ConfigError(f"{name} 必须是 JSON 对象，例如 {{\"key\": \"value\"}}")
    return value


@dataclass
class Config:
    # —— 必填 ——
    cookie: str
    url: str

    # —— 请求 ——
    method: str = "POST"
    body: str = ""
    headers: dict = field(default_factory=dict)
    timeout: float = 20.0
    retries: int = 3
    retry_backoff: float = 3.0

    # —— 结果判定 ——
    success_keywords: list = field(default_factory=lambda: list(DEFAULT_SUCCESS_KEYWORDS))
    already_keywords: list = field(default_factory=lambda: list(DEFAULT_ALREADY_KEYWORDS))
    auth_keywords: list = field(default_factory=lambda: list(DEFAULT_AUTH_KEYWORDS))
    risk_keywords: list = field(default_factory=lambda: list(DEFAULT_RISK_KEYWORDS))
    success_json_path: list = field(default_factory=list)
    already_json_path: list = field(default_factory=list)

    # —— 通知 ——
    notify_channels: list = field(default_factory=list)
    notify_on_already: bool = False

    @property
    def is_configured(self) -> bool:
        return bool(self.cookie and self.url)


def load_config() -> Config:
    _load_dotenv()

    cookie = _extract_cookie(_get("DOUBAO_COOKIE", "COOKIE"))
    url = _get("DOUBAO_CHECKIN_URL", "CHECKIN_URL")

    if not cookie:
        raise ConfigError(
            "缺少 DOUBAO_COOKIE：请把浏览器中豆包的 Cookie，或 DevTools 复制出的 cURL 命令填进环境变量或 .env。"
        )
    if not url:
        raise ConfigError(
            "缺少 DOUBAO_CHECKIN_URL：请把抓包拿到的签到接口地址填进环境变量或 .env。\n"
            "不知道接口地址？先看 docs/抓包指南.md。"
        )
    if not url.startswith(("http://", "https://")):
        raise ConfigError(f"DOUBAO_CHECKIN_URL 必须是完整的 http(s) 地址，当前为：{url}")

    method = _get("DOUBAO_CHECKIN_METHOD", default="POST").upper()
    if method not in {"GET", "POST", "PUT"}:
        raise ConfigError("DOUBAO_CHECKIN_METHOD 只支持 GET / POST / PUT")

    headers = {
        "User-Agent": _get("DOUBAO_USER_AGENT", default=DEFAULT_UA),
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Content-Type": "application/json",
        "Cookie": cookie,
    }
    headers.update(_parse_json_env(_get("DOUBAO_EXTRA_HEADERS"), "DOUBAO_EXTRA_HEADERS"))

    body = _get("DOUBAO_CHECKIN_BODY")
    if body:
        try:
            json.loads(body)
        except json.JSONDecodeError as exc:
            raise ConfigError(f"DOUBAO_CHECKIN_BODY 不是合法 JSON：{exc}") from exc

    retries_raw = _get("DOUBAO_RETRY", default="3")
    try:
        retries = max(1, int(retries_raw))
    except ValueError as exc:
        raise ConfigError("DOUBAO_RETRY 必须是整数") from exc

    return Config(
        cookie=cookie,
        url=url,
        method=method,
        body=body,
        headers=headers,
        timeout=float(_get("DOUBAO_TIMEOUT", default="20")),
        retries=retries,
        retry_backoff=float(_get("DOUBAO_RETRY_BACKOFF", default="3")),
        success_keywords=_split_list(_get("DOUBAO_SUCCESS_KEYWORDS")) or list(DEFAULT_SUCCESS_KEYWORDS),
        already_keywords=_split_list(_get("DOUBAO_ALREADY_KEYWORDS")) or list(DEFAULT_ALREADY_KEYWORDS),
        auth_keywords=_split_list(_get("DOUBAO_AUTH_KEYWORDS")) or list(DEFAULT_AUTH_KEYWORDS),
        risk_keywords=_split_list(_get("DOUBAO_RISK_KEYWORDS")) or list(DEFAULT_RISK_KEYWORDS),
        success_json_path=_split_list(_get("DOUBAO_SUCCESS_JSON_PATH")),
        already_json_path=_split_list(_get("DOUBAO_ALREADY_JSON_PATH")),
        notify_channels=_split_list(_get("DOUBAO_NOTIFY")),
        notify_on_already=_get("DOUBAO_NOTIFY_ON_ALREADY", default="0") in {"1", "true", "yes"},
    )

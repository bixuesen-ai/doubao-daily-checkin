"""配置加载。

所有配置来自环境变量（GitHub Actions 里放 Secrets，本地放 .env）。
这样仓库里永远不会出现任何账号凭据。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

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

    cookie = _get("DOUBAO_COOKIE", "COOKIE")
    url = _get("DOUBAO_CHECKIN_URL", "CHECKIN_URL")

    if not cookie:
        raise ConfigError(
            "缺少 DOUBAO_COOKIE：请把浏览器里豆包的 Cookie 填进环境变量或 .env。"
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

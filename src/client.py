"""签到请求的核心实现。

设计原则：
1. 接口地址、请求体全部可配置 —— 官方随时会改，改配置比改代码快。
2. 幂等优先 —— 「已签到」视为成功，不会因为 Actions 触发了两次就报错。
3. 失败必留证 —— 打印响应片段（脱敏），方便回来改关键词。
"""

from __future__ import annotations

import json
import random
import re
import time
from dataclasses import dataclass, field
from typing import Any

from . import http, logger
from .config import Config

# 业务状态
SUCCESS = "SUCCESS"
ALREADY = "ALREADY"
AUTH_FAILED = "AUTH_FAILED"
RISK = "RISK"
FAILED = "FAILED"


@dataclass
class CheckinResult:
    status: str
    message: str
    http_status: int = 0
    amount: str = ""
    body_snippet: str = ""
    elapsed: float = 0.0
    attempts: int = 1
    raw: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        """SUCCESS 和 ALREADY 都算这一天过完了。"""
        return self.status in (SUCCESS, ALREADY)

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "ok": self.ok,
            "message": self.message,
            "http_status": self.http_status,
            "amount": self.amount,
            "elapsed": round(self.elapsed, 2),
            "attempts": self.attempts,
            "body_snippet": self.body_snippet,
        }


_UNICODE_ESCAPE = re.compile(r"\\u([0-9a-fA-F]{4})")


def _normalize(text: str) -> str:
    """把 JSON 里转义的中文还原成真字符。

    很多接口返回的是 "\\u7b7e\\u5230\\u6210\\u529f" 而不是「签到成功」，
    不还原的话关键词匹配、额度提取会全部失效。
    """
    if "\\u" not in text:
        return text
    return _UNICODE_ESCAPE.sub(lambda m: chr(int(m.group(1), 16)), text)


def _dig(data: Any, path: str) -> Any:
    """极简 JSON 取值：a.b.c / a.0.b"""
    current = data
    for part in path.split("."):
        if isinstance(current, dict):
            if part not in current:
                return None
            current = current[part]
        elif isinstance(current, list):
            try:
                current = current[int(part)]
            except (ValueError, IndexError):
                return None
        else:
            return None
    return current


def _check_json_path(body: Any, rules: list[str]) -> bool | None:
    """按 "path=value" 规则校验，返回 None 表示规则不适用/无法解析。"""
    if not rules:
        return None
    for rule in rules:
        if "=" not in rule:
            continue
        path, _, expected = rule.partition("=")
        actual = _dig(body, path.strip())
        if actual is None:
            continue
        if str(actual).strip().lower() == expected.strip().lower():
            return True
    return False


def _extract_amount(text: str) -> str:
    """从响应里尽量抠出「+200 额度」这类数字，纯锦上添花。"""
    patterns = [
        r"[+＋]\s*(\d+)\s*(?:活动)?额度",
        r"(?:获得|领取|赠送)[^\d]{0,6}(\d+)\s*(?:活动)?额度",
        r"\"(?:amount|credit|quota|reward|reward_amount)\"\s*:\s*(\d+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return match.group(1)
    return ""


class CheckinClient:
    def __init__(self, config: Config) -> None:
        self.config = config

    def _classify(self, http_status: int, text: str) -> tuple[str, str]:
        lowered = text.lower()

        try:
            data: Any = json.loads(text)
        except (json.JSONDecodeError, TypeError):
            data = None

        # 先判「已签到」：响应里往往同时包含「签到」字样，顺序错了会误判成成功
        for keyword in self.config.already_keywords:
            if keyword.lower() in lowered:
                return ALREADY, f"今日已签到（命中关键词：{keyword}）"

        # 已签到 JSON 规则：优先于成功规则，避免把幂等重放判成新领取
        if self.config.already_json_path:
            verdict = _check_json_path(data, self.config.already_json_path)
            if verdict is True:
                return ALREADY, "今日已签到（JSON 规则命中）"

        # 再用 JSON 规则判定（比关键词准，优先于成功关键词）
        verdict = None
        if self.config.success_json_path:
            verdict = _check_json_path(data, self.config.success_json_path)
        if verdict is True:
            return SUCCESS, "签到成功（JSON 规则命中）"

        for keyword in self.config.success_keywords:
            if keyword.lower() in lowered:
                return SUCCESS, f"签到成功（命中关键词：{keyword}）"

        for keyword in self.config.risk_keywords:
            if keyword.lower() in lowered:
                return RISK, f"疑似触发风控/人机验证（命中关键词：{keyword}）"

        for keyword in self.config.auth_keywords:
            if keyword.lower() in lowered:
                return AUTH_FAILED, f"Cookie 可能已失效（命中关键词：{keyword}）"

        # 部分接口 HTTP 200 但业务 code 非 0；不能落到 2xx 兜底误判成功。
        if isinstance(data, dict) and "code" in data:
            code = data.get("code")
            if str(code).strip() != "0":
                biz_message = data.get("msg") or data.get("message") or ""
                return FAILED, f"业务请求失败 code={code}，{biz_message}".strip("，")

        if 200 <= http_status < 300:
            return SUCCESS, "签到请求返回 2xx（未配置关键词，按成功处理）"

        return FAILED, f"签到失败，HTTP {http_status}"

    def _should_retry(self, status: str, http_status: int) -> bool:
        if status in (SUCCESS, ALREADY, AUTH_FAILED):
            return False
        if status == RISK:
            return http_status == 429
        if status == FAILED:
            # 只重试网络层异常（状态码 0）和 5xx；HTTP 200 包裹的业务错误不重复请求。
            return http_status == 0 or 500 <= http_status < 600
        return False

    def run(self) -> CheckinResult:
        last: CheckinResult | None = None
        started = time.time()

        for attempt in range(1, self.config.retries + 1):
            logger.info(f"发起签到请求（第 {attempt}/{self.config.retries} 次）", url=self.config.url)

            try:
                response = http.request(
                    method=self.config.method,
                    url=self.config.url,
                    headers=self.config.headers,
                    body=self.config.body or None,
                    timeout=self.config.timeout,
                )
            except ConnectionError as exc:
                last = CheckinResult(
                    status=FAILED,
                    message=f"网络请求异常：{exc}",
                    attempts=attempt,
                )
                logger.warn(last.message)
                if attempt < self.config.retries:
                    self._sleep(attempt)
                continue

            text = _normalize(response.text or "")
            snippet = text[:800].replace("\n", " ")
            status, message = self._classify(response.status, text)
            last = CheckinResult(
                status=status,
                message=message,
                http_status=response.status,
                amount=_extract_amount(text),
                body_snippet=snippet,
                attempts=attempt,
            )

            logger.debug(f"HTTP {response.status} → {status}", body=snippet[:200])

            if not self._should_retry(status, response.status):
                break
            if attempt < self.config.retries:
                logger.warn(f"{message}，准备重试")
                self._sleep(attempt)

        assert last is not None
        last.elapsed = time.time() - started
        return last

    def _sleep(self, attempt: int) -> None:
        delay = self.config.retry_backoff * attempt + random.uniform(0, 1.5)
        logger.info(f"等待 {delay:.1f}s 后重试")
        time.sleep(delay)

    def close(self) -> None:  # 兼容旧调用
        return None

"""核心逻辑单元测试：python -m unittest discover -s tests -v"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.client import (  # noqa: E402
    ALREADY,
    AUTH_FAILED,
    FAILED,
    RISK,
    SUCCESS,
    CheckinClient,
    CheckinResult,
    _check_json_path,
    _dig,
    _extract_amount,
)
from src.config import Config, ConfigError, _extract_cookie  # noqa: E402
from src.logger import mask  # noqa: E402
from src.notifier import build_message  # noqa: E402


def make_client(**overrides) -> CheckinClient:
    config = Config(cookie="sessionid=abc; sid=def", url="https://example.com/checkin")
    for key, value in overrides.items():
        setattr(config, key, value)
    return CheckinClient(config)


class TestClassify(unittest.TestCase):
    def test_success_keyword(self):
        status, _ = make_client()._classify(200, '{"message":"签到成功，获得200额度"}')
        self.assertEqual(status, SUCCESS)

    def test_already_wins_over_success(self):
        # 响应里同时出现「签到」和「已签到」，必须判成 ALREADY
        status, _ = make_client()._classify(200, '{"message":"今日已签到，明天再来"}')
        self.assertEqual(status, ALREADY)

    def test_auth_failed(self):
        status, _ = make_client()._classify(200, '{"message":"未登录"}')
        self.assertEqual(status, AUTH_FAILED)

    def test_http_401(self):
        status, _ = make_client()._classify(401, "unauthorized")
        self.assertEqual(status, AUTH_FAILED)

    def test_risk(self):
        status, _ = make_client()._classify(200, '{"message":"请完成验证"}')
        self.assertEqual(status, RISK)

    def test_plain_2xx_is_success(self):
        status, _ = make_client()._classify(204, "")
        self.assertEqual(status, SUCCESS)

    def test_500_is_failed(self):
        status, _ = make_client()._classify(500, "internal error")
        self.assertEqual(status, FAILED)

    def test_custom_json_path(self):
        client = make_client(success_json_path=["data.checkin_status=1"])
        status, _ = client._classify(200, '{"data":{"checkin_status":1}}')
        self.assertEqual(status, SUCCESS)

    def test_real_success_json_response(self):
        body = json.dumps({
            "data": {
                "checked_in": True,
                "checkin_date": "2026-10-31",
                "continuous_days": 2,
                "idempotent": False,
                "reward_amount": 200,
                "reward_code": "quota_grant",
                "reward_status": 3,
                "reward_type": "quota_grant",
            },
            "code": 0,
            "msg": "",
            "message": "",
        }, ensure_ascii=False)
        client = make_client(success_json_path=["data.checked_in=true"])
        status, message = client._classify(200, body)
        self.assertEqual(status, SUCCESS)
        self.assertIn("JSON", message)
        self.assertEqual(_extract_amount(body), "200")

    def test_real_idempotent_response_is_already(self):
        body = json.dumps({
            "data": {"checked_in": True, "idempotent": True, "reward_amount": 200},
            "code": 0,
        })
        client = make_client(
            success_json_path=["data.checked_in=true"],
            already_json_path=["data.idempotent=true"],
        )
        status, _ = client._classify(200, body)
        self.assertEqual(status, ALREADY)

    def test_business_error_code_is_not_success_on_200(self):
        body = '{"data":null,"code":710010702,"msg":"系统错误","message":"system error"}'
        status, message = make_client()._classify(200, body)
        self.assertEqual(status, FAILED)
        self.assertIn("710010702", message)

    def test_json_path_not_match(self):
        client = make_client(success_json_path=["data.checkin_status=1"])
        status, _ = client._classify(200, '{"data":{"checkin_status":0},"msg":"ok"}')
        self.assertEqual(status, SUCCESS)  # 落到 2xx 兜底
        self.assertEqual(client._classify(400, '{"data":{"checkin_status":0}}')[0], FAILED)


class TestRetryPolicy(unittest.TestCase):
    def test_no_retry_on_success_or_already(self):
        client = make_client()
        self.assertFalse(client._should_retry(SUCCESS, 200))
        self.assertFalse(client._should_retry(ALREADY, 200))

    def test_retry_on_5xx_and_network(self):
        client = make_client()
        self.assertTrue(client._should_retry(FAILED, 500))
        self.assertTrue(client._should_retry(FAILED, 0))

    def test_no_retry_on_business_200_error(self):
        client = make_client()
        self.assertFalse(client._should_retry(FAILED, 200))

    def test_no_retry_on_business_4xx(self):
        client = make_client()
        self.assertFalse(client._should_retry(FAILED, 400))
        self.assertFalse(client._should_retry(FAILED, 404))

    def test_retry_on_429(self):
        client = make_client()
        self.assertTrue(client._should_retry(RISK, 429))


class TestHelpers(unittest.TestCase):
    def test_dig(self):
        data = {"a": {"b": [{"c": 7}]}}
        self.assertEqual(_dig(data, "a.b.0.c"), 7)
        self.assertIsNone(_dig(data, "a.x.y"))

    def test_json_path_rules(self):
        self.assertTrue(_check_json_path({"code": 0}, ["code=0"]))
        self.assertFalse(_check_json_path({"code": 1}, ["code=0"]))
        self.assertIsNone(_check_json_path({"code": 1}, []))

    def test_extract_amount(self):
        self.assertEqual(_extract_amount("签到成功，+200 活动额度"), "200")
        self.assertEqual(_extract_amount('{"amount": 300}'), "300")
        self.assertEqual(_extract_amount("no number"), "")

    def test_mask(self):
        masked = mask("sessionid=abcdefghijklmn")
        self.assertNotIn("abcdefghij", masked)
        self.assertIn("len=", masked)


class TestCookieExtraction(unittest.TestCase):
    def test_raw_cookie_is_unchanged(self):
        raw = "sessionid=abc; sid=def; uid=tt=xyz"
        self.assertEqual(_extract_cookie(raw), raw)

    def test_extract_cookie_from_windows_curl_cmd(self):
        curl = (
            'curl "https://www.doubao.com/chat/" '
            '-H "accept: application/json" '
            '-H "cookie: sessionid=abc; sid=def" '
            "--compressed"
        )
        self.assertEqual(_extract_cookie(curl), "sessionid=abc; sid=def")

    def test_extract_cookie_from_bash_curl(self):
        curl = (
            "curl 'https://www.doubao.com/chat/' "
            "-H 'accept: application/json' "
            "-H 'cookie: sessionid=abc; sid=def'"
        )
        self.assertEqual(_extract_cookie(curl), "sessionid=abc; sid=def")

    def test_extract_cookie_from_multiline_bash_curl(self):
        curl = (
            "curl 'https://www.doubao.com/chat/' \\\n"
            "  -H 'accept: application/json' \\\n"
            "  -H 'Cookie: sessionid=abc; sid=def'"
        )
        self.assertEqual(_extract_cookie(curl), "sessionid=abc; sid=def")

    def test_extract_cookie_from_windows_caret_continuation(self):
        curl = (
            'curl "https://www.doubao.com/chat/" ^\n'
            '  -H "accept: application/json" ^\n'
            '  -H "cookie: sessionid=abc; sid=def"'
        )
        self.assertEqual(_extract_cookie(curl), "sessionid=abc; sid=def")

    def test_extract_cookie_with_leading_dollar_prompt(self):
        curl = (
            '$ curl "https://www.doubao.com/chat/" '
            '-H "cookie: sessionid=abc; sid=def"'
        )
        self.assertEqual(_extract_cookie(curl), "sessionid=abc; sid=def")

    def test_extract_cookie_from_cookie_flag(self):
        curl = 'curl "https://www.doubao.com/chat/" --cookie "sessionid=abc; sid=def"'
        self.assertEqual(_extract_cookie(curl), "sessionid=abc; sid=def")

    def test_curl_without_cookie_raises_clear_error(self):
        curl = 'curl "https://www.doubao.com/chat/" -H "accept: application/json"'
        with self.assertRaises(ConfigError):
            _extract_cookie(curl)


class TestNotifyMessage(unittest.TestCase):
    def test_success_title_contains_amount(self):
        result = CheckinResult(status=SUCCESS, message="签到成功", amount="200")
        title, body = build_message(result)
        self.assertIn("成功", title)
        self.assertIn("200", title)
        self.assertIn("SUCCESS", body)

    def test_failure_title(self):
        result = CheckinResult(status=FAILED, message="boom", http_status=500)
        title, _ = build_message(result)
        self.assertIn("失败", title)

    def test_ok_property(self):
        self.assertTrue(CheckinResult(status=ALREADY, message="").ok)
        self.assertFalse(CheckinResult(status=FAILED, message="").ok)


if __name__ == "__main__":
    unittest.main(verbosity=2)

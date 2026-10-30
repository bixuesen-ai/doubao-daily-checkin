"""端到端测试：本地起一个假接口，跑一遍完整签到流程。

这是整个项目最有价值的测试 —— 它证明「配置 → 请求 → 判定 → 退出码」整条链路是通的，
不需要真的豆包账号也能验证。
"""

from __future__ import annotations

import json
import os
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.client import ALREADY, AUTH_FAILED, SUCCESS, CheckinClient  # noqa: E402
from src.config import Config  # noqa: E402


class _Handler(BaseHTTPRequestHandler):
    # 由测试用例注入
    response_body = "{}"
    response_code = 200
    seen: dict = {}

    def _handle(self):
        length = int(self.headers.get("Content-Length") or 0)
        payload = self.rfile.read(length).decode("utf-8") if length else ""
        _Handler.seen = {
            "method": self.command,
            "path": self.path,
            "cookie": self.headers.get("Cookie", ""),
            "body": payload,
            "ua": self.headers.get("User-Agent", ""),
        }
        body = _Handler.response_body.encode("utf-8")
        self.send_response(_Handler.response_code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    do_POST = _handle
    do_GET = _handle

    def log_message(self, *args):  # 静音
        pass


class IntegrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(("127.0.0.1", 0), _Handler)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def _config(self, **overrides) -> Config:
        config = Config(
            cookie="sessionid=fake-cookie-value; sid=1",
            url=f"http://127.0.0.1:{self.port}/api/checkin",
            method="POST",
            body=json.dumps({"activity": "daily"}),
            headers={
                "Cookie": "sessionid=fake-cookie-value; sid=1",
                "User-Agent": "doubao-daily-checkin-test",
                "Content-Type": "application/json",
            },
            retries=1,
        )
        for key, value in overrides.items():
            setattr(config, key, value)
        return config

    def test_success_flow(self):
        _Handler.response_code = 200
        _Handler.response_body = json.dumps({"code": 0, "msg": "签到成功，+200活动额度"})
        result = CheckinClient(self._config()).run()

        self.assertEqual(result.status, SUCCESS)
        self.assertEqual(result.amount, "200")
        self.assertTrue(result.ok)
        # 请求确实带上了 cookie 和 body
        self.assertEqual(_Handler.seen["method"], "POST")
        self.assertIn("sessionid=fake-cookie-value", _Handler.seen["cookie"])
        self.assertEqual(json.loads(_Handler.seen["body"]), {"activity": "daily"})

    def test_already_signed_flow(self):
        _Handler.response_code = 200
        _Handler.response_body = json.dumps({"code": 1001, "msg": "今日已签到"})
        result = CheckinClient(self._config()).run()

        self.assertEqual(result.status, ALREADY)
        self.assertTrue(result.ok)

    def test_auth_expired_flow(self):
        _Handler.response_code = 200
        _Handler.response_body = json.dumps({"code": 401, "msg": "未登录"})
        result = CheckinClient(self._config()).run()

        self.assertEqual(result.status, AUTH_FAILED)
        self.assertFalse(result.ok)

    def test_server_error_retries_then_fails(self):
        _Handler.response_code = 500
        _Handler.response_body = "internal error"
        config = self._config(retries=2, retry_backoff=0.01)
        result = CheckinClient(config).run()

        self.assertEqual(result.status, "FAILED")
        self.assertEqual(result.attempts, 2)  # 重试确实发生了

    def test_get_without_body(self):
        _Handler.response_code = 200
        _Handler.response_body = '{"success":true}'
        config = self._config(method="GET", body="")
        CheckinClient(config).run()
        self.assertEqual(_Handler.seen["method"], "GET")
        self.assertEqual(_Handler.seen["body"], "")


class MainCliTest(unittest.TestCase):
    """用子进程跑 main.py，验证退出码。"""

    def _run(self, env_extra: dict) -> tuple[int, str]:
        import subprocess

        env = {k: v for k, v in os.environ.items() if not k.startswith("DOUBAO_")}
        env["PYTHONIOENCODING"] = "utf-8"
        env["DOUBAO_NO_DOTENV"] = "1"
        env.update(env_extra)
        proc = subprocess.run(
            [sys.executable, str(ROOT / "main.py"), "--check"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            cwd=str(ROOT),
        )
        return proc.returncode, (proc.stdout or "") + (proc.stderr or "")

    def test_missing_config_exits_2(self):
        code, output = self._run({})
        self.assertEqual(code, 2, output)
        self.assertIn("DOUBAO_COOKIE", output)

    def test_valid_config_exits_0(self):
        code, output = self._run(
            {
                "DOUBAO_COOKIE": "sessionid=abc",
                "DOUBAO_CHECKIN_URL": "https://example.com/checkin",
            }
        )
        self.assertEqual(code, 0, output)
        self.assertIn("配置检查通过", output)

    def test_cookie_is_masked_in_output(self):
        code, output = self._run(
            {
                "DOUBAO_COOKIE": "sessionid=SUPERSECRETVALUE",
                "DOUBAO_CHECKIN_URL": "https://example.com/checkin",
            }
        )
        self.assertEqual(code, 0, output)
        self.assertNotIn("SUPERSECRETVALUE", output)


if __name__ == "__main__":
    unittest.main(verbosity=2)

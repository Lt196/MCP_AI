# -*- coding: utf-8 -*-
"""MCP_AI 自动化测试的公共设施：环境隔离、安全导入、假 HTTP 替身、网络封锁。

三个必须守住的约束
------------------
1) 环境隔离：server.py 在导入时会自动加载同目录的 .env，所以每个用例都必须
   显式设定它关心的环境变量，否则测试结果会取决于本机密钥，无法复现。
2) 导入安全：server.py 导入时执行 sys.stdout = io.TextIOWrapper(sys.stdout.buffer, ...)，
   而 pytest 的捕获对象不一定有 .buffer，因此导入期间临时替换成安全流。
3) 禁止真实网络：单元用例默认拦截 socket，避免误打飞书线上接口。

只要用到 server.py 的用例，都应该通过 srv / load_server fixture 拿到模块对象，
不要直接 import server。
"""
from __future__ import annotations

import datetime
import importlib
import io
import socket
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
import requests

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# ---------------------------------------------------------------------------
# 依赖检查：缺 mcp 就直接跳过整套用例，并给出可执行的修复命令
# ---------------------------------------------------------------------------
try:  # pragma: no cover
    from mcp.server.fastmcp import FastMCP  # noqa: F401
except Exception as _exc:  # pragma: no cover
    pytest.skip(
        f"未安装 mcp 包，请先执行 pip install -r requirements.txt（{_exc}）",
        allow_module_level=True,
    )


# ---------------------------------------------------------------------------
# 环境变量：显式给全，压掉本机 .env / 系统环境的影响
# ---------------------------------------------------------------------------
MANAGED_ENV = {
    "FEISHU_APP_ID": "cli_test_app_id",
    "FEISHU_APP_SECRET": "test_app_secret",
    "FEISHU_APP_TOKEN": "bascnTestAppToken",
    "FEISHU_TABLE_ID": "tblTestTable",
    "FIELD_TITLE": "任务内容",
    "FIELD_RAW": "原始语音",
    "FIELD_DUE": "截止日期",
    "FIELD_PRIO": "优先级",
    "FIELD_SOURCE": "来源",
    "FIELD_REQID": "请求ID",
    "DEFAULT_PRIO": "普通",
    "MCP_ENDPOINT": "",
    "MCP_CONFIG": "",
}


def _safe_stream() -> io.TextIOWrapper:
    """一定带 .buffer 的 UTF-8 文本流，专供 server.py 导入期包装使用。"""
    return io.TextIOWrapper(io.BytesIO(), encoding="utf-8")


def import_server(monkeypatch, **env_overrides):
    """在受控环境变量下重新导入 server 模块。

    env_overrides 里的键会被写入环境（不限于 MANAGED_ENV），
    想验证“没配置”的用例可以传 FEISHU_APP_ID=""。
    """
    env = dict(MANAGED_ENV)
    env.update({k: str(v) for k, v in env_overrides.items()})
    for key, value in env.items():
        monkeypatch.setenv(key, value)

    sys.modules.pop("server", None)
    saved_out, saved_err = sys.stdout, sys.stderr
    sys.stdout, sys.stderr = _safe_stream(), _safe_stream()
    try:
        return importlib.import_module("server")
    finally:
        sys.stdout, sys.stderr = saved_out, saved_err


@pytest.fixture
def load_server(monkeypatch):
    """工厂 fixture：load_server(**env) -> 干净的 server 模块。"""

    def _factory(**env_overrides):
        return import_server(monkeypatch, **env_overrides)

    return _factory


@pytest.fixture
def srv(load_server):
    """默认配置下的 server 模块（可自由改属性，monkeypatch 会收拾干净）。"""
    return load_server()


@pytest.fixture
def cached_token(srv):
    """预置一个尚在有效期内（且远离过期窗口）的 tenant_access_token。"""
    srv.TOKEN_CACHE["token"] = "t-cached"
    srv.TOKEN_CACHE["expire"] = time.time() + 7200
    return "t-cached"


# ---------------------------------------------------------------------------
# 假 HTTP：不装任何 mock 框架，直接替换 server 模块里的 requests
# ---------------------------------------------------------------------------
class FakeResponse:
    def __init__(self, payload, status_code=200):
        self.payload = payload
        self.status_code = status_code

    def json(self):
        return self.payload

    def __repr__(self):  # 断言失败时输出好看一点
        return f"FakeResponse({self.payload!r})"


class FakeRequests:
    """requests 模块替身：按顺序消费 handlers，并记录每一次调用。

    handler 形如 ``lambda **kw: FakeResponse(...)``，返回异常实例则抛出该异常。
    """

    RequestException = requests.RequestException

    def __init__(self, handlers=None):
        self.handlers = list(handlers or [])
        self.calls = []

    def post(self, url, headers=None, json=None, timeout=None):
        self.calls.append(
            SimpleNamespace(url=url, headers=dict(headers or {}), json=json, timeout=timeout)
        )
        if not self.handlers:
            raise AssertionError(f"FakeRequests 收到计划外的请求：{url}")
        handler = self.handlers.pop(0)
        result = handler(url=url, headers=headers, json=json, timeout=timeout)
        if isinstance(result, Exception):
            raise result
        return result


def ok_token(token="t-ok", expire=7200):
    return FakeResponse({"code": 0, "tenant_access_token": token, "expire": expire})


def ok_record(record_id="rec_ok_1"):
    return FakeResponse({"code": 0, "data": {"record": {"record_id": record_id}}})


def api_error(code=1254005, msg="FieldNameNotFound"):
    return FakeResponse({"code": code, "msg": msg})


def token_expired(code=99991663):
    return FakeResponse({"code": code, "msg": "tenant_access_token invalid"})


def network_error(message="connection reset by peer"):
    return requests.RequestException(message)


@pytest.fixture
def install_http(monkeypatch):
    """install_http(srv, [handler, ...]) -> FakeRequests 实例。"""

    def _install(module, handlers):
        fake = FakeRequests(handlers)
        monkeypatch.setattr(module, "requests", fake)
        return fake

    return _install


# ---------------------------------------------------------------------------
# 冻结“今天”，让依赖当天日期的时间抽取用例可复现
# ---------------------------------------------------------------------------
@pytest.fixture
def freeze_today(monkeypatch):
    """freeze_today(srv, datetime.date(2026, 3, 4))，只影响被测模块。"""

    def _freeze(module, day: datetime.date):
        class FakeDate(datetime.date):
            @classmethod
            def today(cls):
                return day

        monkeypatch.setattr(
            module, "datetime", SimpleNamespace(date=FakeDate, timedelta=datetime.timedelta)
        )
        return day

    return _freeze


# ---------------------------------------------------------------------------
# 安全网：单元用例里任何真实 TCP 连接都直接失败
# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def block_real_network(request, monkeypatch):
    """拦截真实网络连接。

    integration 用例需要拉起子进程（可能用到 socket），因此跳过封锁；
    这些用例自身通过“不配 FEISHU_APP_ID”来保证不会打真实接口。
    """
    if request.node.get_closest_marker("integration"):
        yield
        return

    def _blocked(*args, **kwargs):
        raise AssertionError(
            "单元用例禁止真实网络访问：请用 install_http 注入假 requests "
            "（如果确实需要联网，把用例标成 @pytest.mark.integration）"
        )

    monkeypatch.setattr(socket.socket, "connect", _blocked)
    monkeypatch.setattr(socket, "create_connection", _blocked)
    yield

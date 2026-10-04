# -*- coding: utf-8 -*-
"""L3 集成测试：把 server.py 当真正的 MCP stdio 服务拉起来，走一遍协议。

这里刻意不配置 FEISHU_APP_ID，让 add_todo 走“配置缺失”分支立即返回，
从而既能验证协议链路（握手 / tools/list / tools/call），又不会碰真实飞书接口。
需要 mcp 客户端 SDK（随 requirements.txt 一起装）。
"""
from __future__ import annotations

import asyncio
import os
import sys

import pytest

from conftest import PROJECT_ROOT

try:  # pragma: no cover
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
except Exception as _exc:  # pragma: no cover
    pytest.skip(f"未安装 / 版本不兼容 mcp 客户端 SDK：{_exc}", allow_module_level=True)

SERVER_PY = PROJECT_ROOT / "server.py"
CALL_TIMEOUT_SECONDS = 120

# 这些变量一律从子进程环境里摘掉，避免本机 .env 或系统环境把它们带进来
STRIPPED_ENV_KEYS = (
    "FEISHU_APP_ID",
    "FEISHU_APP_SECRET",
    "FEISHU_APP_TOKEN",
    "FEISHU_TABLE_ID",
    "DEFAULT_PRIO",
)


def _child_env():
    env = {key: value for key, value in os.environ.items() if key not in STRIPPED_ENV_KEYS}
    env["FIELD_RAW"] = "原始语音"
    env["FEISHU_APP_ID"] = ""  # 空 = 未配置：add_todo 直接返回错误，不发起请求
    return env


async def _with_session(handler):
    params = StdioServerParameters(
        command=sys.executable,
        args=[str(SERVER_PY)],
        env=_child_env(),
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            return await handler(session)


def _text_of(result) -> str:
    return "".join(getattr(block, "text", "") for block in result.content)


async def _list_tools_and_call(session):
    tools = await session.list_tools()
    result = await session.call_tool("add_todo", {"content": "提醒我明天买牛奶"})
    return tools, result


async def _call_twice(session):
    first = await session.call_tool("add_todo", {"content": "第一条待办"})
    second = await session.call_tool("add_todo", {"content": "第二条待办"})
    return first, second


@pytest.mark.integration
def test_stdio_server_exposes_add_todo_and_reports_missing_config():
    tools, result = asyncio.run(
        asyncio.wait_for(_with_session(_list_tools_and_call), timeout=CALL_TIMEOUT_SECONDS)
    )

    names = [tool.name for tool in tools.tools]
    assert "add_todo" in names

    tool = next(tool for tool in tools.tools if tool.name == "add_todo")
    assert "飞书" in (tool.description or "")

    assert not result.isError, f"工具调用返回了错误：{_text_of(result)}"
    assert "未配置" in _text_of(result)


@pytest.mark.integration
def test_stdio_server_survives_sequential_calls():
    first, second = asyncio.run(
        asyncio.wait_for(_with_session(_call_twice), timeout=CALL_TIMEOUT_SECONDS)
    )

    assert not first.isError, f"第一次调用出错：{_text_of(first)}"
    assert not second.isError, f"第二次调用出错：{_text_of(second)}"
    assert "未配置" in _text_of(first)
    assert "未配置" in _text_of(second)

# -*- coding: utf-8 -*-
"""L2 组件测试：MCP 工具 add_todo 的注册、字段组装、调试模式与失败传播。"""
from __future__ import annotations

import asyncio
import datetime

import pytest

from conftest import api_error, ok_record, ok_token


def _registered_tools(srv):
    """取已注册工具。

    直接走 FastMCP 内部的 tool manager：本机 mcp 版本里它是同步方法，而公开的
    async list_tools() 需要一个事件循环，在纯同步用例里会留下未 await 的协程和
    未关闭的 ProactorEventLoop（表现为 RuntimeWarning）。
    """
    manager = getattr(srv.mcp, "_tool_manager", None)
    assert manager is not None, "FastMCP 没有 _tool_manager，需要换一种取工具列表的方式"
    tools = manager.list_tools()
    if asyncio.iscoroutine(tools):  # 少数版本这里是 async
        tools = asyncio.run(tools)
    return tools


def _tool_parts(tool):
    """返回 (名称, 描述, 入参 schema)，抹平不同版本的字段命名。"""
    name = tool.name
    description = getattr(tool, "description", None) or ""
    schema = (
        getattr(tool, "inputSchema", None)
        or getattr(tool, "input_schema", None)
        or getattr(tool, "parameters", None)
    )
    return name, description, schema


# ---------------------------------------------------------------------------
# 工具注册与协议契约
# ---------------------------------------------------------------------------
def test_tool_registered_with_expected_schema(srv):
    tools = _registered_tools(srv)

    names = [tool.name for tool in tools]
    assert names == ["add_todo"], f"小智侧当前只应看到 add_todo，实际：{names}"

    name, description, schema = _tool_parts(tools[0])
    assert name == "add_todo"
    assert "飞书" in description, "工具描述是模型判断何时调用的唯一依据"
    assert schema, "工具没有暴露入参 schema"
    assert set(schema["properties"]) == {"content", "due_date", "priority"}
    assert "content" in (schema.get("required") or [])
    assert "due_date" not in (schema.get("required") or [])
    assert "priority" not in (schema.get("required") or [])


# ---------------------------------------------------------------------------
# 正常写入路径
# ---------------------------------------------------------------------------
def test_add_todo_persists_raw_content_only(srv, install_http, cached_token):
    """当前实现只写 FIELD_RAW，其余字段的赋值都被注释掉了（文档 BUG-03）。

    用 cached_token 预置有效 token，这样假 requests 只需要应付“写记录”这一个请求。
    """
    fake = install_http(srv, [lambda **kw: ok_record("rec_a")])

    result = srv.add_todo("提醒我明天上午 10 点买洗发水")

    assert result["success"] is True
    assert result["record_id"] == "rec_a"
    assert set(fake.calls[0].json["fields"]) == {"原始语音"}
    assert fake.calls[0].json["fields"]["原始语音"] == "提醒我明天上午 10 点买洗发水"


def test_add_todo_returns_request_id(srv, install_http, cached_token):
    install_http(srv, [lambda **kw: ok_record("rec_b")])

    result = srv.add_todo("买牛奶")

    assert result["request_id"].startswith("xz-")
    assert result["request_id"][3:].isdigit(), "请求ID 应为 xz-<毫秒时间戳>"


def test_add_todo_truncates_content_to_500(srv, install_http, cached_token):
    fake = install_http(srv, [lambda **kw: ok_record("rec_d")])

    srv.add_todo("买" * 600)

    assert len(fake.calls[0].json["fields"]["原始语音"]) == 500


def test_add_todo_strips_content_whitespace(srv, install_http, cached_token):
    fake = install_http(srv, [lambda **kw: ok_record("rec_e")])

    srv.add_todo("  买牛奶  ")

    assert fake.calls[0].json["fields"]["原始语音"] == "买牛奶"


def test_add_todo_explicit_due_date_and_priority_are_ignored(srv, install_http, cached_token):
    """传了 due_date/priority 也不会落库——锁定现状，供 BUG-03 修复后回改。"""
    fake = install_http(srv, [lambda **kw: ok_record("rec_c")])

    srv.add_todo("买牛奶", due_date="2026-05-01", priority="紧急")

    assert fake.calls[0].json["fields"] == {"原始语音": "买牛奶"}


@pytest.mark.xfail(
    reason="BUG-03：期望把兜底抽取出的截止日期/优先级一并写库，当前实现未落库", strict=False
)
def test_add_todo_should_persist_due_and_priority(srv, install_http, cached_token, freeze_today):
    freeze_today(srv, datetime.date(2026, 3, 4))
    fake = install_http(srv, [lambda **kw: ok_record("rec_f")])

    srv.add_todo("明天下午提醒我买牛奶")

    fields = fake.calls[0].json["fields"]
    assert fields["截止日期"] == "2026-03-05"
    assert fields["优先级"] == "普通"


# ---------------------------------------------------------------------------
# 配置缺失与失败传播
# ---------------------------------------------------------------------------
def test_add_todo_without_app_id_returns_error(load_server):
    """未配 FEISHU_APP_ID 时直接返回错误提示，且完全不发起请求。"""
    srv = load_server(FEISHU_APP_ID="")

    result = srv.add_todo("买牛奶")

    assert result["success"] is False
    assert "未配置" in result["error"]


def test_add_todo_only_validates_app_id(load_server, install_http):
    """配置校验只看 FEISHU_APP_ID：APP_TOKEN / TABLE_ID 为空也会打到接口（文档 BUG-07）。"""
    srv = load_server(FEISHU_APP_TOKEN="", FEISHU_TABLE_ID="")
    fake = install_http(
        srv,
        [
            lambda **kw: ok_token("t-1"),
            lambda **kw: api_error(1254004, "app not found"),
        ],
    )

    result = srv.add_todo("买牛奶")

    assert result["success"] is False
    assert fake.calls[1].url.endswith("/apps//tables//records")


def test_add_todo_propagates_record_failure(srv, install_http, cached_token):
    install_http(srv, [lambda **kw: api_error(1254005, "FieldNameNotFound")])

    result = srv.add_todo("买牛奶")

    assert result["success"] is False
    assert "1254005" in result["error"]
    assert "request_id" in result, "失败时也要能拿到请求ID，便于对账排查"


# ---------------------------------------------------------------------------
# 调试模式
# ---------------------------------------------------------------------------
def test_add_todo_debug_mode_returns_fields_without_writing(srv, install_http, monkeypatch):
    monkeypatch.setattr(srv, "DEBUG_MODE", True)
    fake = install_http(srv, [])  # 任何请求都会让用例失败

    result = srv.add_todo("提醒我明天买牛奶")

    assert result["success"] is True
    assert result["debug"] is True
    assert result["fields"] == {"原始语音": "提醒我明天买牛奶"}
    assert fake.calls == [], "调试模式绝不能写线上表格"


def test_add_todo_debug_mode_logs_to_stderr(srv, install_http, monkeypatch, capsys):
    monkeypatch.setattr(srv, "DEBUG_MODE", True)
    install_http(srv, [])

    srv.add_todo("提醒我明天买牛奶")

    captured = capsys.readouterr()
    assert "[调试]" in captured.err
    assert "识别 due" in captured.err
    assert "识别 prio" in captured.err
    assert captured.out == "", "stdio 传输下 stdout 被 MCP 协议占用，调试信息不能污染它"

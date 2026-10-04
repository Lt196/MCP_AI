# -*- coding: utf-8 -*-
"""L1/L2 测试：mcp_pipe 的配置读取、子进程命令组装，以及进程级行为。

标了 @pytest.mark.integration 的用例会真的起 mcp_pipe.py 子进程，
但都走“立刻退出”的分支，不连接任何 WebSocket 服务。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import PROJECT_ROOT

pytest.importorskip("websockets", reason="mcp_pipe 依赖 websockets")
pytest.importorskip("dotenv", reason="mcp_pipe 依赖 python-dotenv")

PIPE_PY = PROJECT_ROOT / "mcp_pipe.py"


@pytest.fixture(scope="module")
def pipe():
    """导入 mcp_pipe，并回滚它导入时 load_dotenv() 对环境变量的污染。"""
    snapshot = dict(os.environ)
    try:
        import mcp_pipe

        yield mcp_pipe
    finally:
        os.environ.clear()
        os.environ.update(snapshot)


def _write_config(tmp_path: Path, data) -> Path:
    path = tmp_path / "mcp_config.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path


def _stdio_entry(**overrides):
    entry = {"command": sys.executable, "args": ["server.py"], "type": "stdio"}
    entry.update(overrides)
    return entry


# ---------------------------------------------------------------------------
# load_config
# ---------------------------------------------------------------------------
def test_load_config_returns_empty_when_missing(pipe, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("MCP_CONFIG", raising=False)

    assert pipe.load_config() == {}


def test_load_config_reads_env_path(pipe, monkeypatch, tmp_path):
    cfg = _write_config(tmp_path, {"mcpServers": {"a": {"command": "python"}}})
    monkeypatch.setenv("MCP_CONFIG", str(cfg))

    assert pipe.load_config() == {"mcpServers": {"a": {"command": "python"}}}


def test_load_config_falls_back_to_cwd(pipe, monkeypatch, tmp_path):
    monkeypatch.delenv("MCP_CONFIG", raising=False)
    _write_config(tmp_path, {"mcpServers": {}})
    monkeypatch.chdir(tmp_path)

    assert pipe.load_config() == {"mcpServers": {}}


def test_load_config_returns_empty_on_broken_json(pipe, monkeypatch, tmp_path):
    broken = tmp_path / "mcp_config.json"
    broken.write_text("{ 这不是合法 JSON", encoding="utf-8")
    monkeypatch.setenv("MCP_CONFIG", str(broken))

    assert pipe.load_config() == {}, "配置损坏时应降级为空配置，而不是崩溃"


# ---------------------------------------------------------------------------
# build_server_command
# ---------------------------------------------------------------------------
def test_build_command_for_config_stdio_server(pipe, monkeypatch, tmp_path):
    cfg = _write_config(
        tmp_path,
        {"mcpServers": {"feishu": _stdio_entry(env={"FEISHU_APP_ID": "cli_x"})}},
    )
    monkeypatch.setenv("MCP_CONFIG", str(cfg))
    monkeypatch.setenv("MCP_PIPE_TEST_FLAG", "1")

    cmd, env = pipe.build_server_command("feishu")

    assert cmd == [sys.executable, "server.py"]
    assert env["FEISHU_APP_ID"] == "cli_x", "配置里的 env 必须注入子进程"
    assert env["MCP_PIPE_TEST_FLAG"] == "1", "父进程环境必须被继承"


def test_build_command_defaults_to_stdio_without_type(pipe, monkeypatch, tmp_path):
    cfg = _write_config(tmp_path, {"mcpServers": {"x": {"command": "python", "args": []}}})
    monkeypatch.setenv("MCP_CONFIG", str(cfg))

    cmd, _ = pipe.build_server_command("x")

    assert cmd == ["python"]


def test_build_command_script_path_backcompat(pipe, monkeypatch, tmp_path):
    script = tmp_path / "server.py"
    script.write_text("print('hi')", encoding="utf-8")
    monkeypatch.setenv("MCP_CONFIG", str(tmp_path / "does-not-exist.json"))

    cmd, env = pipe.build_server_command(str(script))

    assert cmd == [sys.executable, str(script)]
    assert isinstance(env, dict)


def test_build_command_rejects_unknown_target(pipe, monkeypatch, tmp_path):
    monkeypatch.setenv("MCP_CONFIG", str(tmp_path / "does-not-exist.json"))

    with pytest.raises(RuntimeError) as excinfo:
        pipe.build_server_command("not-a-server")

    assert "neither a configured server nor an existing script" in str(excinfo.value)


def test_build_command_rejects_disabled_server(pipe, monkeypatch, tmp_path):
    cfg = _write_config(tmp_path, {"mcpServers": {"x": _stdio_entry(disabled=True)}})
    monkeypatch.setenv("MCP_CONFIG", str(cfg))

    with pytest.raises(RuntimeError) as excinfo:
        pipe.build_server_command("x")

    assert "disabled" in str(excinfo.value)


def test_build_command_requires_command_for_stdio(pipe, monkeypatch, tmp_path):
    cfg = _write_config(tmp_path, {"mcpServers": {"x": {"type": "stdio", "args": []}}})
    monkeypatch.setenv("MCP_CONFIG", str(cfg))

    with pytest.raises(RuntimeError) as excinfo:
        pipe.build_server_command("x")

    assert "missing 'command'" in str(excinfo.value)


def test_build_command_rejects_unknown_type(pipe, monkeypatch, tmp_path):
    cfg = _write_config(tmp_path, {"mcpServers": {"x": {"type": "carrier-pigeon"}}})
    monkeypatch.setenv("MCP_CONFIG", str(cfg))

    with pytest.raises(RuntimeError) as excinfo:
        pipe.build_server_command("x")

    assert "Unsupported server type" in str(excinfo.value)


@pytest.mark.parametrize("typ", ["sse", "http", "streamablehttp"])
def test_build_command_requires_url_for_remote_transport(pipe, monkeypatch, tmp_path, typ):
    cfg = _write_config(tmp_path, {"mcpServers": {"remote": {"type": typ}}})
    monkeypatch.setenv("MCP_CONFIG", str(cfg))

    with pytest.raises(RuntimeError) as excinfo:
        pipe.build_server_command("remote")

    assert "missing 'url'" in str(excinfo.value)


@pytest.mark.parametrize(
    "typ, expected_transport",
    [("sse", None), ("http", "streamablehttp"), ("streamablehttp", "streamablehttp")],
)
def test_build_command_proxy_for_remote_types(pipe, monkeypatch, tmp_path, typ, expected_transport):
    entry = {
        "type": typ,
        "url": "https://example.com/mcp",
        "headers": {"Authorization": "Bearer tok"},
    }
    cfg = _write_config(tmp_path, {"mcpServers": {"remote": entry}})
    monkeypatch.setenv("MCP_CONFIG", str(cfg))

    cmd, _ = pipe.build_server_command("remote")

    assert cmd[:3] == [sys.executable, "-m", "mcp_proxy"]
    assert cmd[-1] == "https://example.com/mcp"
    assert "-H" in cmd and "Authorization" in cmd and "Bearer tok" in cmd
    if expected_transport:
        assert cmd[3:5] == ["--transport", expected_transport]
    else:
        assert "--transport" not in cmd, "sse 走默认传输，不应带 --transport"


def test_build_command_transporttype_alias(pipe, monkeypatch, tmp_path):
    entry = {"transportType": "http", "url": "https://example.com/mcp"}
    cfg = _write_config(tmp_path, {"mcpServers": {"remote": entry}})
    monkeypatch.setenv("MCP_CONFIG", str(cfg))

    cmd, _ = pipe.build_server_command("remote")

    assert "--transport" in cmd


def test_build_command_uses_sys_argv_when_target_missing(pipe, monkeypatch, tmp_path):
    cfg = _write_config(tmp_path, {"mcpServers": {"feishu": _stdio_entry()}})
    monkeypatch.setenv("MCP_CONFIG", str(cfg))
    monkeypatch.setattr(sys, "argv", ["mcp_pipe.py", "feishu"])

    cmd, _ = pipe.build_server_command()

    assert cmd == [sys.executable, "server.py"]


def test_build_command_without_target_or_argv_raises(pipe, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["mcp_pipe.py"])

    with pytest.raises(AssertionError):
        pipe.build_server_command()


# ---------------------------------------------------------------------------
# 进程级行为（integration）
# ---------------------------------------------------------------------------
def _run_pipe(args, env_overrides, cwd, timeout=60):
    env = dict(os.environ)
    env.update({key: str(value) for key, value in env_overrides.items()})
    return subprocess.run(
        [sys.executable, str(PIPE_PY), *args],
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )


@pytest.mark.integration
def test_pipe_exits_when_endpoint_missing(tmp_path):
    result = _run_pipe([], {"MCP_ENDPOINT": "", "MCP_CONFIG": ""}, tmp_path)

    assert result.returncode == 1
    assert "MCP_ENDPOINT" in result.stderr


@pytest.mark.integration
def test_pipe_rejects_configured_server_name(tmp_path):
    """BUG-02：命令行支持“配置里的服务名”由 build_server_command 保证，
    但 __main__ 只接受存在的脚本路径，于是配置里的服务名根本跑不起来。"""
    cfg = _write_config(tmp_path, {"mcpServers": {"feishu-todo": _stdio_entry()}})

    result = _run_pipe(
        ["feishu-todo"],
        {"MCP_ENDPOINT": "ws://127.0.0.1:9/mcp", "MCP_CONFIG": str(cfg)},
        tmp_path,
    )

    assert result.returncode == 1
    assert "must be a local Python script path" in result.stderr


@pytest.mark.integration
def test_pipe_reports_empty_config(tmp_path):
    cfg = _write_config(tmp_path, {"mcpServers": {}})

    result = _run_pipe(
        [],
        {"MCP_ENDPOINT": "ws://127.0.0.1:9/mcp", "MCP_CONFIG": str(cfg)},
        tmp_path,
    )

    assert "No enabled mcpServers found in config" in result.stderr
    assert result.returncode == 0, "异常被吞掉后以 0 退出，上层无法感知失败（文档 BUG-08）"


@pytest.mark.integration
def test_pipe_skips_disabled_servers(tmp_path):
    cfg = _write_config(
        tmp_path,
        {"mcpServers": {"a": _stdio_entry(disabled=True), "b": _stdio_entry(disabled=True)}},
    )

    result = _run_pipe(
        [],
        {"MCP_ENDPOINT": "ws://127.0.0.1:9/mcp", "MCP_CONFIG": str(cfg)},
        tmp_path,
    )

    assert "Skipping disabled servers: a, b" in result.stderr

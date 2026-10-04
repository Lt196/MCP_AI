# -*- coding: utf-8 -*-
"""L2 组件测试：tenant_access_token 缓存/刷新，以及飞书记录写入的重试与错误处理。

全部通过 install_http 注入假 requests，不产生任何真实网络请求。
"""
from __future__ import annotations

import time

import pytest

from conftest import (
    FakeResponse,
    api_error,
    network_error,
    ok_record,
    ok_token,
    token_expired,
)

TOKEN_URL_SUFFIX = "/open-apis/auth/v3/tenant_access_token/internal"


# ---------------------------------------------------------------------------
# get_tenant_token
# ---------------------------------------------------------------------------
def test_get_token_success(srv, install_http):
    fake = install_http(srv, [lambda **kw: ok_token("t-abc")])

    assert srv.get_tenant_token() == "t-abc"
    assert len(fake.calls) == 1
    call = fake.calls[0]
    assert call.url.endswith(TOKEN_URL_SUFFIX)
    assert call.json == {"app_id": "cli_test_app_id", "app_secret": "test_app_secret"}
    assert call.timeout == 10
    assert srv.TOKEN_CACHE["token"] == "t-abc"


def test_get_token_is_cached(srv, install_http):
    fake = install_http(srv, [lambda **kw: ok_token("t-abc")])

    assert srv.get_tenant_token() == "t-abc"
    assert srv.get_tenant_token() == "t-abc"
    assert len(fake.calls) == 1, "缓存命中时不应再次请求飞书"


def test_get_token_refreshes_when_near_expiry(srv, install_http):
    """剩余有效期不足 300 秒视为过期，必须重新换取。"""
    srv.TOKEN_CACHE.update(token="t-old", expire=time.time() + 100)
    fake = install_http(srv, [lambda **kw: ok_token("t-new")])

    assert srv.get_tenant_token() == "t-new"
    assert len(fake.calls) == 1


def test_get_token_raises_on_business_error(srv, install_http):
    install_http(srv, [lambda **kw: FakeResponse({"code": 10003, "msg": "invalid app_secret"})])

    with pytest.raises(RuntimeError) as excinfo:
        srv.get_tenant_token()
    assert "换取 tenant_access_token 失败" in str(excinfo.value)
    assert srv.TOKEN_CACHE["token"] is None, "换取失败时不能把脏 token 写进缓存"


# ---------------------------------------------------------------------------
# feishu_create_record：正常路径
# ---------------------------------------------------------------------------
def test_create_record_success(srv, install_http, cached_token):
    fake = install_http(srv, [lambda **kw: ok_record("rec_1")])

    result = srv.feishu_create_record({"原始语音": "买牛奶"})

    assert result == {"success": True, "record_id": "rec_1"}
    call = fake.calls[0]
    assert call.url == (
        "https://open.feishu.cn/open-apis/bitable/v1/apps/"
        "bascnTestAppToken/tables/tblTestTable/records"
    )
    assert call.headers["Authorization"] == f"Bearer {cached_token}"
    assert call.json == {"fields": {"原始语音": "买牛奶"}}
    assert call.timeout == 10


def test_create_record_business_error_does_not_retry(srv, install_http, cached_token):
    """字段名不匹配这类业务错误重试没有意义，必须立刻返回。"""
    fake = install_http(srv, [lambda **kw: api_error(1254005, "FieldNameNotFound")])

    result = srv.feishu_create_record({"原始语音": "买牛奶"})

    assert result["success"] is False
    assert "1254005" in result["error"]
    assert len(fake.calls) == 1


@pytest.mark.parametrize("code", [99991663, 99991664])
def test_create_record_refreshes_token_and_retries(srv, install_http, code):
    srv.TOKEN_CACHE.update(token="t-stale", expire=time.time() + 7200)
    fake = install_http(
        srv,
        [
            lambda **kw: token_expired(code),
            lambda **kw: ok_token("t-fresh"),
            lambda **kw: ok_record("rec_refreshed"),
        ],
    )

    result = srv.feishu_create_record({"原始语音": "买牛奶"})

    assert result == {"success": True, "record_id": "rec_refreshed"}
    assert [call.url.rsplit("/", 1)[-1] for call in fake.calls] == [
        "records",
        "internal",
        "records",
    ]
    assert fake.calls[2].headers["Authorization"] == "Bearer t-fresh"


def test_create_record_retries_on_network_error(srv, install_http, cached_token, monkeypatch):
    sleeps = []
    monkeypatch.setattr(srv.time, "sleep", lambda seconds: sleeps.append(seconds))
    fake = install_http(
        srv,
        [
            lambda **kw: network_error("connection reset by peer"),
            lambda **kw: ok_record("rec_3"),
        ],
    )

    result = srv.feishu_create_record({"原始语音": "买牛奶"})

    assert result == {"success": True, "record_id": "rec_3"}
    assert sleeps == [1], "首次失败后应等待 1 秒再重试"
    assert len(fake.calls) == 2


def test_create_record_gives_up_after_three_attempts(srv, install_http, cached_token, monkeypatch):
    sleeps = []
    monkeypatch.setattr(srv.time, "sleep", lambda seconds: sleeps.append(seconds))
    fake = install_http(srv, [lambda **kw: network_error("boom") for _ in range(3)])

    result = srv.feishu_create_record({"原始语音": "买牛奶"})

    assert result["success"] is False
    assert "boom" in result["error"]
    assert len(fake.calls) == 3
    assert sleeps == [1, 2, 3], "退避间隔为 1+attempt；最后一次失败后仍会多睡一次（文档 BUG-06）"


def test_create_record_error_message_truncated(srv, install_http, cached_token, monkeypatch):
    monkeypatch.setattr(srv.time, "sleep", lambda seconds: None)
    install_http(srv, [lambda **kw: network_error("x" * 500) for _ in range(3)])

    result = srv.feishu_create_record({"原始语音": "买牛奶"})

    assert result["success"] is False
    assert len(result["error"]) == 200


def test_create_record_raises_when_token_refresh_fails(srv, install_http):
    """token 失效后重新换取也失败时，异常会直接抛出，而不是返回 success=False。"""
    srv.TOKEN_CACHE.update(token="t-stale", expire=time.time() + 7200)
    install_http(
        srv,
        [
            lambda **kw: token_expired(),
            lambda **kw: FakeResponse({"code": 10003, "msg": "invalid app_secret"}),
        ],
    )

    with pytest.raises(RuntimeError):
        srv.feishu_create_record({"原始语音": "买牛奶"})

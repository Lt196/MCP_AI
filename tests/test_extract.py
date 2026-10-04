# -*- coding: utf-8 -*-
"""L1 单元测试：服务端的中文时间 / 优先级 / 标题兜底抽取。

这三个函数是 add_todo 在“大模型没传参数”时的兜底逻辑，纯函数、无 IO，
所以用固定“今天”（2026-03-04 周三）覆盖各种语音表述。
"""
from __future__ import annotations

import datetime

import pytest

# 冻结的“今天”：2026-03-04 是周三，weekday() == 2
FROZEN_WEDNESDAY = datetime.date(2026, 3, 4)
NEXT_MONDAY = FROZEN_WEDNESDAY + datetime.timedelta(days=7 - FROZEN_WEDNESDAY.weekday())


# ---------------------------------------------------------------------------
# guess_due_date：相对日期
# ---------------------------------------------------------------------------
def test_due_tomorrow(srv, freeze_today):
    freeze_today(srv, FROZEN_WEDNESDAY)
    assert srv.guess_due_date("提醒我明天上午 10 点买洗发水") == "2026-03-05"


def test_due_day_after_tomorrow(srv, freeze_today):
    freeze_today(srv, FROZEN_WEDNESDAY)
    assert srv.guess_due_date("后天交房租") == "2026-03-06"


def test_due_today(srv, freeze_today):
    freeze_today(srv, FROZEN_WEDNESDAY)
    assert srv.guess_due_date("今天下午三点开会") == "2026-03-04"


def test_due_prefers_tomorrow_over_today(srv, freeze_today):
    """现实现是 if/elif 顺序判断，同一句里“明天”优先于“今天”。"""
    freeze_today(srv, FROZEN_WEDNESDAY)
    assert srv.guess_due_date("今天先把明天的材料准备好") == "2026-03-05"


# ---------------------------------------------------------------------------
# guess_due_date：绝对日期
# ---------------------------------------------------------------------------
def test_due_absolute_with_year(srv, freeze_today):
    freeze_today(srv, FROZEN_WEDNESDAY)
    assert srv.guess_due_date("2026年5月1日交房租") == "2026-05-01"


def test_due_absolute_without_year_uses_current_year(srv, freeze_today):
    freeze_today(srv, FROZEN_WEDNESDAY)
    assert srv.guess_due_date("5月1日交房租") == "2026-05-01"


def test_due_absolute_with_ri_suffix(srv, freeze_today):
    freeze_today(srv, FROZEN_WEDNESDAY)
    assert srv.guess_due_date("3月20号体检") == "2026-03-20"


def test_due_invalid_date_returns_none(srv, freeze_today):
    """2 月 30 日不存在，兜底逻辑必须返回 None 而不是抛异常。"""
    freeze_today(srv, FROZEN_WEDNESDAY)
    assert srv.guess_due_date("2月30日开会") is None


def test_due_no_date_returns_none(srv, freeze_today):
    freeze_today(srv, FROZEN_WEDNESDAY)
    assert srv.guess_due_date("随便记一下买牛奶") is None


# ---------------------------------------------------------------------------
# guess_due_date：下周 X
# ---------------------------------------------------------------------------
CN_NUM = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6, "天": 6}


def _next_week_case(label: str):
    """返回（正确预期日期, 当前实现是否有缺陷）。"""
    target = CN_NUM[label]
    expected = (NEXT_MONDAY + datetime.timedelta(days=target)).isoformat()
    buggy = target > FROZEN_WEDNESDAY.weekday()
    return expected, buggy


NEXT_WEEK_PARAMS = []
for _label in ("一", "二", "三", "四", "五", "六", "日", "天"):
    _expected, _buggy = _next_week_case(_label)
    _marks = ()
    if _buggy:
        _marks = pytest.mark.xfail(
            reason="BUG-01：今天周三时说“下周四~下周日”，当前实现算成本周那一天",
            strict=False,
        )
    NEXT_WEEK_PARAMS.append(pytest.param(_label, _expected, id=f"下周{_label}", marks=_marks))


@pytest.mark.parametrize("label, expected", NEXT_WEEK_PARAMS)
def test_due_next_week_weekday(srv, freeze_today, label, expected):
    freeze_today(srv, FROZEN_WEDNESDAY)
    assert srv.guess_due_date(f"下周{label}交周报") == expected


def test_due_next_week_from_sunday(srv, freeze_today):
    """今天就是周日时，“下周X”在现实现下恰好全对，作为对照锚点。"""
    sunday = datetime.date(2026, 3, 8)
    freeze_today(srv, sunday)
    next_monday = sunday + datetime.timedelta(days=1)
    assert srv.guess_due_date("下周一开会") == next_monday.isoformat()
    assert srv.guess_due_date("下周日开会") == (
        next_monday + datetime.timedelta(days=6)
    ).isoformat()


# ---------------------------------------------------------------------------
# guess_priority
# ---------------------------------------------------------------------------
def test_priority_urgent_keywords(srv):
    for word in ("紧急", "尽快", "马上", "立刻", "urgent"):
        assert srv.guess_priority(f"{word}处理一下") == "紧急"


def test_priority_important_keywords(srv):
    for word in ("重要", "优先", "别忘了", "记得"):
        assert srv.guess_priority(f"{word}买牛奶") == "重要"


def test_priority_default(srv):
    assert srv.guess_priority("写周报") == "普通"


def test_priority_default_follows_env(load_server):
    srv = load_server(DEFAULT_PRIO="低")
    assert srv.guess_priority("写周报") == "低"


def test_priority_urgent_wins_when_both_present(srv):
    assert srv.guess_priority("紧急又重要的事") == "紧急"


def test_priority_keyword_is_case_sensitive(srv):
    """现实现只匹配小写 urgent，全大写不生效（文档 BUG-04 观察项）。"""
    assert srv.guess_priority("URGENT fix") == "普通"


def test_priority_ignores_negation(srv):
    """“不重要”会被判成“重要”，这是纯关键词匹配的已知代价（文档 BUG-04）。"""
    assert srv.guess_priority("这事不重要") == "重要"


# ---------------------------------------------------------------------------
# make_title
# ---------------------------------------------------------------------------
def test_title_strips_reminder_prefix(srv):
    assert srv.make_title("提醒我明天上午 10 点买洗发水") == "明天上午 10 点买洗发水"


def test_title_strips_multiple_prefixes(srv):
    assert srv.make_title("记住帮我记下周交周报") == "下周交周报"


def test_title_takes_first_sentence(srv):
    assert srv.make_title("买牛奶。顺便买鸡蛋") == "买牛奶"


def test_title_falls_back_to_raw_when_prefix_only(srv):
    """整句被前缀规则清空时，回退到原文，不能返回空标题。"""
    assert srv.make_title("记住") == "记住"


def test_title_falls_back_when_first_sentence_empty(srv):
    assert srv.make_title("。记一下") == "。记一下"


def test_title_truncates_to_60_chars(srv):
    assert len(srv.make_title("买" * 100)) == 60


def test_title_keeps_colon_after_removed_prefix(srv):
    """“记住：”只去掉“记住”，全角冒号会残留（文档 BUG-05 观察项）。"""
    assert srv.make_title("记住：明天开会") == "：明天开会"


def test_title_strips_surrounding_whitespace(srv):
    assert srv.make_title("  提醒我  买牛奶  ") == "买牛奶"

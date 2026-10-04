# -*- coding: utf-8 -*-
"""
小智本地 MCP Server：飞书待办入库
运行:  python server.py          (stdio, 供小智 MCP 接入点连接)
依赖:  pip install mcp requests
配置:  见下方环境变量 / .env
"""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8")
import os, re, time, json, logging, datetime
import requests
# ---- 自动加载同目录下的 .env ----
_ENV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
if os.path.exists(_ENV_PATH):
    with open(_ENV_PATH, encoding="utf-8") as _f:
        for _line in _f:
            _line = _line.strip()
            if not _line or _line.startswith("#"):
                continue
            if _line.startswith("export "):
                _line = _line[7:]
            if "=" in _line:
                _k, _v = _line.split("=", 1)
                os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))
from mcp.server.fastmcp import FastMCP

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("xiaozhi_feishu_mcp")

# ---------------- 配置 ----------------
FEISHU_APP_ID      = os.getenv("FEISHU_APP_ID", "")
FEISHU_APP_SECRET  = os.getenv("FEISHU_APP_SECRET", "")
FEISHU_APP_TOKEN   = os.getenv("FEISHU_APP_TOKEN", "")   # 多维表格 app_token/base_id（wiki 链接需先 get_node 换 obj_token）
FEISHU_TABLE_ID    = os.getenv("FEISHU_TABLE_ID", "tblxxxx")

# 字段名映射：默认与新建表格的列名一致，务必改成你表里的真实列名（一字不差）
FIELD_TITLE   = os.getenv("FIELD_TITLE", "任务内容")
FIELD_RAW     = os.getenv("FIELD_RAW", "原始语音")
FIELD_DUE     = os.getenv("FIELD_DUE", "截止日期")     # 日期字段
FIELD_PRIO    = os.getenv("FIELD_PRIO", "优先级")      # 单选字段
FIELD_SOURCE  = os.getenv("FIELD_SOURCE", "来源")
FIELD_REQID   = os.getenv("FIELD_REQID", "请求ID")     # 用于幂等去重，建议加一列文本字段
DEFAULT_PRIO  = os.getenv("DEFAULT_PRIO", "普通")

# ---------------- 调试模式 ----------------
# 调试模式开关：注释掉下一行的赋值即关闭调试模式（默认开启）
DEBUG_MODE = False          # ← 调试模式。把这行注释掉就关闭，恢复正常写入

TOKEN_CACHE = {"token": None, "expire": 0}

# ---------------- 飞书 ----------------
def get_tenant_token() -> str:
    if TOKEN_CACHE["token"] and time.time() < TOKEN_CACHE["expire"] - 300:
        return TOKEN_CACHE["token"]
    r = requests.post(
        "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
        json={"app_id": FEISHU_APP_ID, "app_secret": FEISHU_APP_SECRET}, timeout=10)
    data = r.json()
    if data.get("code") != 0:
        raise RuntimeError(f"换取 tenant_access_token 失败: {data}")
    TOKEN_CACHE["token"] = data["tenant_access_token"]
    TOKEN_CACHE["expire"] = time.time() + int(data.get("expire", 7200))
    return TOKEN_CACHE["token"]

def feishu_create_record(fields: dict) -> dict:
    token = get_tenant_token()
    url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{FEISHU_APP_TOKEN}/tables/{FEISHU_TABLE_ID}/records"
    last_err = None
    for attempt in range(3):
        try:
            r = requests.post(url, headers={"Authorization": f"Bearer {token}"},
                              json={"fields": fields}, timeout=10)
            data = r.json()
            if data.get("code") == 0:
                return {"success": True, "record_id": data["data"]["record"]["record_id"]}
            last_err = data
            if data.get("code") in (99991663, 99991664):   # token 类错误，刷新重试
                TOKEN_CACHE["token"], TOKEN_CACHE["expire"] = None, 0
                token = get_tenant_token()
                continue
            break
        except requests.RequestException as e:
            last_err = str(e)
            time.sleep(1 + attempt)
    return {"success": False, "error": str(last_err)[:200]}

# ---------------- 轻量兜底抽取 ----------------
CN_WEEK = {"周一": 0, "周二": 1, "周三": 2, "周四": 3, "周五": 4, "周六": 5, "周日": 6, "周天": 6}

def guess_due_date(text: str):
    today = datetime.date.today()
    if "明天" in text: return str(today + datetime.timedelta(days=1))
    if "后天" in text: return str(today + datetime.timedelta(days=2))
    if "今天" in text: return str(today)
    m = re.search(r"(下周)([一二三四五六日天])", text)
    if m:
        target = CN_WEEK.get("周" + m.group(2), 0)
        delta = (target - today.weekday()) % 7 or 7
        return str(today + datetime.timedelta(days=7 - today.weekday() + target if target < today.weekday() else delta))
    m = re.search(r"(?:(\d{4})年)?(\d{1,2})月(\d{1,2})[日号]?", text)
    if m:
        y = int(m.group(1) or today.year)
        try: return datetime.date(y, int(m.group(2)), int(m.group(3))).isoformat()
        except ValueError: return None
    return None

def guess_priority(text: str) -> str:
    if any(w in text for w in ("紧急", "尽快", "马上", "立刻", "urgent")): return "紧急"
    if any(w in text for w in ("重要", "优先", "别忘了", "记得")): return "重要"
    return DEFAULT_PRIO

def make_title(text: str) -> str:
    t = re.sub(r"(记住|帮我记|提醒我|待办[:：]?)", "", text).strip()
    t = t.split("。")[0].strip() or text.strip()
    return t[:60]

# ---------------- MCP ----------------
mcp = FastMCP("xiaozhi-feishu-todo")

@mcp.tool()
def add_todo(content: str, due_date: str = "", priority: str = "") -> dict:
    """把用户这段语音/对话中的待办事项写入飞书待办项目库（多维表格）。
    优先使用传入的 due_date/priority；为空时服务端从 content 兜底猜测。
    content 传用户原始表述即可，不要编造。
    """
    due = due_date or guess_due_date(content)
    prio = priority or guess_priority(content)
    req_id = f"xz-{int(time.time()*1000)}"
    fields = {
        #FIELD_TITLE: make_title(content),
        FIELD_RAW: content.strip()[:500],
        #FIELD_SOURCE: "小智语音",
        #FIELD_REQID: req_id,
    }
    #if due:  fields[FIELD_DUE] = int(datetime.date.fromisoformat(due).strftime("%s")) * 1000 if re.fullmatch(r"\d{4}-\d{2}-\d{2}", due) else due
    #if prio: fields[FIELD_PRIO] = prio

    if DEBUG_MODE:
        # 注意：stdio 传输下 stdout 被 MCP 协议占用，调试信息必须输出到 stderr
        lines = [
            "=" * 50,
            f"[调试] 目标多维表格: app_token={FEISHU_APP_TOKEN}  table_id={FEISHU_TABLE_ID}",
            f"[调试] 原始 content : {content}",
            f"[调试] 识别 due    : {due}",
            f"[调试] 识别 prio   : {prio}",
            f"[调试] 生成 title  : {make_title(content)}",
            f"[调试] request_id  : {req_id}",
            "[调试] 将要提交的 fields:",
            json.dumps(fields, ensure_ascii=False, indent=2),
            "=" * 50,
        ]
        sys.stderr.write("\n".join(lines) + "\n")
        sys.stderr.flush()
        return {"success": True, "debug": True, "request_id": req_id, "fields": fields}

    if not FEISHU_APP_ID:
        return {"success": False, "error": "未配置 FEISHU_APP_ID/SECRET/APP_TOKEN/TABLE_ID"}
    res = feishu_create_record(fields)
    res["request_id"] = req_id
    return res

if __name__ == "__main__":
    log.info("server 启动 (DEBUG_MODE=%s)，FEISHU_APP_ID=%s, FIELD_RAW=%s",
             DEBUG_MODE,
             (FEISHU_APP_ID[:8] + "...") if FEISHU_APP_ID else "(空)",
             FIELD_RAW)
    if not all([FEISHU_APP_ID, FEISHU_APP_SECRET, FEISHU_APP_TOKEN]):
        log.warning("飞书环境变量未配齐，add_todo 将返回错误。")
    mcp.run(transport="stdio")
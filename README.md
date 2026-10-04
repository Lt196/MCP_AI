# MCP_AI：小智语音待办 → 飞书多维表格

把「小智」语音助手说的一句话，落成飞书多维表格里的一条待办记录。

项目由两个可独立运行的部分组成：

| 文件 | 作用 | 传输 |
| --- | --- | --- |
| [server.py](server.py) | 本地 MCP Server，注册唯一工具 `add_todo`，调用飞书开放接口写入多维表格 | stdio |
| [mcp_pipe.py](mcp_pipe.py) | 把小智 MCP 接入点（WebSocket）与本地 stdio 服务互相转发 | WebSocket ↔ stdio |

链路：`小智设备 → xiaozhi.me 接入点（ws）→ mcp_pipe.py → server.py（stdio）→ 飞书 bitable API`

> **当前能力边界（务必先读）**：`server.py` 只注册了 `add_todo` 一个工具，**没有**联网搜索 / `web_search` 能力；且因已知缺陷 BUG-03，`add_todo` 目前**只写入「原始语音」一列**，截止日期 / 优先级 / 请求ID 虽已被抽取但不会落库。详见 [已知问题](#9-已知问题)。

---

## 1. 环境要求

| 项 | 版本 / 说明 |
| --- | --- |
| Python | 3.10+（开发与测试环境为 3.13.12） |
| 运行依赖 | `mcp>=1.2.0`、`requests>=2.31`；跑 pipe 还需 `websockets>=11.0`、`python-dotenv>=1.0` |
| 系统 | Windows（[run_tests.cmd](run_tests.cmd) 为 cmd 批处理；其他系统用等价的 pytest 命令） |
| 外部条件 | 飞书自建应用 + 一张多维表格；小智智能体的 MCP 接入点地址 |

## 2. 安装

```powershell
cd E:\26urt\MCP_AI
venv\Scripts\python.exe -m pip install -r requirements.txt                            # 只跑 server.py
venv\Scripts\python.exe -m pip install -r requirements.txt -r requirements-dev.txt    # 连测试依赖一起装
```

## 3. 配置

### 3.1 环境变量（写在根目录 `.env`，程序启动时自动加载）

`.env` 格式为 `KEY=value`，一行一个，`#` 开头为注释；[server.py](server.py) 与 [test_feishu.py](test_feishu.py) 也兼容 `export KEY=value` 写法。

| 变量 | 必填 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `FEISHU_APP_ID` | 是 | — | 飞书自建应用 App ID，`cli_xxx` |
| `FEISHU_APP_SECRET` | 是 | — | 飞书自建应用 App Secret |
| `FEISHU_APP_TOKEN` | 是 | — | 多维表格 base/app token，`bascnXXX`；wiki 链接需先 `get_node` 换 obj_token |
| `FEISHU_TABLE_ID` | 是 | `tblxxxx`（占位值） | 表格 URL 中 `table=` 后的值 |
| `MCP_ENDPOINT` | 跑 pipe 时必填 | — | 小智控制台复制的接入点，形如 `wss://api.xiaozhi.me/mcp/?token=xxx`；未设置时 [mcp_pipe.py](mcp_pipe.py) 直接以退出码 1 结束 |
| `MCP_CONFIG` | 否 | 空 → 回退到 `./mcp_config.json` | 自定义配置 JSON 路径 |
| `FIELD_TITLE` | 否 | `任务内容` | 标题列名（当前不写入） |
| `FIELD_RAW` | 否 | `原始语音` | **当前唯一真正写入的列**，必须与表里列名一字不差 |
| `FIELD_DUE` | 否 | `截止日期` | 日期列（当前不写入） |
| `FIELD_PRIO` | 否 | `优先级` | 单选列（当前不写入） |
| `FIELD_SOURCE` | 否 | `来源` | 当前不写入 |
| `FIELD_REQID` | 否 | `请求ID` | 幂等去重列（当前不写入） |
| `DEFAULT_PRIO` | 否 | `普通` | 猜不出优先级时的兜底值 |

注意：**不要把变量写成空值**（如 `FEISHU_TABLE_ID=`）。空串会被视为「已配置」，程序不会回退到默认值，反而更难排查。

`.env` 模板：

```dotenv
# --- 必填 ---
FEISHU_APP_ID=cli_xxx
FEISHU_APP_SECRET=xxx
FEISHU_APP_TOKEN=bascnXXX
FEISHU_TABLE_ID=tblXXX
MCP_ENDPOINT=wss://api.xiaozhi.me/mcp/?token=xxx

# --- 可选：字段名与表格列名不一致时才取消注释 ---
# FIELD_TITLE=任务内容
# FIELD_RAW=原始语音
# FIELD_DUE=截止日期
# FIELD_PRIO=优先级
# FIELD_SOURCE=来源
# FIELD_REQID=请求ID
# DEFAULT_PRIO=普通
```

> **加载路径差异**：[server.py](server.py) 读的是**脚本所在目录**的 `.env`；[mcp_pipe.py](mcp_pipe.py) 用 `load_dotenv()`，从**当前工作目录**向上查找。请始终在项目根目录启动命令，两边才能读到同一份 `.env`。

### 3.2 调试模式

[server.py](server.py) 第 48 行 `DEBUG_MODE = False`：

- `False`（当前值）：正常写入飞书。
- 改为 `True`：不写飞书，改为把「将提交的 fields、识别出的日期/优先级」打印到 **stderr**，并直接返回结果，用于核对列名。

`DEBUG_MODE` 打开时会把 `app_token`、`table_id` 打到 stderr，注意日志外泄。

### 3.3 多服务配置 `mcp_config.json`（可选）

[mcp_pipe.py](mcp_pipe.py) 不带参数运行时会读 `$MCP_CONFIG`，否则读当前目录的 `mcp_config.json`，按其中**启用的**服务逐个建立连接：

```json
{
  "mcpServers": {
    "feishu": {
      "type": "stdio",
      "command": "python",
      "args": ["server.py"],
      "env": { "FIELD_RAW": "原始语音" }
    },
    "remote-demo": {
      "type": "sse",
      "url": "https://example.com/mcp",
      "headers": { "Authorization": "Bearer xxx" },
      "disabled": true
    }
  }
}
```

- `type` 支持 `stdio`、`sse`、`http`、`streamablehttp`（别名 `transportType` 等价），缺省为 `stdio`。
- 远端类型会用当前 Python 执行 `python -m mcp_proxy` 做桥接，因此需要额外安装 `mcp-proxy`。
- `disabled: true` 的服务只打印一行 `Skipping disabled servers: ...` 后跳过。
- JSON 损坏时降级为空配置并给出警告，不会崩溃。

## 4. 运行

```powershell
# 方式 A：常驻 stdio 服务，供接入点/客户端自己拉起（最常用）
venv\Scripts\python.exe server.py

# 方式 B：本地脚本 + 小智接入点，由 pipe 拉起 server.py
venv\Scripts\python.exe mcp_pipe.py server.py

# 方式 C：启动 mcp_config.json 里所有启用的服务
venv\Scripts\python.exe mcp_pipe.py
```

> 已知缺陷 BUG-02：`python mcp_pipe.py <配置里的服务名>` 会被拒绝并退出（入口只接受存在的脚本路径）。按名启动请留空参数走方式 C。

连接断开后 pipe 会按 1s → 2s → 4s … 指数退避重连，上限 600s。

## 5. 小智侧接线

1. xiaozhi.me 控制台 → 智能体 → 配置角色 → 右下角复制该智能体的 **MCP 接入点**。
2. 把接入点地址写入 `.env` 的 `MCP_ENDPOINT`，按方式 B 或 C 让 pipe 常驻。
3. 对设备说「记一下：明天下午提醒我买牛奶」，模型应调用 `add_todo`。
4. 服务端日志会打印工具入参；飞书表格应新增一条记录。

## 6. 飞书侧准备

1. 在飞书开放平台创建自建应用，开通 `bitable:app` 权限并发布版本。
2. 在多维表格里点「... → 添加文档应用/协作者」，把这个应用加进去，否则写入返回 403。
3. 表格至少要有与 `FIELD_RAW` 同名的文本列（默认「原始语音」）。
4. 若要让截止日期/优先级/请求ID 生效，需等 BUG-03 修复，并预先加好对应列：日期列（写入毫秒时间戳）、单选列、文本列。

## 7. 工具契约

[server.py](server.py) 通过 FastMCP 注册一个工具：

| 工具 | 入参 | 返回 |
| --- | --- | --- |
| `add_todo` | `content`（必填，用户原始表述）、`due_date`（可选）、`priority`（可选） | `{"success": bool, "record_id"?: str, "request_id": "xz-<毫秒时间戳>", "error"?: str}`；`DEBUG_MODE=True` 时为 `{"success": true, "debug": true, "request_id", "fields"}` |

服务端行为：

- `content` 截断到 500 字并去除首尾空白；`request_id` 为 `xz-` + 毫秒时间戳。
- `due_date` / `priority` 为空时由 [server.py](server.py) 兜底猜测（中文「明天/后天/今天/下周X/M月D日」、关键词「紧急/尽快/重要/优先/记得」），猜不出返回 `None`。
- 飞书写入最多重试 3 次，仅网络异常重试；业务错误立即返回；token 失效码 `99991663/99991664` 会刷新 token 后重试一次。
- 未配置 `FEISHU_APP_ID` 时直接返回「未配置」错误，不发请求。

## 8. 测试

自动化用例见 [测试用例设计.md](测试用例设计.md)，最近一次执行结果见 [tests/测试结果.txt](tests/测试结果.txt)：83 条用例，`77 passed, 6 xfailed, 0 failed`（6 条 `xfail` 是已确认缺陷的守护用例，不是失败）。

```powershell
run_tests.cmd                          # 全量
run_tests.cmd -m "not integration"     # 快速子集（不拉子进程）
run_tests.cmd -v -k "next_week"        # 单个主题

# 等价的显式命令
venv\Scripts\python.exe -m pytest
venv\Scripts\python.exe -m pytest -m "not integration"
```

三条内置环境约定（见 [tests/conftest.py](tests/conftest.py)）：

1. 所有环境变量由用例显式赋值，本机 `.env` / 系统环境不影响结果；
2. `server.py` 导入期对 `sys.stdout` 的包装被替换为安全流，不破坏 pytest 捕获；
3. 非 `integration` 用例中任何真实 TCP 连接都会直接失败，避免误写线上表格。

[test_feishu.py](test_feishu.py) 是**手工**脚本，会真实写入飞书表格，已被 [pytest.ini](pytest.ini) 的 `testpaths = tests` 排除在自动化之外：

```powershell
venv\Scripts\python.exe test_feishu.py     # 需要 .env 里配好真实密钥
```

## 9. 已知问题

完整证据、复现与建议见 [测试用例设计.md](测试用例设计.md) 第 7 节。摘要：

| 编号 | 级别 | 问题 | 影响 |
| --- | --- | --- | --- |
| BUG-01 | 高 | 「下周X」跨周计算错误（server.py:96-100） | 周三说「下周五」会被记成本周五 |
| BUG-03 | 高 | `add_todo` 只写「原始语音」一列，其余字段赋值被注释（server.py:131-137） | 截止日期/优先级丢失，请求ID 幂等去重不生效 |
| BUG-02 | 中 | pipe 入口不接受配置里的服务名（mcp_pipe.py:253、270-275） | 无法按名启动单个已配置服务 |
| BUG-06 | 中 | 重试退避固定 1/2/3 秒，最后一次失败后仍多睡 3 秒 | 最坏白等 6 秒，语音体感差 |
| BUG-07 | 中 | 只校验 `FEISHU_APP_ID`，APP_TOKEN/TABLE_ID 缺失也照发请求 | 报错信息难以定位 |
| BUG-08 | 中 | pipe 顶层异常被吞，进程以退出码 0 结束 | 看护/编排脚本感知不到失败 |
| BUG-09 | 中 | 注释代码里的 `strftime("%s")` 在 Windows 不可用 | 恢复注释即报错 |
| BUG-04 | 低 | 优先级关键词大小写敏感、无否定判断 | 「URGENT」识别不到，「不重要」被判成「重要」 |
| BUG-05 | 低 | 标题清洗残留分隔符 | 「记住：明天开会」→「：明天开会」 |

## 10. 排障

| 现象 | 可能原因与处理 |
| --- | --- |
| `Please set the MCP_ENDPOINT environment variable` | `.env` 缺 `MCP_ENDPOINT`，或启动目录不是项目根目录 |
| `add_todo` 返回「未配置 ...」错误 | `FEISHU_APP_ID` 为空；四个飞书变量都要填 |
| 返回 `1254005 FieldNameNotFound` | 表格列名与 `FIELD_RAW` 不一致；打开 `DEBUG_MODE=True` 核对 fields 键名 |
| 返回 403 / 权限错误 | 应用未加入该多维表格的协作者，或未开通 `bitable:app` |
| 写入成功但表格看不到内容 | 记录写进了别的表：核对 `FEISHU_APP_TOKEN`、`FEISHU_TABLE_ID` |
| `python mcp_pipe.py <服务名>` 立即退出 | BUG-02，改用不带参数的方式启动 |
| 语音有确认但表格无记录 | `DEBUG_MODE=True` 所致（不写库）；改回 `False` |

## 11. 目录结构

```
MCP_AI/
├─ server.py             # MCP Server：add_todo → 飞书多维表格
├─ mcp_pipe.py           # WebSocket(接入点) ↔ stdio 转发 + 多服务启动
├─ test_feishu.py        # 手工联调脚本（会真实写表，不参与自动化）
├─ mcp_config.json       # 可选：多服务配置（不存在则单脚本运行）
├─ requirements.txt      # 运行依赖
├─ requirements-dev.txt  # 测试依赖
├─ pytest.ini            # 只收集 tests/
├─ run_tests.cmd         # 一键跑测试
├─ README.md             # 本文档
├─ README.old.md         # 早期文档存档（含已不存在的 web_search 描述）
├─ 测试用例设计.md        # 83 条用例设计 + 缺陷清单
└─ tests/
   ├─ conftest.py            # 环境隔离、安全导入、假 HTTP 替身、网络封锁
   ├─ test_extract.py        # 中文日期/优先级/标题抽取
   ├─ test_feishu_api.py     # token 缓存、写入重试
   ├─ test_add_todo.py       # 工具注册契约与字段组装
   ├─ test_mcp_pipe.py       # 配置解析、命令组装、进程入口
   ├─ test_server_stdio.py   # stdio 端到端协议（官方 MCP SDK）
   └─ 测试结果.txt            # 最近一次执行汇报
```

## 12. 安全提示

- 根目录 `.env` 含真实密钥，**不要提交到版本库、不要随包分发**；一旦泄露请立即在飞书开放平台重置 App Secret。
- `DEBUG_MODE=True` 与启动日志会打印 `app_token`、`table_id`（`FEISHU_APP_ID` 只打印前 8 位），注意日志留存范围。

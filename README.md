# 小智本地 MCP：飞书待办入库

将语音或对话中的待办事项写入飞书多维表格的 MCP 服务。

## 安装

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
```

编辑 `.env`，填写飞书应用凭据和多维表格信息。`.env` 是本地私密配置，不要提交或分享；`.gitignore` 已将其排除。也可以直接通过系统环境变量配置。

| 变量 | 说明 |
| --- | --- |
| `FEISHU_APP_ID` | 飞书自建应用 App ID |
| `FEISHU_APP_SECRET` | 飞书自建应用 App Secret |
| `FEISHU_APP_TOKEN` | 多维表格 app token；wiki 链接需先通过 `get_node` 获取 `obj_token` |
| `FEISHU_TABLE_ID` | 多维表格中的表格 ID |
| `FIELD_RAW` | 原始语音对应的列名，默认 `原始语音` |
| `FIELD_TITLE`、`FIELD_DUE`、`FIELD_PRIO`、`FIELD_SOURCE`、`FIELD_REQID` | 可选列名映射，必须与表格列名一致 |
## 运行

```powershell
python server.py
```

服务通过 stdio 提供 MCP，运行期间保持进程开启。

## 小智侧接线
1. xiaozhi.me 控制台 → 智能体 → 配置角色 → 右下角复制该智能体的 MCP 接入点/启动命令。
2. 把本项目挂到该接入点；语音里说“记一下：明天下午提醒我买牛奶”，模型应调 add_todo。
3. 优先级/截止日期的主抽取靠大模型按工具参数传；服务端只做兜底猜测，别把兜底当真理。

## 飞书侧准备
- 自建应用开通 bitable:app 权限；
- 在该多维表格「... → 添加文档应用/协作者」里加这个应用，否则 403；
- 建议加一列“请求ID”文本字段做幂等去重；日期列是日期类型，写入的是毫秒时间戳。

## 验证顺序
运行自动化测试：

```powershell
.\run_tests.cmd
```

配置飞书后，可通过小智语音创建待办，并在飞书表格中确认结果。

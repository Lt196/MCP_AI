# 小智本地 MCP：飞书待办入库

## 安装
pip install -r requirements.txt

## 配置（环境变量，建议写进启动脚本或 .env 后 source）
export FEISHU_APP_ID=cli_xxx
export FEISHU_APP_SECRET=xxx
export FEISHU_APP_TOKEN=bascnXXX            # 多维表格链接里的 base/app token；wiki 链接要先 get_node 换 obj_token
export FEISHU_TABLE_ID=tblXXX               # 表格 URL 里 table= 后面的值
# 字段名必须与表里列名一字不差，不一致就覆盖：
# export FIELD_TITLE=任务内容 FIELD_RAW=原始语音 FIELD_DUE=截止日期 FIELD_PRIO=优先级 FIELD_SOURCE=来源 FIELD_REQID=请求ID
# 可选：export SERPAPI_KEY=xxx   （更稳的搜索；不配则走 DuckDuckGo lite）

## 跑起来
python server.py        # stdio，保持常驻

## 小智侧接线
1. xiaozhi.me 控制台 → 智能体 → 配置角色 → 右下角复制该智能体的 MCP 接入点/启动命令。
2. 把本项目挂到该接入点；语音里说“记一下：明天下午提醒我买牛奶”，模型应调 add_todo。
3. 优先级/截止日期的主抽取靠大模型按工具参数传；服务端只做兜底猜测，别把兜底当真理。

## 飞书侧准备
- 自建应用开通 bitable:app 权限；
- 在该多维表格「... → 添加文档应用/协作者」里加这个应用，否则 403；
- 建议加一列“请求ID”文本字段做幂等去重；日期列是日期类型，写入的是毫秒时间戳。

## 验证顺序
1. 本地直接 python -c 调 feishu_create_record 验证 token 和字段；
2. 用小智语音说一条待办，看服务端日志里 add_todo 的入参；
3. 飞书表格确认记录，再测 web_search。

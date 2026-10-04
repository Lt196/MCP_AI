# -*- coding: utf-8 -*-
import os

# 1. 手动加载同目录下的 .env（兼容 export KEY=VALUE 和 KEY=VALUE 两种写法）
env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
if os.path.exists(env_path):
    with open(env_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[7:]
            if "=" in line:
                k, v = line.split("=", 1)
                os.environ[k.strip()] = v.strip().strip('"').strip("'")
    print("[OK] 已加载 .env")
else:
    print("[WARN] 没找到 .env，将用系统环境变量")

# 2. 环境变量必须在 import server 之前设好，所以放这里再导入
import server

# 3. 测试字段：直接用 server 里配置的 FIELD_RAW，不用手写列名
test_fields = {
    server.FIELD_RAW: "提醒我明天上午 10 点买洗发水",
}

print("即将写入字段:", list(test_fields.keys()))
result = server.feishu_create_record(test_fields)
print("写入结果:", result)
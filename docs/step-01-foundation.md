# 第一步：开发环境与离线会话闭环

## 目标和当前能力

使用 Python 3.11 和项目 `.venv`，让命令行经由 `AgentLoop` 运行单轮和多轮会话。当前模型是明确标识的 Echo，仅回复输入；不搜索新闻、不推理、不调用真实 API。多轮上下文在内存中保留，关闭程序后不保存。

## 使用方式

在 PyCharm 打开本仓库，将解释器设为项目根目录 `.venv\Scripts\python.exe`。终端工作目录设为仓库根目录。

```powershell
.venv\Scripts\python.exe --version
.venv\Scripts\python.exe -m newspaper_agent --message "研究人工智能新闻"
.venv\Scripts\python.exe -m newspaper_agent --interactive
```

交互输入：普通文本产生一轮回复；空白文本忽略；`/reset` 清空当前上下文；`/exit`、Ctrl+C 或输入结束退出。`--message` 默认为 `newspaper_agent ready`；同时指定 `--interactive` 时进入交互模式。此阶段无需环境变量或 API key，也不读取模型配置文件。

## 学习顺序与练习

1. 虚拟环境与包：解释 `.venv`、`requirements.txt`、`pyproject.toml` 和 `src` 布局的职责。可编辑安装 `pip install -e .` 让 `python -m newspaper_agent` 可运行。
2. 入口与组装：阅读 `__main__.py` 和 `app.py`，沿着输入 → `AgentLoop.run` → `EchoModel.complete` → 输出跟踪调用。
3. 数据结构：阅读 `Message` 和 `AgentState`。输入两轮后应有 user/assistant/user/assistant 四条消息；它们构成模型上下文。
4. 依赖注入：观察循环接收模型对象，CLI 接收读写函数。替换模型和终端 IO 不需要修改核心循环。
5. 离线测试：阅读 `test_app.py` 的 RecordingModel，检查第二次调用收到第一轮历史。Echo 本身不理解历史，但循环必须正确传递历史。
6. 动手练习：运行两轮会话、输入空行、执行 `/reset` 再输入；再调试一次测试，观察状态变化。

## 验证、错误与边界

```powershell
.venv\Scripts\python.exe -m compileall src tests
.venv\Scripts\python.exe -m pytest -q
.venv\Scripts\python.exe -m newspaper_agent
```

CLI 参数错误由 argparse 显示帮助并退出；EOF/Ctrl+C 正常结束。此阶段的 Echo 没有网络调用、数据库写入或会话落盘。真实模型接入和失败恢复已在第二步实现，见 `step-02-live-model.md`；上下文限制和工具调用属于后续步骤。

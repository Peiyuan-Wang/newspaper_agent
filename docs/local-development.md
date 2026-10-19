# 本机开发目录与迁移

目标路径：`C:\Users\13689\Documents\newspaper_agent`。

Git 的 `.git` 随仓库整体移动，保留历史、分支、远程、暂存区和未提交文件；origin 仍为 `https://github.com/AssassinsAlex/newspaper_agent.git`。移动文件夹不需要改远程 URL，不会自动提交或上传。

Python 3.11 基础运行时保存在 `.python-runtime/`；新 `.venv` 基于它建立。两者都被 Git 忽略。原长路径目录因 Windows 进程占用而暂时完整保留，作为迁移备份；后续开发使用本页的新目录。不要移动单独的虚拟环境；将来更换目录时重新建立它：

```powershell
.\.python-runtime\python.exe -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt -e .
```

PyCharm：打开新项目文件夹，把解释器设为 `C:\Users\13689\Documents\newspaper_agent\.venv\Scripts\python.exe`，运行配置的 Working directory 设为新仓库目录。

```powershell
cd "C:\Users\13689\Documents\newspaper_agent"
.\.venv\Scripts\python.exe -m newspaper_agent --interactive
.\.venv\Scripts\python.exe -m pytest -q
git status
```

Git 本地配置保留 OpenSSL 和 DPAPI，GitHub 认证保存在当前 Windows 用户的加密存储。上传代码时使用此用户权限的普通终端；受限沙箱可能不能读取 DPAPI。`git push --dry-run origin main` 可验证上传权限而不更新远程分支。真正上传仍需先检查 diff，再按明确任务提交、推送。

pytest 在 `.pytest_cache/tmp` 保存本项目的临时文件，每次运行会清理这一专用子目录，不要在那里保存资料。这避免不同权限环境共用系统临时目录导致 PermissionError。`.pytest_cache` 已被 Git 忽略。

迁移后须运行编译、全套测试、CLI、Git diff 检查及远程访问检查。API key 仍通过环境变量注入；本次迁移不写入密钥、不调用付费模型。

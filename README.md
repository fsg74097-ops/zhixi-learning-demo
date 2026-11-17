# 智析学伴 · 公开演示

交互网址：https://major-utilize-perspective-drew.trycloudflare.com

当前交互服务使用临时公网连接，演示电脑与服务需保持运行；停止后该地址失效。`docs/index.html` 是 GitHub Pages 的静态访问入口。长期独立云部署可使用下方的 Streamlit Community Cloud 配置。

七年级数学学情、知识探索、受控 Agent 与教师任务单演示。仅包含 8 名虚构学生、4 道自建题和 6 张项目自建 CSV；不包含赛题原始数据、教材 PDF、真实学生资料、运行数据库或模型凭证。

```bash
python -m pip install -r requirements.txt
python -m streamlit run public_app.py
```

从仓库根目录启动。`public_app.py` 固定启用 `PUBLIC_DEMO_MODE=1`；页面与数据加载器仅允许 `demo_data/math_workflow`，知识库服务器文件导入已关闭。正常的教师与学生演示页面、任务草稿版本编辑、教师确认、学生自检和导出仍可使用。

DeepSeek 为可选功能，默认无凭证时使用本地统计与数学核验。仅通过部署平台秘密变量配置 `DEEPSEEK_API_KEY`，或在模型设置页输入仅当前会话使用的凭证；不要提交密钥。本仓库不包含独立 HTTP API 服务，Python Agent 接口在 `02_项目代码/agent_runtime.py`。

Streamlit Community Cloud：入口 `public_app.py`，选择 Python 3.11。依赖和配置均在仓库根目录。

这是角色切换演示，不提供生产身份认证。运行时本地记录不保证在云端重启后持久保存。模型建议属于待核验草稿，代码核验与引用检查不等同于教师人工验收。

回归测试：`python -m pytest tests -q --basetemp=outputs/test-tmp`（先创建 `outputs` 目录）。测试不会调用模型。

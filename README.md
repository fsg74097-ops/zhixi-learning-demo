# 智析学伴 · 公开演示

教师工作台、班级分析、学生端、知识星图、任务单和可审计的 Agent 使用独立自建数据。

[进入重做版演示](https://marshall-serves-representatives-referrals.trycloudflare.com)。暂时通过开发电脑上的隧道提供服务，电脑或连接停止时可使用下面的本地启动方式。

在仓库目录使用 Python 3.11 和 Node.js 24：

    python -m pip install -r "02_项目代码/requirements.txt" -c config/dependency_constraints.txt
    npm --prefix web ci
    npm --prefix web run build
    python -X utf8 serve_hex.py --demo --port 8511

访问 http://127.0.0.1:8511 。公开演示使用本地核验及受控 Agent 工具。模型凭证可由部署方通过环境配置。

本仓库仅包含自建数据、程序及接口文档。

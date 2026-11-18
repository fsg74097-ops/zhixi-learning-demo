# build_learning_profile｜智析学伴专业 Skill

- `skill_id`: `build_learning_profile`
- `skill_version`: `1.1.0-candidate`
- `input_schema`: `../../schemas/skill_request.schema.json`
- `output_schema`: `../../schemas/skill_response.schema.json`
- `source_snapshot`: 必需，后端权限注入

## 任务目标
把学生/班级经核验学习证据汇总成个人画像或班级发现

## 最小输入
有效快照、学生或班级范围、证据引用、分母口径和受众。必须提供有效 request_id、snapshot_hash、goal、trace_id、约束；缺少返回 MISSING_REQUIRED_INPUT，不调用模型猜。

## 允许工具
`query_learning_records`, `compute_stats`, `search_knowledge`, `get_evidence_item`, `get_data_state`, `analyze_responses`。

## 输出
统计、可确认正确表现、可观察困难、原因假设、行动候选、证据和限制。先结构化事实再转为教师/学生文案；所有事实句须保留 evidence_refs 和快照。

## 安全与业务边界
不改官方分数，不自行计算统计，不将 n=1 宣称掌握，不越权。

## 故障处理
无权→ACCESS_DENIED；快照过期→STALE_SNAPSHOT；无资料→EVIDENCE_NOT_FOUND；模型超时→MODEL_TIMEOUT；资源不足→RESOURCE_UNAVAILABLE 或 INSUFFICIENT_ITEMS。可降级输出已确认的统计/事实，但不得伪造成功。

## 独立运行
Skill Registry 加载本说明与 JSON Schema；测试入口无需完整 Agent。测试集至少含正常、缺数据、旧快照、越权、模型不可用、证据冲突和资源不足。第三 AI 与 L3.5 为内置验证组件，不由 Skill 直接绕过。

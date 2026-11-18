# recommend_learning_tasks｜智析学伴专业 Skill

- `skill_id`: `recommend_learning_tasks`
- `skill_version`: `1.1.0-candidate`
- `input_schema`: `../../schemas/skill_request.schema.json`
- `output_schema`: `../../schemas/skill_response.schema.json`
- `source_snapshot`: 必需，后端权限注入

## 任务目标
把经证实的学习问题转化为可审查任务候选

## 最小输入
已核验问题、目标知识、授权学生范围、题量时间与题源策略。必须提供有效 request_id、snapshot_hash、goal、trace_id、约束；缺少返回 MISSING_REQUIRED_INPUT，不调用模型猜。

## 允许工具
`get_data_state`, `search_knowledge`, `get_evidence_item`, `find_practice_items`, `compute_stats`, `analyze_responses`, `create_assignment_draft`。

## 输出
练习候选、选题理由、证据、缺口和可选草稿引用。先结构化事实再转为教师/学生文案；所有事实句须保留 evidence_refs 和快照。

## 安全与业务边界
不得批准/派发，不凭空造题，不把教材切片当练习题。

## 故障处理
无权→ACCESS_DENIED；快照过期→STALE_SNAPSHOT；无资料→EVIDENCE_NOT_FOUND；模型超时→MODEL_TIMEOUT；资源不足→RESOURCE_UNAVAILABLE 或 INSUFFICIENT_ITEMS。可降级输出已确认的统计/事实，但不得伪造成功。

## 独立运行
Skill Registry 加载本说明与 JSON Schema；测试入口无需完整 Agent。测试集至少含正常、缺数据、旧快照、越权、模型不可用、证据冲突和资源不足。第三 AI 与 L3.5 为内置验证组件，不由 Skill 直接绕过。

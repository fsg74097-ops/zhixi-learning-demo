# HTTP presentation bridge v1 — implementation contract

This adapter reuses the existing Python evidence, governance, math, tool, Skill,
Gate and assignment services. The React frontend does not calculate scores or
create student findings. The current Streamlit applications remain available.

## Session and response

`POST /api/session` accepts `{role: "TEACHER"|"STUDENT", student_id?: string}`.
The server issues an opaque HttpOnly demo session cookie and binds role/student
scope server-side. This is explicitly `DEMO_ROLE_SWITCH`, not production auth.
All later requests obtain their audience from that session, never a caller's
`audience` field. Student endpoints expose only the bound student's records and
omit answers/rubric solutions before submission.

Responses use `{ok, data, error?, trace_id, snapshot_hash, limitations}`.
Error is `{code, message, retryable}`. Missing/zero/pending/unobserved states are
distinct. Stats preserve numerator, denominator, n, coverage and exclusions.

## Bootstrap and views

- `GET /api/health`: runtime health only, no credentials.
- `GET /api/context`: `{app_version, data_origin, dataset_label, snapshot_hash,
  model_configured, role, student_id, nodes:[{id,label}], classes:[{id,label}],
  students:[{id,label,class_id}], registry:{tools,skills}, capabilities, warnings}`.
- `GET /api/views/{view}?node_id=...&student_id=...&class_id=...`: real, readable
  view models for `dashboard`, `classes`, `profile`, `diagnosis`, `knowledge`,
  `assignments`, `agent`, `governance`, `learning`, `practice`.
  Exact view payloads are documented by the backend owner in this file before
  frontend binding. Always keep normal findings and evidence references apart.
- `GET /api/evidence/{entity_id}`: exact source item subject to session scope.
- `GET /api/responses/{record_id}`: source response, rubric, supported math
  result, evidence references and current effective/official/candidate status.
- `GET /api/knowledge/relations?question_id=...`: teacher-only relation versions, Q-matrix and review history.
- `POST /api/knowledge/relations/{relation_id}/reviews`: explicit teacher review with
  `{expected_snapshot_hash,review_status,reviewer_id,reason}`; the new relation version
  is auditable and stale versions cannot be reviewed.
- `GET /api/knowledge/practice` returns teacher-only independent catalog `{items,reviews}`.
- `POST /api/knowledge/practice` accepts `{items:[{practice_id,exact_text,reference_answer,
  reference_explanation,source_description,author,knowledge_refs,authorized_use}],
  reviewer_id,expected_snapshot_hash}`. Registration always starts as a candidate.
- `POST /api/knowledge/practice/{practice_id}/review` accepts
  `{approved,authorized_use,reviewer_id,expected_snapshot_hash}`. Only an
  authorized and explicitly accepted practice enters the selection pool.
- `GET /api/knowledge/sources` returns teacher-only `{sources,passages}`. Each available
  local source is identified by `id,title,sha256` and selected from the fixed source index.
- `POST /api/knowledge/sources/passages` accepts `{source_id,passage_id,exact_text,
  physical_page,printed_page?,resource_title,edition,node_ids,reviewer_id,
  expected_snapshot_hash}`. The server validates the exact PDF text layer and
  source hash before storing a candidate. Public demos cannot import files. Scanned
  sources use the existing audited OCR import workflow.

## Bounded commands

- `POST /api/tools/{tool_id}` body `{arguments, student_id?}`; only existing
  seven read/analysis tools plus the one draft-write tool, using schema checks.
- `POST /api/skills/{skill_id}` body `{arguments, student_id?}`; only the two
  approved Skills. Load their current contract and validate request/response.
  Callers may send `{request, student_id?}` with the full v1.1 SkillRequest in
  place of `arguments`. The server checks the URL skill matches the request and
  returns the usual envelope with Skill data, `skill_status` and `model_metadata`.
- `POST /api/agent` body `{goal, student_id?, node_id?, use_model}`.
- `POST /api/external-authorization` body `{snapshot_hash, confirmed,
  response_refs}`: bind explicit permission to exact snapshot/record subset.
  Real official-data verification currently permits only D01/N02/DR0023 and
  necessary source slices; never send full OCR pages or local paths.

Long DS calls run in server jobs: command returns `{job_id, status}` when
asynchronous. `GET /api/jobs/{id}` returns `{status, result?, error?, progress}`
within the same session. No raw chain-of-thought; show tool execution summaries.

## Assignment actions

- `GET /api/assignments` (student sees only confirmed assignments for self).
- `POST /api/assignments` creates a draft from `{request}`.
- `POST /api/assignments/complementary-groups` teacher-only comparison from
  `{class_id,node_id,indicator_ids}` (at least two indicators). Returns traceable
  temporary candidates or an explicit insufficient/no-match status.
- `POST /api/assignments/complementary-groups/{candidate_id}/draft` accepts the
  same scope plus `expected_snapshot_hash,item_count,max_minutes`. The server
  recomputes eligibility, records candidate evidence in a teacher-only draft
  and leaves confirmation to the existing teacher action.
- `GET /api/assignments/{id}` gets an immutable revision.
- `POST /api/assignments/{id}/revisions` body `{revision, changes}`.
- `POST /api/assignments/{id}/validate` body `{revision}`.
- `POST /api/assignments/{id}/confirm` body `{revision, teacher_id,
  idempotency_key}`; explicit teacher click required, never an Agent route.
- `POST /api/assignments/{id}/export` body `{revision}`; returns a download
  route or ZIP response with correct content disposition, no fake dispatch.
- `POST /api/assignments/{id}/answers` body `{revision, draft_item_id, answer,
  process, hint_level}`; student identity comes from server session.
- `GET /api/answers` returns current student's own history or teacher scope.
- `POST /api/practice/{practice_id}/answers` body `{snapshot_hash, answer, process, hint_level}` checks the currently approved catalog and the student session. It saves an isolated self-check attempt with `kind=SELF_PRACTICE`, `draft_id=null`, `revision=null` and the same mathematical feedback structure as an assignment attempt.

## Governance

- `GET /api/governance` returns actual snapshot summary, quality, current rows,
  revisions, candidates, decisions and manifest, with provenance in details.
- `POST /api/governance/decisions` wraps append-only teacher_decision and
  requires expected snapshot/revision, actor and reason.
- `POST /api/governance/candidates` wraps controlled candidate validation;
  unavailable sources remain unresolved, and official values stay immutable.

## Deployment and validation

Backend serves built `web/dist` for same-origin production use; dev CORS allows
only explicit local development origins. Public mode pins self-created data and
disables server-path import. Credentials stay in process/runtime secrets.
Implement meaningful integration tests for scope, stale snapshots, human-only
confirmation, zero/missing states, source quotes and a complete revised-task
submission. UI pages must be bound to these real endpoints before acceptance.

## Exact view DTO (React binding, v1)

Bootstrap always `POST /api/session` first (default UI `TEACHER`); unauthenticated
context/views return HTTP 401 with the standard envelope. The cookie is the only
scope authority. POSTing a new demo role replaces that browser's current session.

Every view data object has `{view, title, description, scope, metrics, findings,
actions, evidence_refs, limitations}`. `scope` is `{node_id, student_id, class_id}`.
Each response also includes `presentation`, validated against
`schemas/ui_response.schema.json`, preserving the same evidence and role.
`metrics` entries preserve runtime fields and add `id,label,value`; numeric
scores are `{numerator,denominator,n,coverage,denominator_policy,
denominator_definition,evidence_refs,observation_scope}`. A finding is
`{id,title,description,kind,verification,evidence_refs}`; verification uses the
Gate's four states. `actions` entries are `{id,label,target}`. Do not infer
claims from a chart or turn a missing `value` into zero.

Additional fields by view:

| View | Additional data |
|---|---|
| dashboard / classes | `students:[{id,label,class_id,metric}], observed_students, records_count, quality_summary, gate`. Top metrics represent the selected cohort; `classes` also returns `indicator_metrics` keyed by knowledge indicator. |
| profile / learning | `profile` (existing Skill result), `records` (current rows), `gate`, `assignments` (confirmed self for students) |
| diagnosis | `records:[{record_id,student_id,question_id,question_display,status,official_score,effective_score,effective_value_status,selected_revision,knowledge_refs}], selected_record_id, response` (first/specified response detail) |
| knowledge | `nodes:[{id,label,definition,knowledge_refs,n,coverage,rate,observation_scope}], edges:[{source,target,relation_type,review_status}], practice_items` |
| assignments | `assignments` (draft objects; student sanitized), `practice_items`, `knowledge_nodes:[{id,label}], students:[{id,label,class_id}]` |
| agent | `registry, model_configured, skill_contract_status` |
| governance | `summary, records, history, candidates, decisions, invalidations, practice_reviews, unresolved_issues, dataset_hash, snapshot_hash, source_files, rules_version, field_mapping, sensitivity`. The sensitivity comparison uses the same eligible events for official and effective sums and supplies n, denominator, coverage and exclusions. |
| practice | `assignments, practice_items, answers, hints` (`hints` maps knowledge ID to progressive directions) |

`GET /api/responses/{record_id}` returns `{record,question,rubric_points,
student_steps,knowledge_links,source_passages,evidence_refs,mathematics,gate}`.
Teacher responses also expose `step_rubric_links`, `step_node_links`,
`step_alignment`, `rubric_verification` when calculated by the knowledge layer.
Knowledge graph edges contain endpoints, review state and the shared question
references used to establish each joint assessment relationship.
Students receive their own record, question without reference answer/solution,
and no rubric solutions; detailed correctness/feedback is available only for
their submitted exercise attempt. Source objects never expose server paths or
full-page OCR metadata.

`GET /api/assignments` returns `{assignments:[...]}`; `/api/answers` returns
`{answers:[...]}`. Generic tool/Skill routes put the business `data` into the
HTTP envelope's `data`, and retain trace/gate/warnings metadata. Until the real
Skill Loader is implemented, `skill_contract_status="LEGACY_WORKFLOW"` and
limitation `LEGACY_SKILL_CONTRACT` explicitly disclose that gap.

An asynchronous command returns HTTP 202 with `data={job_id,status,progress}`.
Polling returns `data={job_id,status,progress,result,error}`. `result` is the
**complete standard HTTP envelope** of the completed command; unwrap
`poll.data.result.data` for its business result. Status is
`QUEUED|RUNNING|SUCCEEDED|FAILED`. Jobs are isolated to the issuing session.
Synchronous Agent command `data` preserves `{plan,steps,claims,gate,result}`.

Assignment `confirm` returns status `SUBMITTED_FOR_REVIEW`; `export` responds
with real ZIP bytes and `Content-Disposition: attachment`. Student answer
submission returns the existing practice record including
`draft_id,revision,draft_item_id,student_id,feedback.answer_validity`.

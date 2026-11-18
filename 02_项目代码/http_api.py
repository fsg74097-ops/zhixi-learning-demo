"""Session-scoped HTTP adapter; all business decisions stay in DemoRuntime."""
from contextlib import nullcontext
from dataclasses import dataclass, field
import os
from pathlib import Path
import re
from threading import RLock
from time import time
from uuid import uuid4

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field

from agent_runtime import DemoRuntime
from complementary_groups import suggest_complementary_groups
from http_jobs import JobStore
from http_presenters import (assignment_dto, build_view, clean_dto, governance_view,
                             response_view)
from workflow import build_bundle, DEFAULT_DATA

ROOT = Path(__file__).resolve().parents[1]
DEMO_LIMITATION = 'DEMO_ROLE_SWITCH / NOT_PRODUCTION_AUTH'
WEB_ASSET_MEDIA_TYPES = {'.js':'text/javascript','.mjs':'text/javascript','.css':'text/css',
                         '.svg':'image/svg+xml','.woff2':'font/woff2'}


class Input(BaseModel):
    model_config = ConfigDict(extra='forbid')


class SessionInput(Input):
    role: str
    student_id: str | None = None


class ToolInput(Input):
    arguments: dict = Field(default_factory=dict)
    student_id: str | None = None


class SkillInput(Input):
    arguments: dict | None = None
    request: dict | None = None
    student_id: str | None = None


class AgentInput(Input):
    goal: str = Field(min_length=1,max_length=4000)
    student_id: str | None = None
    node_id: str | None = None
    use_model: bool = False


class AuthorizationInput(Input):
    snapshot_hash: str
    confirmed: bool
    response_refs: list[str]


class CreateInput(Input):
    request: dict


class ComplementaryGroupInput(Input):
    class_id: str
    node_id: str
    indicator_ids: list[str] = Field(min_length=2,max_length=20)


class ComplementaryDraftInput(ComplementaryGroupInput):
    expected_snapshot_hash: str
    item_count: int = Field(default=3,ge=1,le=30)
    max_minutes: int = Field(default=15,ge=1,le=180)


class RevisionInput(Input):
    revision: int = Field(ge=1)


class EditInput(RevisionInput):
    changes: dict


class ConfirmInput(RevisionInput):
    teacher_id: str = Field(min_length=1,max_length=120)
    idempotency_key: str = Field(min_length=1,max_length=160)


class AnswerInput(RevisionInput):
    draft_item_id: str
    answer: str = Field(min_length=1,max_length=4000)
    process: str = Field(default='',max_length=12000)
    hint_level: int = Field(default=0,ge=0,le=3)


class SelfPracticeInput(Input):
    snapshot_hash: str
    answer: str = Field(min_length=1,max_length=4000)
    process: str = Field(default='',max_length=12000)
    hint_level: int = Field(default=0,ge=0,le=3)


class DecisionInput(Input):
    decision: dict


class CandidateInput(Input):
    proposal: dict


class RelationshipReviewInput(Input):
    expected_snapshot_hash: str
    review_status: str
    reviewer_id: str = Field(min_length=1,max_length=120)
    reason: str = Field(min_length=1,max_length=4000)


class PracticeRegistrationInput(Input):
    items: list[dict] = Field(min_length=1,max_length=20)
    reviewer_id: str = Field(min_length=1,max_length=120)
    expected_snapshot_hash: str


class PracticeReviewInput(Input):
    approved: bool
    authorized_use: bool
    reviewer_id: str = Field(min_length=1,max_length=120)
    expected_snapshot_hash: str


class SourcePassageInput(Input):
    source_id: str
    passage_id: str = Field(min_length=1,max_length=120)
    exact_text: str = Field(min_length=1,max_length=6000)
    physical_page: int = Field(ge=1)
    printed_page: str | None = Field(default=None,max_length=60)
    resource_title: str = Field(min_length=1,max_length=160)
    edition: str = Field(min_length=1,max_length=120)
    node_ids: list[str] = Field(min_length=1,max_length=20)
    reviewer_id: str = Field(min_length=1,max_length=120)
    expected_snapshot_hash: str


class APIError(Exception):
    def __init__(self,code,status=None):
        self.code=code
        self.status=status or (403 if code=='ACCESS_DENIED' else
            404 if code.endswith('NOT_FOUND') or code=='EVIDENCE_NOT_FOUND' else
            409 if 'STALE' in code or code=='IDEMPOTENCY_CONFLICT' else 400)


@dataclass
class Session:
    identifier: str
    role: str
    student_id: str | None
    runtime: DemoRuntime
    created_at: float = field(default_factory=time)
    lock: RLock = field(default_factory=RLock)
    external_snapshot: str | None = None
    external_refs: set = field(default_factory=set)


class ScopedProvider:
    """Final transport guard, independent of any model-supplied tool arguments."""
    def __init__(self,provider,session):
        self.provider,self.session=provider,session

    @property
    def configured(self):
        return self.provider.configured

    @property
    def mode(self):
        return self.provider.mode

    def call(self,purpose,payload,**settings):
        context=self.session
        runtime=context.runtime
        formal = not runtime.kb.is_self_created
        if formal:
            if context.external_snapshot!=runtime.snapshot_hash or not context.external_refs:
                raise ValueError('EXTERNAL_DATA_NOT_AUTHORIZED')
            if purpose=='PLAN':
                requested = set(payload.get('available',{}).get('record_ids',[]))
                rows=[row for row in runtime.kb.snapshot['records']
                      if row['record_id'] in context.external_refs & requested]
                if not rows:
                    raise ValueError('EXTERNAL_SCOPE_NOT_AUTHORIZED')
                payload={**payload,'goal':'复核已授权的模拟作答，给出有依据的教学建议。',
                    'selected_student':rows[0]['student_id'] if rows else None,
                    'prepared_draft_request':None,
                    'available':{'record_ids':[row['record_id'] for row in rows],
                    'student_refs':sorted({row['student_id'] for row in rows}),
                    'question_ids':sorted({row['question_id'] for row in rows}),
                    'knowledge_refs':sorted({ref for row in rows for ref in row['knowledge_refs']}),
                    'snapshot_hash':runtime.snapshot_hash}}
            elif payload.get('record_id') not in context.external_refs:
                raise ValueError('EXTERNAL_SCOPE_NOT_AUTHORIZED')
        allowed = ({'goal','available','tools','skills','selected_student','max_steps','prepared_draft_request'}
                   if purpose=='PLAN' else
                   {'record_id','question_id','question','student_answer','student_process','allowed_evidence_refs',
                    'allowed_question_ids','reference_answer','rubric_points','knowledge_nodes','source_passages',
                    'locked_initial','mathematics','path_a','path_b','path_a_hash','path_b_hash'})
        outgoing=clean_dto({key:item for key,item in payload.items() if key in allowed})
        if formal and 'allowed_question_ids' in outgoing:
            outgoing['allowed_question_ids'] = [outgoing['question_id']]
        skill = payload.get('skill_context')
        if isinstance(skill,dict):
            metadata = {key:skill[key] for key in ('skill_id','skill_version','skill_document_hash') if key in skill}
            if formal:
                evidence = set(outgoing.get('allowed_evidence_refs', []))
                metadata['knowledge_evidence_refs'] = [
                    ref for ref in skill.get('knowledge_evidence_refs', []) if ref in evidence
                ]
                row = next((row for row in runtime.kb.snapshot['records']
                            if row['record_id'] == outgoing.get('record_id')), None)
                if row:
                    metadata['scope_knowledge_refs'] = [
                        ref for ref in skill.get('scope_knowledge_refs', []) if ref in row['knowledge_refs']
                    ]
            else:
                metadata = clean_dto(skill)
            outgoing['skill_context'] = metadata
        if not formal and 'diagnostic_goal' in payload:
            outgoing['diagnostic_goal'] = payload['diagnostic_goal']
        return self.provider.call(purpose,outgoing,**settings)


def safe_code(error):
    text=str(error).split('：',1)[0]
    return text if re.fullmatch('[A-Z][A-Z0-9_]{1,78}',text) else 'INVALID_ARGUMENT'


def create_app(bundle=None, storage_root=None, web_dist=None, public_mode=False):
    application=FastAPI(title='智析学伴 HTTP bridge',version='1')
    application.add_middleware(CORSMiddleware,allow_origins=['http://localhost:5173','http://127.0.0.1:5173'],
        allow_credentials=True,allow_methods=['GET','POST'],allow_headers=['Content-Type'])
    sessions={}
    sessions_lock=RLock()
    jobs=JobStore()
    session_cookie = 'zhixi_demo_session' if public_mode else 'zhixi_formal_session'
    default_directory = 'outputs/http_public_runtime' if public_mode else 'outputs/full_demo_runtime'
    directory = Path(storage_root) if storage_root is not None else ROOT / default_directory
    loaded=bundle
    dist=Path(web_dist) if web_dist else ROOT/'web/dist'
    application.state.sessions=sessions
    application.state.jobs=jobs

    def envelope(data=None,context=None,error=None,limitations=None,trace_id=None,snapshot_hash=None):
        return {'ok':error is None,'data':data,'error':error,'trace_id':trace_id or 'HTTP-'+uuid4().hex[:16],
            'snapshot_hash':context.runtime.snapshot_hash if context else snapshot_hash,
            'limitations':[DEMO_LIMITATION,*(limitations or [])]}

    def fail(code,status=None):
        raise APIError(code,status)

    def context(request:Request):
        identifier=request.cookies.get(session_cookie)
        with sessions_lock:
            value=sessions.get(identifier)
        if value is None or time()-value.created_at>8*3600:
            fail('SESSION_REQUIRED',401)
        return value

    def teacher(value):
        if value.role!='TEACHER':
            fail('ACCESS_DENIED')

    def requests_model(arguments):
        count = arguments.get('max_model_calls', 0)
        if type(count) is not int or count < 0:
            fail('SCHEMA_INVALID',422)
        return count > 0

    def selected(value,student_id):
        if value.role=='STUDENT':
            if student_id not in (None,value.student_id):
                fail('ACCESS_DENIED')
            return value.student_id
        if student_id and student_id not in value.runtime.bundle['dataset']['students']:
            fail('EVIDENCE_NOT_FOUND')
        return student_id

    def execute(value,operation,*,read_only=False):
        with value.lock:
            try:
                scope = value.runtime.kb.read_scope() if read_only else nullcontext()
                with scope:
                    return operation()
            except APIError:
                raise
            except (ValueError,KeyError,TypeError) as error:
                fail(safe_code(error))

    def business(value,response,*,agent=False,skill=False):
        data=response.get('data')
        if agent:
            data={key:response.get(key) for key in ('plan','steps','claims','gate','result')}
        limits=[*response.get('limitations',[]),*response.get('warnings',[])]
        if skill and not response.get('model_metadata',{}).get('skill_document_hash'):
            limits.append('LEGACY_SKILL_CONTRACT')
        result=envelope(clean_dto(data,value.role),value,error=response.get('error'),limitations=limits,
                        trace_id=response.get('trace_id'))
        if not response.get('ok') and result['error'] is None:
            result['ok']=False
            result['error']={'code':'COMMAND_BLOCKED','message':'当前证据未通过输出核验。','retryable':False}
        gate=data.get('gate') if isinstance(data,dict) else None
        if gate:
            result['gate']=gate
            if gate['status']=='GATE_BLOCKED':
                result['ok']=False
                result['error']={'code':'GATE_BLOCKED','message':'当前结论未通过核验。','retryable':False}
        return result

    def command(value,operation,asynchronous=False):
        if not asynchronous:
            return execute(value,operation)
        accepted_snapshot = execute(value, lambda: value.runtime.snapshot_hash)

        def run():
            try:
                def validate_and_run():
                    if value.runtime.snapshot_hash != accepted_snapshot:
                        fail('STALE_SNAPSHOT')
                    return operation()
                return execute(value,validate_and_run)
            except APIError as error:
                return envelope(error={'code':error.code,'message':error.code,'retryable':False},
                                snapshot_hash=accepted_snapshot)

        try:
            job = jobs.submit(value.identifier,run)
        except ValueError as error:
            fail(safe_code(error), 429)
        return JSONResponse(envelope(job, snapshot_hash=accepted_snapshot), status_code=202)

    @application.exception_handler(APIError)
    async def api_error(request,error):
        return JSONResponse(envelope(error={'code':error.code,'message':error.code,'retryable':False}),status_code=error.status)

    @application.exception_handler(RequestValidationError)
    async def validation_error(request,error):
        return JSONResponse(envelope(error={'code':'SCHEMA_INVALID','message':'请求字段或类型不符合接口要求。','retryable':False}),status_code=422)

    @application.get('/api/health')
    def health():
        return envelope({'status':'READY','app_version':'http-v1','production_auth':False,'public_mode':public_mode})

    @application.post('/api/session')
    def create_session(body:SessionInput):
        nonlocal loaded
        if body.role not in {'TEACHER','STUDENT'}:
            fail('ACCESS_DENIED')
        with sessions_lock:
            if loaded is None:
                loaded=build_bundle(ROOT/'demo_data/math_workflow' if public_mode else DEFAULT_DATA)
            if body.role=='STUDENT' and body.student_id not in loaded['dataset']['students']:
                fail('ACCESS_DENIED')
            identifier=uuid4().hex
            runtime=DemoRuntime(loaded,storage_root=directory)
            value=Session(identifier,body.role,body.student_id if body.role=='STUDENT' else None,runtime)
            runtime.provider=ScopedProvider(runtime.provider,value)
            sessions[identifier]=value
        response=JSONResponse(envelope({'role':value.role,'student_id':value.student_id,
            'production_auth':False,'session_mode':'DEMO_ROLE_SWITCH'},value))
        response.set_cookie(session_cookie,identifier,httponly=True,samesite='lax',max_age=8*3600)
        return response

    @application.get('/api/context')
    def get_context(value:Session=Depends(context)):
        def operation():
            state=value.runtime.state()
            students=[{'id':identifier,'label':identifier,'class_id':row['模拟班级编号']}
                for identifier,row in value.runtime.bundle['dataset']['students'].items()
                if value.role=='TEACHER' or identifier==value.student_id]
            nodes=value.runtime.bundle['dataset']['nodes']
            node_options = []
            for identity, row in nodes.items():
                description = row.get('说明','')
                label = re.split('[：:·]',description,maxsplit=1)[0].strip() or identity
                node_options.append({'id':identity,'label':label,'date':row.get('模拟测验日期'),
                                     'description':description})
            return envelope({'app_version':'http-v1','data_origin':'SELF_CREATED' if value.runtime.kb.is_self_created else 'AUTHORIZED_OFFICIAL',
                'dataset_label':'自建数学演示' if value.runtime.kb.is_self_created else '正式模拟学情资料',
                'snapshot_hash':state['snapshot_hash'],'model_configured':state['model_configured'],
                'role':value.role,'student_id':value.student_id,'nodes':node_options,
                'classes':[{'id':identity,'label':identity} for identity in sorted({row['class_id'] for row in students})],
                'students':students,'registry':state['registry'],'capabilities':{'production_auth':False,
                'teacher_review':value.role=='TEACHER','model_jobs':True,'server_file_import':False,
                'skill_contract_status':'VERSIONED_SKILLS' if hasattr(value.runtime,'skill_registry') else 'LEGACY_WORKFLOW'},
                'warnings':state['limitations']},value)
        return execute(value,operation,read_only=True)

    @application.get('/api/views/{view}')
    def view(view:str,node_id:str|None=None,student_id:str|None=None,class_id:str|None=None,
             record_id:str|None=None,value:Session=Depends(context)):
        student=selected(value,student_id)
        if view in {'profile','learning'} and student is None:
            student=next(iter(value.runtime.bundle['dataset']['students']),None)
        return execute(value,lambda:envelope(build_view(value.runtime,view,value.role,student,node_id,class_id,record_id),value),read_only=True)

    @application.get('/api/evidence/{entity_id}')
    def evidence(entity_id:str,value:Session=Depends(context)):
        def operation():
            entity=value.runtime.kb.get(entity_id,value.role,student_id=value.student_id)
            if value.role=='STUDENT' and entity.get('type') in {'RUBRIC_POINT','REFERENCE_ANSWER'}:
                fail('ACCESS_DENIED')
            return envelope(clean_dto(entity,value.role),value)
        return execute(value,operation,read_only=True)

    @application.get('/api/responses/{record_id}')
    def response(record_id:str,value:Session=Depends(context)):
        return execute(value,lambda:envelope(response_view(value.runtime,record_id,value.role,value.student_id),value),read_only=True)

    @application.get('/api/knowledge/relations')
    def course_relations(question_id:str|None=None,value:Session=Depends(context)):
        teacher(value)
        def operation():
            knowledge = value.runtime.kb
            data = knowledge.course_relationships(question_id)
            data['reviews'] = knowledge.course_relationship_reviews()
            data['q_matrix'] = knowledge.export_q_matrix()
            return envelope(clean_dto(data),value)
        return execute(value,operation,read_only=True)

    @application.post('/api/knowledge/relations/{relation_id}/reviews')
    def review_course_relation(relation_id:str,body:RelationshipReviewInput,value:Session=Depends(context)):
        teacher(value)
        return execute(value,lambda:envelope(value.runtime.kb.review_course_relationship(
            relation_id,**body.model_dump()),value))

    @application.get('/api/knowledge/practice')
    def practice_catalog(value:Session=Depends(context)):
        teacher(value)
        def operation():
            knowledge = value.runtime.kb
            reviews = knowledge.practice_review_events
            ids = list(dict.fromkeys(row['practice_id'] for row in reversed(reviews)))
            return envelope(clean_dto({
                'items':[knowledge.get_practice_for_review(identity) for identity in ids],
                'reviews':reviews,
            }),value)
        return execute(value,operation,read_only=True)

    @application.post('/api/knowledge/practice')
    def register_practice(body:PracticeRegistrationInput,value:Session=Depends(context)):
        teacher(value)
        def operation():
            result = value.runtime.kb.import_practice_items(**body.model_dump(),approved=False)
            return envelope(result,value)
        return execute(value,operation)

    @application.post('/api/knowledge/practice/{practice_id}/review')
    def review_practice(practice_id:str,body:PracticeReviewInput,value:Session=Depends(context)):
        teacher(value)
        def operation():
            result = value.runtime.kb.review_practice(practice_id,**body.model_dump())
            return envelope(result,value)
        return execute(value,operation)

    @application.get('/api/knowledge/sources')
    def knowledge_sources(value:Session=Depends(context)):
        teacher(value)
        def operation():
            knowledge = value.runtime.kb
            sources = [] if public_mode else [
                {'id':path.name,'title':path.stem,'sha256':digest}
                for path,digest in knowledge._verified_resources
            ]
            passages = [item for item in knowledge.evidence.values() if item['type']=='SOURCE_PASSAGE']
            return envelope({'sources':sources, 'passages':clean_dto(passages)},value)
        return execute(value,operation,read_only=True)

    @application.post('/api/knowledge/sources/passages')
    def import_source_passage(body:SourcePassageInput,value:Session=Depends(context)):
        teacher(value)
        if public_mode:
            fail('ACCESS_DENIED')
        def operation():
            knowledge = value.runtime.kb
            source = next(((path,digest) for path,digest in knowledge._verified_resources
                           if path.name==body.source_id), None)
            if source is None:
                fail('SOURCE_NOT_APPROVED',404)
            path, digest = source
            payload = body.model_dump(exclude={'source_id','reviewer_id','expected_snapshot_hash'})
            payload.update(source_path=str(path),source_hash=digest,external_authorized=False)
            result = knowledge.import_passages([payload],reviewer_id=body.reviewer_id,
                expected_snapshot_hash=body.expected_snapshot_hash,review_context='AUTOMATED')
            return envelope(clean_dto(result),value)
        return execute(value,operation)

    @application.post('/api/tools/{tool_id}')
    def tool(tool_id:str,body:ToolInput,value:Session=Depends(context)):
        student=selected(value,body.student_id)
        if value.role=='STUDENT' and tool_id=='get_evidence_item':
            entity=value.runtime.kb.get(body.arguments.get('entity_id'),value.role,student_id=student)
            if entity.get('type') in {'RUBRIC_POINT','REFERENCE_ANSWER'}:
                fail('ACCESS_DENIED')
        asynchronous = tool_id == 'analyze_responses' and requests_model(body.arguments)
        if asynchronous:
            teacher(value)
        return command(value,lambda:business(value,value.runtime.call_tool(tool_id,body.arguments,value.role,student)),
                       asynchronous)

    @application.post('/api/skills/{skill_id}')
    def skill(skill_id:str,body:SkillInput,value:Session=Depends(context)):
        student=selected(value,body.student_id)
        if body.request is None:
            arguments = body.arguments or {}
            asynchronous = requests_model(arguments)
            if asynchronous:
                teacher(value)
            return command(value,lambda:business(value,value.runtime.call_skill(skill_id,arguments,value.role,student),skill=True),
                           asynchronous)
        if body.arguments is not None or body.request.get('skill_id') != skill_id:
            fail('SCHEMA_INVALID',422)
        strict = getattr(value.runtime,'call_skill_request',None)
        if not callable(strict):
            fail('SKILL_CONTRACT_NOT_AVAILABLE',503)
        constraints = body.request.get('constraints',{})
        if not isinstance(constraints,dict):
            fail('SCHEMA_INVALID',422)
        asynchronous = requests_model(constraints)
        if asynchronous:
            teacher(value)
        def run_skill():
            result = strict(body.request,value.role,student)
            data = {**(result.get('data') or {}),
                    'skill_status': result['status'], 'skill_id': skill_id,
                    'skill_version': result.get('model_metadata',{}).get('skill_version'),
                    'model_metadata': result.get('model_metadata',{}),
                    'evidence_refs': result.get('evidence_refs',[])}
            projected = {**result, 'data':data, 'ok':result['status']!='FAILED'}
            return business(value,projected)
        return command(value,run_skill,asynchronous)

    @application.post('/api/agent')
    def agent(body:AgentInput,value:Session=Depends(context)):
        student=selected(value,body.student_id)
        if body.use_model:
            teacher(value)
        if value.role=='STUDENT' and any(identifier in body.goal for identifier in value.runtime.bundle['dataset']['students'] if identifier!=student):
            fail('ACCESS_DENIED')
        return command(value,lambda:business(value,value.runtime.run_request(
            body.goal,value.role,student,body.use_model,node_id=body.node_id),agent=True),body.use_model)

    @application.post('/api/external-authorization')
    def authorize(body:AuthorizationInput,value:Session=Depends(context)):
        teacher(value)
        def operation():
            runtime=value.runtime
            if body.snapshot_hash!=runtime.snapshot_hash:
                fail('STALE_SNAPSHOT')
            rows={row['record_id']:row for row in runtime.kb.snapshot['records']}
            refs=set(body.response_refs)
            if not refs<=set(rows):
                fail('EVIDENCE_NOT_FOUND')
            if not runtime.kb.is_self_created and any(identifier!='DR0023' or rows[identifier]['student_id']!='D01' or rows[identifier]['node_id']!='N02' for identifier in refs):
                fail('EXTERNAL_SCOPE_NOT_AUTHORIZED',403)
            if body.confirmed and not refs:
                fail('MISSING_REQUIRED_INPUT')
            value.external_snapshot=body.snapshot_hash if body.confirmed else None
            value.external_refs=refs if body.confirmed else set()
            runtime.grant_external_authorization(body.snapshot_hash,body.confirmed)
            return envelope({'snapshot_hash':body.snapshot_hash,'confirmed':body.confirmed,'response_refs':sorted(value.external_refs)},value)
        return execute(value,operation)

    @application.get('/api/jobs/{job_id}')
    def job(job_id:str,value:Session=Depends(context)):
        try:
            progress = jobs.get(job_id,value.identifier)
        except ValueError as error:
            fail(safe_code(error))
        result = progress.get('result') or {}
        return envelope(progress, snapshot_hash=result.get('snapshot_hash'))

    @application.get('/api/assignments')
    def assignments(value:Session=Depends(context)):
        def operation():
            drafts=value.runtime.list_drafts(value.student_id if value.role=='STUDENT' else None)
            return envelope({'assignments':[assignment_dto(item,value.role,value.student_id) for item in drafts
                if value.role=='TEACHER' or item['status'] in {'SUBMITTED_FOR_REVIEW','EXPORTED'}]},value)
        return execute(value,operation,read_only=True)

    def group_candidates(value, body):
        runtime = value.runtime
        if (body.node_id not in runtime.bundle['dataset']['nodes']
                or not set(body.indicator_ids) <= set(runtime.kb.nodes)):
            fail('INVALID_GROUP_SCOPE')
        if body.class_id not in {row['模拟班级编号'] for row in runtime.bundle['dataset']['students'].values()}:
            fail('INVALID_GROUP_SCOPE')
        return suggest_complementary_groups(runtime.kb.snapshot,class_id=body.class_id,
            node_id=body.node_id,indicator_ids=body.indicator_ids)

    @application.post('/api/assignments/complementary-groups')
    def complementary_groups(body:ComplementaryGroupInput,value:Session=Depends(context)):
        teacher(value)
        return execute(value,lambda:envelope(group_candidates(value,body),value),read_only=True)

    @application.post('/api/assignments/complementary-groups/{candidate_id}/draft')
    def complementary_draft(candidate_id:str,body:ComplementaryDraftInput,value:Session=Depends(context)):
        teacher(value)
        def operation():
            runtime = value.runtime
            with runtime.kb.read_scope():
                result = group_candidates(value,body)
            if result['snapshot_hash'] != body.expected_snapshot_hash:
                fail('STALE_SNAPSHOT')
            candidate = next((item for item in result['candidates'] if item['candidate_id']==candidate_id),None)
            if candidate is None:
                fail('GROUP_CANDIDATE_NOT_FOUND')
            draft = runtime.create_draft({
                'mode':'evidence_group','class_ref':body.class_id,'student_refs':candidate['student_ids'],
                'target_knowledge_refs':sorted({item['indicator_id'] for item in candidate['directions']}),
                'item_count':body.item_count,'max_minutes':body.max_minutes,
                'expected_snapshot_hash':body.expected_snapshot_hash,
                'group_candidate':candidate,
                'guidance':'围绕本次可比作答互相解释具体步骤，由教师核对对象与练习。',
            })
            return envelope(draft,value)
        return execute(value,operation)

    @application.post('/api/assignments')
    def create_assignment(body:CreateInput,value:Session=Depends(context)):
        teacher(value)
        def operation():
            if body.request.get('mode') == 'evidence_group':
                fail('GROUP_CANDIDATE_REQUIRED')
            if body.request.get('snapshot_hash',value.runtime.snapshot_hash)!=value.runtime.snapshot_hash:
                fail('STALE_SNAPSHOT')
            return envelope(value.runtime.create_draft(body.request),value)
        return execute(value,operation)

    @application.get('/api/assignments/{draft_id}')
    def get_assignment(draft_id:str,revision:int|None=None,value:Session=Depends(context)):
        return execute(value,lambda:envelope(assignment_dto(value.runtime.get_draft(draft_id,revision),value.role,value.student_id),value),read_only=True)

    @application.post('/api/assignments/{draft_id}/revisions')
    def edit_assignment(draft_id:str,body:EditInput,value:Session=Depends(context)):
        teacher(value)
        return execute(value,lambda:envelope(value.runtime.edit_draft(draft_id,body.revision,body.changes),value))

    @application.post('/api/assignments/{draft_id}/validate')
    def validate_assignment(draft_id:str,body:RevisionInput,value:Session=Depends(context)):
        teacher(value)
        return execute(value,lambda:envelope(value.runtime.validate_draft(draft_id,body.revision),value))

    @application.post('/api/assignments/{draft_id}/confirm')
    def confirm_assignment(draft_id:str,body:ConfirmInput,value:Session=Depends(context)):
        teacher(value)
        return execute(value,lambda:envelope(value.runtime.confirm_draft(draft_id,body.revision,body.teacher_id,body.idempotency_key),value))

    @application.post('/api/assignments/{draft_id}/export')
    def export_assignment(draft_id:str,body:RevisionInput,value:Session=Depends(context)):
        teacher(value)
        def operation():
            artifact=value.runtime.export_draft(draft_id,body.revision)
            return Response(artifact['content'],media_type='application/zip',
                headers={'Content-Disposition':f'attachment; filename="{artifact["filename"]}"'})
        return execute(value,operation)

    @application.post('/api/assignments/{draft_id}/answers')
    def answer_assignment(draft_id:str,body:AnswerInput,value:Session=Depends(context)):
        if value.role!='STUDENT':
            fail('ACCESS_DENIED')
        return execute(value,lambda:envelope(value.runtime.submit_assignment_answer(draft_id,body.revision,
            body.draft_item_id,value.student_id,body.answer,body.process,body.hint_level),value))

    @application.get('/api/answers')
    def answers(value:Session=Depends(context)):
        return execute(value,lambda:envelope({'answers':clean_dto(value.runtime.list_assignment_answers(
            value.student_id if value.role=='STUDENT' else None))},value),read_only=True)

    @application.post('/api/practice/{practice_id}/answers')
    def self_practice(practice_id:str,body:SelfPracticeInput,value:Session=Depends(context)):
        if value.role != 'STUDENT':
            fail('ACCESS_DENIED')
        return execute(value,lambda:envelope(value.runtime.assignments.submit_self_practice(
            practice_id,value.student_id,body.snapshot_hash,body.answer,body.process,body.hint_level),value))

    @application.get('/api/governance')
    def governance(value:Session=Depends(context)):
        teacher(value)
        return execute(value,lambda:envelope(governance_view(value.runtime),value),read_only=True)

    @application.post('/api/governance/decisions')
    def decision(body:DecisionInput,value:Session=Depends(context)):
        teacher(value)
        return execute(value,lambda:envelope(value.runtime.kb.teacher_decision(body.decision),value))

    @application.post('/api/governance/candidates')
    def candidate(body:CandidateInput,value:Session=Depends(context)):
        teacher(value)
        return execute(value,lambda:envelope(value.runtime.kb.add_candidate(body.proposal),value))

    @application.api_route('/{path:path}',methods=['GET','HEAD'],include_in_schema=False)
    def static(path:str):
        if path=='api' or path.startswith('api/'):
            fail('ENDPOINT_NOT_FOUND',404)
        filename=(dist/path).resolve()
        if not filename.is_relative_to(dist.resolve()):
            fail('ACCESS_DENIED')
        if filename.is_file():
            return FileResponse(filename,media_type=WEB_ASSET_MEDIA_TYPES.get(filename.suffix.lower()))
        if path.startswith('assets/'):
            fail('ASSET_NOT_FOUND',404)
        if (dist/'index.html').is_file():
            return FileResponse(dist/'index.html')
        fail('FRONTEND_NOT_BUILT',404)

    return application


app=create_app(public_mode=os.environ.get('PUBLIC_DEMO_MODE','').lower() in {'1','true','yes'})

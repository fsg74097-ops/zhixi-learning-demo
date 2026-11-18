import { useEffect, useState } from 'react';
import { ArrowRight, BookOpen, Fingerprint, Sparkles } from 'lucide-react';
import { api, ApiError } from './api.ts';
import { EvidenceDrawer } from './components/EvidenceDrawer.tsx';
import { Shell } from './components/Shell.tsx';
import { Badge, Button, Dialog, ErrorNotice, Loading } from './components/ui.tsx';
import { AgentRun } from './features/AgentRun.tsx';
import { AssignmentStudio } from './features/AssignmentStudio.tsx';
import { ComplementaryGroups } from './features/ComplementaryGroups.tsx';
import { Diagnosis } from './features/Diagnosis.tsx';
import { Governance } from './features/Governance.tsx';
import { KnowledgeMap } from './features/KnowledgeMap.tsx';
import { KnowledgeLibrary } from './features/KnowledgeLibrary.tsx';
import { Classroom, Overview, Profile } from './features/Overview.tsx';
import { StudentFeedback, StudentLearning, StudentPractice } from './features/Student.tsx';
import type { FeatureProps } from './features/types.ts';
import { useView } from './hooks.ts';
import type { Context, Role, Scope } from './models.ts';
import { evidenceIds } from './presentation.ts';

const teacherPages = {
  dashboard: ['学情总览', '优先关注本轮发现，从具体作答找到下一步教学行动。', Overview],
  classes: ['班级分析', '按相同题目和评分口径对照班级表现。', Classroom],
  profile: ['学生画像', '整合作答证据、观察边界和针对性学习动作。', Profile],
  diagnosis: ['逐题诊断', '从原始作答定位具体操作，核对数学依据。', Diagnosis],
  knowledge: ['知识星图', '浏览知识节点、当前观察和可用练习。', KnowledgeMap],
  assignments: ['任务中心', '围绕真实题源编辑练习，由教师核对并确认。', AssignmentStudio],
  agent: ['Agent 中心', '在限定范围内调度工具、核对证据并形成教学建议。', AgentRun],
  governance: ['数据治理', '查看有效数据从哪里来，以及每一次变更的影响。', Governance],
} as const;
const studentPages = {
  learning: ['我的学习', '从最近一次作答出发，找到你现在可以做的一件事。', StudentLearning],
  knowledge: ['我的知识探索', '看看这轮练习涉及哪些知识，找到下一步练习。', KnowledgeMap],
  diagnosis: ['逐题讲解', '回看自己的思路，找到可以检查的一步。', Diagnosis],
  practice: ['练习任务', '先独立尝试，需要时逐层查看提示。', StudentPractice],
  feedback: ['自检反馈', '回看自己的练习结果和可以立刻做的检查。', StudentFeedback],
} as const;

function readRoute() {
  const [path, query] = window.location.hash.slice(1).split('?');
  const [, , page, entity] = path.split('/');
  const params = new URLSearchParams(query);
  let decoded: string | undefined;
  try { decoded = entity ? decodeURIComponent(entity) : undefined; } catch { decoded = undefined; }
  return { page, entity: decoded, student: params.get('student'), node: params.get('node') };
}

function scopeFor(context: Context, previous?: Scope, student?: string | null): Scope {
  const classId = context.classes.some(item => item.id === previous?.class_id) ? previous?.class_id ?? '' : '';
  const students = context.students.filter(item => !classId || item.class_id === classId);
  const chooseStudent = student ?? previous?.student_id;
  return {
    class_id: context.role === 'TEACHER' ? classId : '',
    node_id: context.nodes.some(item => item.id === previous?.node_id) ? previous?.node_id ?? '' : context.nodes.find(item => item.id === 'N02')?.id ?? context.nodes[0]?.id ?? '',
    student_id: context.student_id ?? students.find(item => item.id === chooseStudent)?.id ?? students[0]?.id ?? '',
  };
}

function Workspace({page, entity, apiScope, revision, ...props}: Omit<FeatureProps, 'data'> & { page:string; apiScope:Scope; revision:number }) {
  const teacher = props.context.role === 'TEACHER';
  const pages = teacher ? teacherPages : studentPages;
  const [title, description, Feature] = pages[page as keyof typeof pages] ?? (teacher ? teacherPages.dashboard : studentPages.learning);
  const { data, error, loading } = useView(page, apiScope, revision, page === 'diagnosis' ? entity : undefined);
  const evidenceId = evidenceIds(data?.evidence_refs)[0];
  const nodeLabel = props.context.nodes.find(item => item.id === props.scope.node_id)?.label;
  return (
    <main className={`page page-${page} ${teacher ? '' : 'student-page'}`} id="main">
      <div className="page-header">
        <div className="page-heading">
          <div className="page-crumb">
            <span>{teacher ? '教师工作空间' : '我的学习空间'}</span>
            <span aria-hidden="true">/</span><span>{props.context.dataset_label}</span>
          </div>
          <h1>{title}</h1>
          <p>{description}</p>
        </div>
        {page === 'dashboard' && <div className="page-actions">
          {evidenceId && <Button onClick={() => props.onEvidence(evidenceId)}><BookOpen size={15}/>查看本轮依据</Button>}
          <Button variant="primary" onClick={() => props.onNavigate('agent')}><Sparkles size={15}/>分析本轮</Button>
        </div>}
        {teacher && page === 'knowledge' && data && !loading && !error &&
          <div className="page-actions"><KnowledgeLibrary nodes={data.nodes??[]} onRefresh={props.onRefresh}/></div>}
        {teacher && page === 'assignments' && data && !loading && !error && <div className="page-actions">
          <ComplementaryGroups context={props.context} scope={props.scope} indicators={data.knowledge_nodes??[]}
            onNavigate={props.onNavigate} onEvidence={props.onEvidence} notify={props.notify}/>
        </div>}
      </div>
      <div className="page-body">
        {nodeLabel && page !== 'governance' && <div className="scope-line">
          <Fingerprint size={14}/>
          <strong>{nodeLabel}</strong>
          {teacher && ['dashboard', 'classes'].includes(page) && <span>{props.scope.class_id || '所有班级'}</span>}
          {['profile','diagnosis','learning'].includes(page) && <span>{props.scope.student_id}</span>}
        </div>}
        {loading ? <Loading/> : error ? <ErrorNotice error={error} onRetry={props.onRefresh}/> : data && <Feature {...props} data={data} entity={entity}/>}
      </div>
    </main>
  );
}

export function App() {
  const [context, setContext] = useState<Context>();
  const [scope, setScope] = useState<Scope>();
  const [route, setRoute] = useState(readRoute);
  const [error, setError] = useState<unknown>();
  const [switching, setSwitching] = useState(false);
  const [selectStudent, setSelectStudent] = useState(false);
  const [student, setStudent] = useState('');
  const [revision, setRevision] = useState(0);
  const [evidence, setEvidence] = useState<string | null>(null);
  const [toast, setToast] = useState('');
  const [refreshing, setRefreshing] = useState(false);

  function navigate(target: string, role = context?.role ?? 'TEACHER') {
    const hash = `#/${role === 'TEACHER' ? 'teacher' : 'student'}/${target}`;
    if (location.hash !== hash) location.hash = hash;
    setRoute(readRoute());
    setEvidence(null);
  }
  async function load(role?: Role, studentId?: string) {
    setSwitching(true);
    setError(undefined);
    try {
      if (role) await api.post('/session', { role, ...(studentId ? { student_id: studentId } : {}) });
      let next: Context;
      try {
        next = await api.get('/context');
      } catch (failure) {
        if (role || !(failure instanceof ApiError) || failure.code !== 'AUTH_REQUIRED' && !/401|SESSION/.test(failure.code)) throw failure;
        await api.post('/session', { role: 'TEACHER' });
        next = await api.get('/context');
      }
      setContext(next);
      setScope(scopeFor(next, scope, readRoute().student));
      setRevision(value => value + 1);
      if (role && context && context.role !== role) navigate(role === 'TEACHER' ? 'dashboard' : 'learning', role);
      setSelectStudent(false);
    } catch (failure) {
      setError(failure);
    } finally {
      setSwitching(false);
    }
  }
  useEffect(() => { void load(); }, []);
  useEffect(() => {
    const changed = () => {
      setRoute(readRoute());
      setEvidence(null);
      window.scrollTo({ top: 0 });
    };
    window.addEventListener('hashchange', changed);
    return () => window.removeEventListener('hashchange', changed);
  }, []);
  useEffect(() => {
    if (!toast) return;
    const timer = window.setTimeout(() => setToast(''), 5500);
    return () => window.clearTimeout(timer);
  }, [toast]);

  async function refresh() {
    if (refreshing) return;
    setRefreshing(true);
    setError(undefined);
    try {
      const next = await api.get<Context>('/context');
      setContext(next);
      setScope(current => scopeFor(next, current));
      setRevision(value => value + 1);
    } catch (failure) {
      setError(failure);
    } finally {
      setRefreshing(false);
    }
  }
  function changeRole(role: Role) {
    if (switching || role === context?.role) return;
    if (role === 'STUDENT') {
      setStudent(scope?.student_id ?? context?.students[0]?.id ?? '');
      setSelectStudent(true);
    } else {
      void load(role);
    }
  }
  function changeScope(next: Scope) {
    if (!context) return;
    setScope(scopeFor(context, next));
    if (route.entity || route.student) navigate(page);
  }

  if (!context || !scope) return <main className="startup stack"><Badge tone="info">智析学伴</Badge><h1>正在打开学习工作空间</h1>{error ? <ErrorNotice error={error} onRetry={() => void load('TEACHER')}/> : <Loading/>}</main>;
  const teacher = context.role === 'TEACHER';
  const pages = teacher ? teacherPages : studentPages;
  const page = route.page && Object.hasOwn(pages, route.page) ? route.page : teacher ? 'dashboard' : 'learning';
  const selectedScope = teacher && (route.student || route.node) ? scopeFor(context, {...scope,node_id:route.node || scope.node_id}, route.student) : scope;
  const focused = ['profile','diagnosis','assignments','agent'].includes(page);
  const apiScope = {
    ...selectedScope,
    student_id: teacher && !focused ? '' : selectedScope.student_id,
  };
  return <>
    <Shell context={context} scope={selectedScope} page={page} onScope={changeScope} onRole={changeRole} onNavigate={navigate} onRefresh={() => void refresh()}>
      {error != null && <div className="page-global-error"><ErrorNotice error={error} onRetry={() => void refresh()}/></div>}
      {switching ? <div className="page"><Loading/></div> :
        <Workspace key={`${context.role}:${context.snapshot_hash}:${page}:${JSON.stringify(apiScope)}`} page={page} entity={route.entity} apiScope={apiScope} context={context} scope={selectedScope} revision={revision} onEvidence={setEvidence} onNavigate={navigate} onRefresh={() => void refresh()} notify={setToast}/>}
    </Shell>
    <EvidenceDrawer key={evidence ?? 'closed'} id={evidence} onClose={() => setEvidence(null)}/>
    <Dialog title="选择学生演示视角" open={selectStudent} onClose={() => !switching && setSelectStudent(false)} description="进入后，页面仅使用这位学生对应的作答、练习和反馈。">
      <div className="stack modal-form">
        <label className="field">演示学生<select value={student} onChange={event => setStudent(event.target.value)}>{context.students.map(item => <option key={item.id} value={item.id}>{item.label}</option>)}</select></label>
        {error != null && <ErrorNotice error={error}/>}
        <Button variant="primary" disabled={switching || !student} onClick={() => void load('STUDENT', student)}>进入学习空间 <ArrowRight size={15}/></Button>
      </div>
    </Dialog>
    {toast && <div className="toast" role="status">{toast}<Button variant="quiet small" aria-label="关闭提示" onClick={() => setToast('')}>关闭</Button></div>}
  </>;
}

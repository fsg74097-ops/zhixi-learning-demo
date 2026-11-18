import { useState } from 'react';
import { ArrowRight, BookOpen, Check, Compass, Lightbulb, Send } from 'lucide-react';
import { api } from '../api.ts';
import { ClaimCard } from '../components/Findings.tsx';
import { Badge, Button, Card, Details, Empty, ErrorNotice, Status, Tabs } from '../components/ui.tsx';
import type { Answer, Draft, DraftItem, PracticeItem } from '../models.ts';
import { formatScore } from '../presentation.ts';
import type { FeatureProps } from './types.ts';

const confirmed = (draft?: Draft) => draft && ['SUBMITTED_FOR_REVIEW', 'EXPORTED'].includes(draft.status);
const practiceId = (item: PracticeItem) => item.practice_id ?? item.question_id ?? item.id ?? '';

export function StudentLearning({ data, onEvidence, onNavigate }: FeatureProps) {
  const task = data.assignments?.find(confirmed);
  const observations = data.findings?.slice(0, 2) ?? [];
  const metric = data.metrics?.[0];
  return <>
    <Card className="student-hero">
      <div className="stack">
        <Badge tone="info"><Compass size={13}/>当前学习任务</Badge>
        <div><h2>{task ? '从一题开始，写下你的思路。' : '回看作答，找到下一步。'}</h2>
          <p>{task ? `老师已确认 ${task.items.length} 道练习。你可以先自己做，卡住时再打开提示。` : '先看看本轮作答里有哪些值得继续练习的地方。'}</p></div>
        <div><Button variant="primary" onClick={() => onNavigate(task ? `practice/${encodeURIComponent(task.draft_id)}` : 'diagnosis')}>{task ? '开始练习' : '回看我的作答'} <ArrowRight size={15}/></Button></div>
      </div>
      <div className="student-hero-symbol" aria-hidden="true"><Compass size={43} strokeWidth={1.2}/></div>
    </Card>
    <div className="split">
      <div className="stack"><h2>我的学习发现</h2>{observations.length ? observations.map(finding => <ClaimCard key={finding.id} finding={finding} onEvidence={onEvidence}/>) : <Card><Empty title="本轮还没有可查看的学习发现"/></Card>}</div>
      <div className="stack">
        <Card className="card-pad stack"><h2>这轮的记录</h2><div className="row spread"><span>有效得分 / 满分</span><strong className="number">{formatScore(metric?.numerator, metric?.denominator)}</strong></div><div className="row spread"><span>本轮作答记录</span><strong>{data.records?.length ?? 0}</strong></div></Card>
        <Card className="card-pad stack"><h2>继续探索</h2><Button onClick={() => onNavigate('knowledge')}><BookOpen size={15}/>查看我的知识探索 <ArrowRight size={14}/></Button><Button onClick={() => onNavigate('feedback')}>回看自检反馈 <ArrowRight size={14}/></Button></Card>
      </div>
    </div>
  </>;
}

function FeedbackResult({ answer }: { answer: Answer }) {
  const feedback = answer.feedback;
  const validity = feedback.answer_validity;
  const valid = validity === 'VALID';
  return <section className={`feedback-box ${valid ? 'valid' : validity === 'INVALID' ? 'invalid' : ''} stack`} aria-label="本次自检结果">
    <div className="row spread"><h3>{valid ? '这次的答案通过了核验' : validity === 'INVALID' ? '再检查这一步' : '这次作答需要进一步核对'}</h3><Status value={validity}/></div>
    {feedback.next_action && <p>{feedback.next_action}</p>}
    {(feedback.steps ?? []).length > 0 && <Details title="展开过程核对">
      <div className="stack">{feedback.steps?.map((step, index) => <div className="step-row" key={index}>
        <span className="step-number">{step.step}</span><span>{step.source} → {step.target}</span><Status value={step.local_validity}/>
      </div>)}</div>
    </Details>}
    {feedback.limitations?.map(limitation => <p className="muted" key={limitation}>{limitation}</p>)}
    <p className="mono muted">{answer.draft_id ? `${answer.draft_id} · r${answer.revision} · ${answer.draft_item_id}` : `自主练习 · ${answer.draft_item_id}`}</p>
  </section>;
}

function PracticeQuestion({ task, item, hints, snapshot, onNavigate }: { task?: Draft; item: DraftItem | PracticeItem; hints: string[]; snapshot: string; onNavigate: FeatureProps['onNavigate'] }) {
  const [answer, setAnswer] = useState('');
  const [process, setProcess] = useState('');
  const [level, setLevel] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>();
  const [result, setResult] = useState<Answer>();
  async function submit() {
    if (busy || !answer.trim()) return;
    setBusy(true);
    setError(undefined);
    try {
      const response = {answer:answer.trim(), process:process.trim(), hint_level:level};
      const created = task && 'draft_item_id' in item
        ? await api.post<Answer>(`/assignments/${encodeURIComponent(task.draft_id)}/answers`, {...response,revision:task.revision,draft_item_id:item.draft_item_id})
        : await api.post<Answer>(`/practice/${encodeURIComponent(practiceId(item as PracticeItem))}/answers`, {...response,snapshot_hash:snapshot});
      setResult(created);
    } catch (failure) {
      setError(failure);
    } finally {
      setBusy(false);
    }
  }
  return <div className="split">
    <Card className="card-pad stack">
      <div className="row spread"><h2>先独立试一试</h2><Badge tone={task ? 'success' : 'info'}><Check size={12}/>{task ? `教师确认 r${task.revision}` : '已授权自主练习'}</Badge></div>
      <div className="source-quote practice-prompt">{item.exact_text}</div>
      {task?.guidance && <p className="muted">{task.guidance}</p>}
      <form className="stack" onSubmit={event => { event.preventDefault(); void submit(); }}>
        <label className="field">我的答案<input required value={answer} onChange={event => setAnswer(event.target.value)} placeholder="写下最后的结果" maxLength={2000}/></label>
        <label className="field">我的解题过程<textarea value={process} onChange={event => setProcess(event.target.value)} rows={5} placeholder="可以每行写一步，并说明你做了什么。" maxLength={5000}/></label>
        <div><Button type="submit" variant="primary" disabled={busy || !answer.trim()}><Send size={14}/>{busy ? '正在核验…' : '提交自己的作答'}</Button></div>
      </form>
      {error != null && <ErrorNotice error={error}/>}
      {result && <FeedbackResult answer={result}/>}
    </Card>
    <div className="stack">
      <Card className="card-pad stack">
        <div className="row spread"><h2>分层提示</h2><Lightbulb size={20} color="var(--cyan)"/></div>
        <p className="muted">试过以后再看，看看能否用提示继续推理。</p>
        {hints.length ? <>
          <div className="hint-list">{hints.slice(0, level).map((hint,index) => <div className="hint" key={index}><strong>提示 {index + 1}</strong><p>{hint}</p></div>)}</div>
          {level < hints.length && <Button onClick={() => setLevel(current => current + 1)}>查看第 {level + 1} 层提示 <ArrowRight size={14}/></Button>}
        </> : <p className="muted">本题暂无专属提示。可以先写下已知条件和你打算做的第一步。</p>}
      </Card>
      <Button onClick={() => onNavigate('feedback')}>查看我的自检记录 <ArrowRight size={14}/></Button>
    </div>
  </div>;
}

export function StudentPractice({ data, context, entity, onNavigate }: FeatureProps) {
  const assignments = (data.assignments ?? []).filter(confirmed);
  const independent = (data.practice_items ?? []).filter(item=>practiceId(item));
  const linkPracticeId = entity?.startsWith('self:') ? entity.slice(5) : '';
  const [mode, setMode] = useState(assignments.length && !linkPracticeId ? 'assigned' : 'independent');
  const [draftId, setDraftId] = useState(entity ?? assignments[0]?.draft_id ?? '');
  const [itemId, setItemId] = useState('');
  const [selfId, setSelfId] = useState(linkPracticeId || practiceId(independent[0] ?? {exact_text:'',source_status:'',knowledge_refs:[]}));
  const task = assignments.find(draft => draft.draft_id === draftId) ?? assignments[0];
  const assignedItem = task?.items.find(item => item.draft_item_id === itemId) ?? task?.items[0];
  const selfItem = independent.find(item=>practiceId(item)===selfId) ?? independent[0];
  const item = mode==='assigned' ? assignedItem : selfItem;
  const hints = [...new Set(item?.knowledge_refs?.flatMap(ref => data.hints?.[ref] ?? []) ?? [])].slice(0,3);
  return <>
    <Card className="card-pad stack">
      <Tabs value={mode} onChange={setMode} tabs={[{id:'assigned',label:'教师确认练习'},{id:'independent',label:'自主练习'}]}/>
      {mode==='assigned' && (task && assignedItem ? <><label className="field">当前练习<select value={task.draft_id} onChange={event => {setDraftId(event.target.value); setItemId('');}}>
        {assignments.map(draft => <option key={draft.draft_id} value={draft.draft_id}>{draft.guidance?.slice(0, 45) || '教师确认练习'} · {draft.draft_id} · r{draft.revision}</option>)}
      </select></label>
      <Tabs value={assignedItem.draft_item_id} onChange={setItemId} tabs={task.items.map((entry,index) => ({id:entry.draft_item_id,label:`第 ${index + 1} 题`}))}/></>
        : <Empty title="当前还没有老师确认的练习" action={<Button onClick={() => setMode('independent')}>打开自主练习</Button>}/>)}
      {mode==='independent' && (selfItem ? <label className="field">选择已授权的练习<select aria-label="选择已授权的练习" value={practiceId(selfItem)} onChange={event=>setSelfId(event.target.value)}>{independent.map((item,index)=><option key={practiceId(item)} value={practiceId(item)}>{index + 1}. {item.exact_text}</option>)}</select></label>
        : <Empty title="当前没有可用的自主练习" action={<Button onClick={() => onNavigate('diagnosis')}>回看我的作答</Button>}/>)}
    </Card>
    {item && <PracticeQuestion key={'draft_item_id' in item ? `${task?.draft_id}:${task?.revision}:${item.draft_item_id}` : practiceId(item)} task={mode==='assigned' ? task : undefined} item={item} hints={hints} snapshot={context.snapshot_hash} onNavigate={onNavigate}/>}
  </>;
}

export function StudentFeedback({ data, onNavigate }: FeatureProps) {
  const [tab, setTab] = useState('latest');
  const attempts = [...(data.answers ?? [])].sort((left,right)=>(right.submitted_at??'').localeCompare(left.submitted_at??''));
  const displayed = tab === 'latest' ? attempts.slice(0, 1) : attempts;
  return <Card className="card-pad stack">
    <div className="row spread"><h2>我的作答记录</h2><Button onClick={() => onNavigate('practice')}>回到练习 <ArrowRight size={14}/></Button></div>
    <Tabs value={tab} onChange={setTab} tabs={[{id:'latest',label:'最近一次'}, {id:'all',label:`全部记录（${attempts.length}）`}]}/>
    {displayed.length ? displayed.map((answer,index) => <div className="stack" key={`${answer.draft_item_id}:${answer.submitted_at ?? index}`}>
      {answer.answer && <p>我的答案　{answer.answer}</p>}<FeedbackResult answer={answer}/>
    </div>) : <Empty title="还没有自检记录" description="完成并提交一道题后，可以在这里回看你的核验结果。"/>}
  </Card>;
}

import { useState } from 'react';
import { ArrowRight, Database, FileCheck2, ListFilter, ShieldCheck } from 'lucide-react';
import { api } from '../api.ts';
import { Badge, Button, Card, Details, Empty, ErrorNotice, JsonDetails, Tabs } from '../components/ui.tsx';
import { entries, object, text } from '../models.ts';
import type { RecordRow } from '../models.ts';
import { stateLabel } from '../presentation.ts';
import type { FeatureProps } from './types.ts';

const stages = [['raw_records', '原始输入'], ['current_events', '当前事件'], ['candidate_count', '评分候选'], ['teacher_decision_count', '教师决定'], ['scored_events', '有效评分']] as const;
const actions = [
  ['FLAG_FOR_LATER', '标记待复核'], ['EXCLUDE_SCOPE', '排除出得分统计'],
  ['REVERT_TO_OFFICIAL', '恢复官方原分'], ['ACCEPT_AI', '接受已验证的 AI 候选'],
  ['MODIFY_VALUE', '更正当前有效得分'],
] as const;
const score = (value: number | null | undefined) => value == null ? '未评估' : value;

function DecisionForm({record, data, context, onRefresh, notify}: FeatureProps & {record:RecordRow}) {
  const [action, setAction] = useState<string>('FLAG_FOR_LATER');
  const [reason, setReason] = useState('');
  const [actor, setActor] = useState('');
  const [approved, setApproved] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>();
  const initialPoints = object(record.effective_point_scores ?? record.official_point_scores);
  const [points, setPoints] = useState<Record<string, string>>(() => Object.fromEntries(Object.entries(initialPoints).map(([id,value]) => [id,text(value,'')])));
  const candidate = (data.candidates ?? []).map(object).find(item => item.candidate_id === record.effective_candidate_id);
  const canSubmit = approved && actor.trim() && reason.trim() && (action !== 'MODIFY_VALUE' || Object.keys(points).length > 0 && Object.values(points).every(value => /^\d+$/.test(value)));
  async function submit() {
    if (busy || !canSubmit) return;
    setBusy(true);
    setError(undefined);
    try {
      const effectivePoints = Object.fromEntries(Object.entries(points).map(([id,value]) => [id,Number(value)]));
      await api.post('/governance/decisions', { decision: {
        request_id:crypto.randomUUID(), record_id:record.record_id, source_revision:record.selected_revision,
        source_dataset_hash:data.dataset_hash, previous_snapshot_hash:data.snapshot_hash ?? context.snapshot_hash,
        teacher_action:action, decision_reason:reason.trim(), reviewer_id:actor.trim(),
        ...(action === 'EXCLUDE_SCOPE' ? {scopes:['include_in_score_stats']} : {}),
        ...(action === 'MODIFY_VALUE' ? {confirmed:true, effective_point_scores:effectivePoints, effective_score:Object.values(effectivePoints).reduce((total, point) => total + point, 0)} : {}),
      } });
      setApproved(false);
      setReason('');
      notify('教师决定已审计，正在刷新有效数据快照。');
      onRefresh();
    } catch (failure) { setError(failure); }
    finally { setBusy(false); }
  }
  return <Details title="记录教师决定">
    <form className="stack" onSubmit={event => {event.preventDefault(); void submit();}}>
      <label className="field">处理方式<select value={action} onChange={event => {setAction(event.target.value);setApproved(false);}}>{actions.map(([id,label]) => <option key={id} value={id} disabled={id === 'ACCEPT_AI' && candidate?.candidate_status !== 'VALIDATED' || id === 'MODIFY_VALUE' && !Object.keys(points).length}>{label}</option>)}</select></label>
      {action === 'MODIFY_VALUE' && <div className="form-grid">{Object.entries(points).map(([id,value]) => <label className="field" key={id}>评分点 {id}<input type="number" min={0} step={1} value={value} onChange={event => {setApproved(false);setPoints(current => ({...current,[id]:event.target.value}));}}/></label>)}</div>}
      <div className="form-grid"><label className="field">审核者标记<input required value={actor} onChange={event => setActor(event.target.value)}/></label><label className="field">决定依据<input required value={reason} onChange={event => setReason(event.target.value)}/></label></div>
      <label className="check"><input type="checkbox" checked={approved} onChange={event => setApproved(event.target.checked)}/>我已核对本条原始记录、影响范围及所选决定。</label>
      {error != null && <ErrorNotice error={error}/>}
      <div><Button variant="primary" type="submit" disabled={busy || !canSubmit}><FileCheck2 size={14}/>{busy ? '正在保存…' : '提交教师决定'}</Button></div>
    </form>
  </Details>;
}

export function Governance(props: FeatureProps) {
  const {data, context, onEvidence, onNavigate} = props;
  const [tab, setTab] = useState('records');
  const [selected, setSelected] = useState('');
  const [query, setQuery] = useState('');
  const [onlyIssues, setOnlyIssues] = useState(false);
  const records = data.records ?? [];
  const issues = data.unresolved_issues ?? [];
  const candidates = data.candidates ?? [];
  const decisions = data.decisions ?? [];
  const sources = Object.entries(object(data.source_files));
  const filtered = records.filter(row => (!onlyIssues || row.status !== '已评分' || row.flagged_for_later || !row.include_in_score_stats)
    && [row.record_id,row.student_id,row.question_id,row.status].some(value => value.toLowerCase().includes(query.trim().toLowerCase())));
  const record = filtered.find(item => item.record_id === selected) ?? filtered[0];
  return <>
    <Card className="card-pad governance-head">
      <div className="row spread"><div className="stack"><div className="row"><Badge tone="info"><Database size={12}/>MODEL_READY</Badge><Badge>{context.dataset_label}</Badge></div><h2>数据如何进入有效口径</h2><p className="muted">从原件、当前事件到有效值，保留修订、候选判断和教师决定。</p></div><div className="snapshot-card"><ShieldCheck size={21}/><div><strong>当前快照</strong><p className="mono">{(data.snapshot_hash ?? context.snapshot_hash).slice(0,16)}</p></div></div></div>
    </Card>
    <section className="quality-pipeline card" aria-label="数据状态">{stages.map(([key,label]) => <div key={key} className="pipeline-stage"><strong>{label}</strong><p>{text(data.summary?.[key], '未提供')}</p></div>)}</section>
    {issues.length > 0 && <div className="banner" role="status">有 {issues.length} 条质量问题待核对。到「质量与候选」查看原始记录与可用范围。</div>}
    <Card>
      <div className="card-pad" style={{paddingBottom:0}}><Tabs value={tab} onChange={setTab} tabs={[{id:'records',label:'原始与有效对照'},{id:'quality',label:'质量与候选'},{id:'audit',label:'审计与来源'}]}/></div>
      {tab === 'records' && <div className="card-pad split" style={{paddingTop:0}}>
        <div className="stack">
          <div className="row"><ListFilter size={16}/><input type="search" aria-label="筛选治理记录" className="input" placeholder="搜索作答、学生、题目或状态" value={query} onChange={event => setQuery(event.target.value)}/><label className="check"><input type="checkbox" checked={onlyIssues} onChange={event => setOnlyIssues(event.target.checked)}/>只显示待处理与排除的记录</label></div>
          {filtered.length ? <div className="table-wrap governance-table"><table className="data-table"><thead><tr><th>记录 / 学生</th><th>状态</th><th>有效值</th></tr></thead><tbody>
            {filtered.map(row => <tr key={row.record_id} className={row.record_id === record?.record_id ? 'selected-row' : ''}><td><button className="evidence-link" onClick={() => setSelected(row.record_id)}>{row.record_id}</button><p className="muted">{row.student_id}</p></td><td>{row.status}</td><td>{score(row.effective_score)}</td></tr>)}
          </tbody></table></div> : <Empty title="当前没有匹配的记录"/>}
        </div>
        {record && <div className="stack">
          <div><div className="row"><Badge>{record.status}</Badge><span className="mono muted">{record.record_id} · r{record.selected_revision}</span></div><h3 style={{marginTop:8}}>本条记录的分数轨迹</h3></div>
          <dl className="score-tracks"><div><dt>官方原分</dt><dd>{score(record.official_score)}</dd></div><div><dt>AI 候选</dt><dd>{score(record.candidate_score)}</dd></div><div><dt>当前有效</dt><dd>{score(record.effective_score)}</dd></div></dl>
          <p className="muted">有效值来源 {stateLabel(record.effective_score_source)} · {record.include_in_score_stats ? '计入当前得分统计' : '当前不计入得分统计'}</p>
          <div className="row"><Button onClick={() => onEvidence(record.record_id)}>查看原始记录</Button><Button onClick={() => onNavigate(`diagnosis/${encodeURIComponent(record.record_id)}?student=${encodeURIComponent(record.student_id)}&node=${encodeURIComponent(text(record.node_id,''))}`)}>定位具体作答 <ArrowRight size={13}/></Button></div>
          <DecisionForm key={record.record_id} {...props} record={record}/>
          <JsonDetails value={record} title="查看事件、修订与影响范围"/>
        </div>}
      </div>}
      {tab === 'quality' && <div className="card-pad stack" style={{paddingTop:0}}>
        <div className="row"><Badge tone="warning">待评分 {text(data.summary?.pending_events)}</Badge><Badge>未作答 {text(data.summary?.unanswered_events)}</Badge><Badge tone="ai">语义核对 {stateLabel(text(data.summary?.semantic_status))}</Badge></div>
        <Details title={`待核对事项 · ${issues.length} 条`} open>{issues.length ? <ul className="plain-list">{issues.map((value,index) => {const issue=object(value); return <li key={index}><strong>{text(issue.code??issue.type??issue.issue,'数据质量事项')}</strong><p className="muted">{text(issue.message??issue.reason??issue.detail??value,'展开结构化记录查看位置及影响。')}</p><JsonDetails value={value} title="查看本条详情"/></li>;})}</ul> : <p className="muted">当前没有未解决的质量问题。</p>}</Details>
        <Details title={`评分候选与验证 · ${candidates.length} 条`}>{candidates.length ? <ul className="plain-list">{candidates.map((value,index) => {const item=object(value); return <li key={index}><strong>{text(item.record_id,'候选')}</strong> · {stateLabel(text(item.candidate_status))}<JsonDetails value={value} title="查看候选与分层校验"/></li>;})}</ul> : <p className="muted">当前快照没有已登记的评分候选。</p>}</Details>
      </div>}
      {tab === 'audit' && <div className="card-pad stack" style={{paddingTop:0}}>
        <Details title={`教师决定时间线 · ${decisions.length} 条`} open>{decisions.length ? <ul className="plain-list">{decisions.slice().reverse().map((value,index) => {const item=object(value); return <li key={index}><strong>{text(item.record_id)} · {actions.find(([key]) => key === item.teacher_action)?.[1] ?? text(item.teacher_action)}</strong><p>{text(item.decision_reason,'')}</p><p className="muted">{text(item.reviewer_id)} · {text(item.reviewed_at)}</p><JsonDetails value={value} title="查看决定与前后快照"/></li>;})}</ul> : <p className="muted">当前快照没有教师决定。</p>}</Details>
        <Details title={`来源清单与文件哈希 · ${sources.length} 份`}><div className="table-wrap"><table className="data-table"><thead><tr><th>来源</th><th>行数</th><th>SHA-256</th></tr></thead><tbody>{sources.map(([key,value]) => <tr key={key}><td>{key}</td><td>{text(object(value).rows)}</td><td className="mono">{text(object(value).sha256)}</td></tr>)}</tbody></table></div></Details>
        <JsonDetails value={{snapshot:data.snapshot_hash,dataset:data.dataset_hash,rules:data.rules_version}} title="批次与版本标识"/>
        <JsonDetails value={data.history} title="原始事件及修订历史"/>
      </div>}
    </Card>
  </>;
}

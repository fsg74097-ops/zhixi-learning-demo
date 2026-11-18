import { useState } from 'react';
import { Search, Users } from 'lucide-react';
import { api } from '../api.ts';
import { Badge, Button, Details, Dialog, ErrorNotice } from '../components/ui.tsx';
import type { Choice, Context, Draft, Scope } from '../models.ts';
import { formatRate, formatScore } from '../presentation.ts';
import type { FeatureProps } from './types.ts';

interface Direction {
  indicator_id:string; explain_student_id:string; review_student_id:string;
  explain_score:number; review_score:number; max_score:number;
  common_question_count:number; coverage:number;
  question_evidence:Array<{question_id:string;explain_record_id:string;review_record_id:string}>;
}
interface Group {
  candidate_id:string; student_ids:string[]; directions:Direction[];
}
interface Comparison {
  status:string; snapshot_hash:string; scoped_student_count:number;
  eligible_candidate_count:number; candidates:Group[];
}

const endpoint = '/assignments/complementary-groups';
const noGroups: Record<string,string> = {
  NO_COMPLEMENTARY_PAIRS:'当前范围没有满足双向互助条件的组合。',
  INSUFFICIENT_COMPARABLE_EVIDENCE:'当前范围的同题同评分证据不足，暂无法提出互助组合。',
};

export function ComplementaryGroups({context,scope,indicators,onEvidence,onNavigate,notify}:{
  context:Context;scope:Scope;indicators:Choice[];
  onEvidence:FeatureProps['onEvidence'];onNavigate:FeatureProps['onNavigate'];notify:FeatureProps['notify'];
}) {
  const [open,setOpen] = useState(false);
  const [classId,setClassId] = useState(scope.class_id);
  const [selected,setSelected] = useState<string[]>([]);
  const [result,setResult] = useState<Comparison>();
  const [pending,setPending] = useState(false);
  const [error,setError] = useState<unknown>();
  const request = {class_id:classId,node_id:scope.node_id,indicator_ids:selected};

  function clear() {
    setResult(undefined);
    setError(undefined);
  }
  async function compare() {
    setPending(true);
    clear();
    try {
      setResult(await api.post<Comparison>(endpoint,request));
    } catch (failure) {
      setError(failure);
    } finally {
      setPending(false);
    }
  }
  async function draft(group:Group) {
    if (!result) return;
    setPending(true);
    setError(undefined);
    try {
      const revision = await api.post<Draft>(endpoint + '/' + encodeURIComponent(group.candidate_id) + '/draft',{
        ...request, expected_snapshot_hash:result.snapshot_hash, item_count:3, max_minutes:15,
      });
      setOpen(false);
      notify('已根据共同作答创建互助练习草稿，等待教师核对。');
      onNavigate('assignments/' + encodeURIComponent(revision.draft_id));
    } catch (failure) {
      setError(failure);
    } finally {
      setPending(false);
    }
  }
  const studentLabel = (id:string) => context.students.find(student => student.id === id)?.label ?? id;
  const indicatorLabel = (id:string) => indicators.find(indicator => indicator.id === id)?.label ?? id;

  return <>
    <Button onClick={() => setOpen(true)}><Users size={15}/>临时互助小组</Button>
    <Dialog title="临时互助小组" description="按同班、同测验、同题及同一评分口径比较。请根据证据决定是否创建一份练习草稿。" open={open} onClose={() => !pending && setOpen(false)}>
      <div className="knowledge-library stack">
        <label className="field">互助班级<select aria-label="互助班级" value={classId} disabled={pending} onChange={event => {setClassId(event.target.value);clear();}}>
          <option value="">选择一个班级</option>
          {context.classes.map(item => <option key={item.id} value={item.id}>{item.label}</option>)}
        </select></label>
        <label className="field">互助知识指标<select aria-label="互助知识指标" multiple value={selected} disabled={pending} onChange={event => {
          setSelected(Array.from(event.target.selectedOptions, option => option.value));
          clear();
        }}>{indicators.map(item => <option key={item.id} value={item.id}>{item.label}</option>)}</select>
          <span className="field-hint">按 Ctrl 选择至少两项。系统只使用共同完成且评分有效的题目。</span>
        </label>
        <div><Button variant="primary" disabled={!classId || selected.length < 2 || selected.length > 20 || pending} onClick={() => void compare()}>
          <Search size={14}/>{pending ? '正在核对…' : '查找有证据的互助搭档'}
        </Button></div>
        {error != null && <ErrorNotice error={error}/>}
        {result && <section className="stack" data-group-result={result.status} aria-live="polite">
          <div className="row spread"><h3>本次证据对照</h3><Badge>范围内 {result.scoped_student_count} 位学生</Badge></div>
          {result.candidates.length === 0 ? <div className="banner">{noGroups[result.status] ?? '当前范围暂时没有可用的互助组合。'}</div> :
            <ul className="plain-list">{result.candidates.map(group => <li key={group.candidate_id} className="stack">
              <div className="row spread"><h3>{group.student_ids.map(studentLabel).join(' 与 ')}</h3>
                <Button variant="small" disabled={pending} onClick={() => void draft(group)}>创建待审核草稿</Button></div>
              {group.directions.map(direction => <Details title={`${studentLabel(direction.explain_student_id)} 可向 ${studentLabel(direction.review_student_id)} 讲解 ${indicatorLabel(direction.indicator_id)}`} key={direction.indicator_id}>
                <div className="stack">
                  <p>{direction.common_question_count} 道共同评分题 · 覆盖 {formatRate(direction.coverage)} · 有效得分 {formatScore(direction.explain_score,direction.max_score)} 与 {formatScore(direction.review_score,direction.max_score)}</p>
                  {direction.question_evidence.map(item => <div className="row" key={item.question_id}>
                    <span className="mono">{item.question_id}</span>
                    <button className="evidence-link" onClick={() => onEvidence(item.explain_record_id)}>查看讲解人作答</button>
                    <button className="evidence-link" onClick={() => onEvidence(item.review_record_id)}>查看练习人作答</button>
                  </div>)}
                </div>
              </Details>)}
            </li>)}</ul>}
        </section>}
      </div>
    </Dialog>
  </>;
}

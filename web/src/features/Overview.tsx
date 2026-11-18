import { useState } from 'react';
import { ArrowRight, ChartColumn, Users } from 'lucide-react';
import { ApiError, emitUiEvent } from '../api.ts';
import { useCommand } from '../hooks.ts';
import { entries, object, text } from '../models.ts';
import type { Metric } from '../models.ts';
import type { FeatureProps } from './types.ts';
import { Badge, Button, Card, Empty, ErrorNotice, Metrics, Tabs } from '../components/ui.tsx';
import { Findings, NextActions } from '../components/Findings.tsx';
import { evidenceIds, formatRate, formatScore } from '../presentation.ts';

function CohortMetrics({data}: {data:FeatureProps['data']}) {
  const aggregate = data.metrics?.[0];
  const counts = [
    {id:'students',label:'有评分观察的学生',value:data.observed_students},
    {id:'records',label:'当前测验的作答记录',value:data.records_count},
    {id:'coverage',label:'有效评分覆盖',value:aggregate ? formatRate(aggregate.coverage) : '未观察',n:aggregate?.n,excluded_count:aggregate?.excluded_count},
  ];
  return <Metrics items={[...(aggregate ? [{...aggregate,denominator_definition:aggregate.denominator_definition ?? '仅纳入当前有效评分事件'}] : []), ...counts]}/>;
}

export function Classroom({data,context,scope,onEvidence,onNavigate}:FeatureProps) {
  const [tab,setTab]=useState('indicators');
  const [students,setStudents]=useState<string[]>([]);
  const [otherAssessment,setOtherAssessment]=useState('');
  const command=useCommand(`${context.snapshot_hash}:${scope.node_id}:${scope.class_id}:${students.join('|')}`);
  const across=useCommand(`${context.snapshot_hash}:${scope.node_id}:${scope.class_id}:${otherAssessment}`);
  const result=object(tab==='assessment'?across.result:command.result);
  const choosingComparison=tab!=='indicators';
  const metrics=choosingComparison ? entries(result.metrics) as Metric[] : data.indicator_metrics ?? [];
  const observed=data.observed_students ?? 0;
  async function compare() {
    try {
      await command.run('/tools/compute_stats',{arguments:{
        operation:'compare_students',
        filters:{student_refs:students,...(scope.node_id ? {node_ids:[scope.node_id]} : {}),...(scope.class_id ? {class_refs:[scope.class_id]} : {})},
        denominator_policy:'scored_only',comparison_basis:'同节点同题及评分标准',
      }});
    } catch { /* The request error is shown under the comparison controls. */ }
  }
  async function compareAssessments() {
    try {
      await across.run('/tools/compute_stats',{arguments:{
        operation:'compare_assessments',
        comparison_basis:'SAME_STUDENT_QUESTION_RUBRIC',
        denominator_policy:'scored_only',
        filters:{node_ids:[scope.node_id,otherAssessment],...(scope.class_id ? {class_refs:[scope.class_id]} : {})},
      }});
    } catch { /* The request error is shown under the comparison controls. */ }
  }
  return <>
    <CohortMetrics data={data}/>
    {observed < 5 && <div className="banner">当前仅 {observed} 位学生有有效评分观察。班级共性分组结论按小样本规则抑制。</div>}
    <Card className="card-pad stack"><div className="row spread"><h2>按同一观察范围比较</h2><Badge tone="info">当前有效得分</Badge></div>
      <Tabs value={tab} onChange={setTab} tabs={[{id:'indicators',label:'同指标分布'},{id:'compare',label:'同题对照'},{id:'assessment',label:'跨测验对照'}]}/>
      {tab==='compare' && <div className="stack">
        <label className="field">选择至少两位学生<select multiple value={students} onChange={event=>setStudents(Array.from(event.target.selectedOptions,option=>option.value))}>{data.students?.map(student=><option key={student.id} value={student.id}>{student.label}</option>)}</select><span className="field-hint">按 Ctrl 可多选。服务只比较同测验中评分有效且共同完成的题目。</span></label>
        <div><Button variant="primary" disabled={students.length<2 || command.busy} onClick={() => void compare()}>{command.busy?'正在核对…':'比较共同作答'}</Button></div>
        {command.error instanceof ApiError && command.error.code==='COMPARISON_NOT_ESTABLISHED' ? <div className="banner">所选学生当前没有可按同一评分口径比较的共同作答。</div> : command.error != null && <ErrorNotice error={command.error}/>}
        {typeof result.paired_n==='number' && <p className="muted">本次对照纳入 {result.paired_n} 道共同题目。</p>}
      </div>}
      {tab==='assessment' && <div className="stack">
        <label className="field">与当前测验对照<select aria-label="对照测验" disabled={across.busy} value={otherAssessment} onChange={event=>{
          const selection = event.target.value;
          setOtherAssessment(selection);
          emitUiEvent(context, 'TIME_RANGE_CHANGED', {node_id: scope.node_id, comparison_node_id: selection || undefined});
        }}>
          <option value="">选择不同测验</option>
          {context.nodes.filter(node=>node.id!==scope.node_id).map(node=><option key={node.id} value={node.id}>{node.label}</option>)}
        </select><span className="field-hint">只纳入在两次测验中属于同一学生、同一题目且使用相同评分依据的作答。</span></label>
        <div><Button variant="primary" disabled={!otherAssessment||across.busy} onClick={()=>void compareAssessments()}>{across.busy?'正在核对…':'核对跨测验可比性'}</Button></div>
        {across.error instanceof ApiError && across.error.code==='COMPARISON_NOT_ESTABLISHED' ? <div className="banner">当前测验之间没有建立可比性，共同题目及评分依据未达到比较要求。</div> :
          across.error != null && <ErrorNotice error={across.error}/>}
        {typeof result.paired_n==='number' && <p className="muted">实际配对 {result.paired_n} 个学生—题目观察，覆盖 {formatRate(Number(object(result.pairing).coverage??0))}。{result.trend_conclusion_allowed===false?'配对数低于趋势判断要求。':''}</p>}
      </div>}
      {metrics.length > 0 ? <div className="table-wrap"><table className="data-table"><thead><tr><th>{tab==='compare'?'学生':tab==='assessment'?'测验':'知识指标'}</th><th>有效得分 / 满分</th><th>观察 n</th><th>覆盖率</th><th>得分率</th><th>证据</th></tr></thead><tbody>{metrics.map((metric,index)=>{
        const details=object(metric);
        const id=text(details.student_id??details.node_id??details.indicator_id,'');
        const name=tab==='compare' ? data.students?.find(student=>student.id===id)?.label :
          tab==='assessment' ? context.nodes.find(node=>node.id===id)?.label : metric.label;
        const ref=evidenceIds(metric.evidence_refs)[0];
        return <tr key={metric.id??(id||index)}><td><strong>{name??id}</strong></td><td className="number">{formatScore(metric.denominator ? metric.numerator : null,metric.denominator)}</td><td>{metric.n??'未观察'}</td><td>{formatRate(metric.coverage)}</td><td>{formatRate(metric.rate)}</td><td>{ref&&<button className="evidence-link" onClick={()=>onEvidence(ref)}>查看依据</button>}</td></tr>;
      })}</tbody></table></div> : tab!=='assessment' && <Empty title={tab==='compare'?'准备比较共同作答':'当前没有可用的指标分布'}/>}
    </Card>
    <div className="insight-grid"><Findings items={data.findings.slice(0,2)} onEvidence={onEvidence}/><NextActions actions={data.actions.filter(action=>action.target!=='classes')} onNavigate={onNavigate}/></div>
  </>;
}
export function Overview({data,onEvidence,onNavigate}:FeatureProps) {const [tab,setTab]=useState('students');return <><CohortMetrics data={data}/><div className="insight-grid"><Findings items={data.findings??[]} onEvidence={onEvidence}/><NextActions actions={data.actions??[]} onNavigate={onNavigate}/></div><Card><div className="card-pad" style={{paddingBottom:0}}><div className="card-header"><div><span className="eyebrow">OBSERVATION, NOT A LABEL</span><h2 style={{marginTop:5}}>从本轮表现开始</h2></div><Badge><Users size={11}/>当前可见范围</Badge></div><Tabs value={tab} onChange={setTab} tabs={[{id:'students',label:'学生与有效记录'},{id:'scope',label:'统计范围与可比性'}]}/></div>{tab==='students'?data.students?.length?<div className="table-wrap"><table className="data-table"><thead><tr><th>学生</th><th>有效得分 / 满分</th><th>已评分事件 n</th><th>覆盖率</th><th>本次得分率</th><th/></tr></thead><tbody>{data.students.map(student=><tr key={student.id}><td><strong>{student.label}</strong></td><td className="number">{formatScore(student.metric?.numerator,student.metric?.denominator)}</td><td className="number">{student.metric?.n??'未观察'}</td><td>{formatRate(student.metric?.coverage)}</td><td>{formatRate(student.metric?.rate)}</td><td><button className="evidence-link" onClick={()=>onNavigate(`profile?student=${encodeURIComponent(student.id)}`)}>查看画像 <ArrowRight size={12}/></button></td></tr>)}</tbody></table></div>:<Empty title="当前范围暂无学生记录"/>:<div className="card-pad stack"><div className="row"><ChartColumn size={19} color="var(--cyan)"/><h3>先明确比较对象，再解释差异</h3></div><p className="muted">分数只覆盖当前有效、已评分事件的满分和。不同学生题目覆盖不一致时，不据此建立能力排名。未观察与零分分开保留。</p>{data.limitations?.map((limitation,index)=><div className="banner" key={index}>{limitation}</div>)}<Button onClick={()=>onEvidence(evidenceIds(data.evidence_refs)[0])} disabled={!data.evidence_refs?.length}>查看当前依据</Button></div>}</Card></>;}
export function Profile(props:FeatureProps) {const {data,onEvidence,onNavigate}=props;return <><CohortMetrics data={data}/><div className="insight-grid"><Findings items={data.findings??[]} onEvidence={onEvidence}/><div className="stack"><NextActions actions={data.actions??[]} onNavigate={onNavigate}/><Card className="card-pad stack"><span className="eyebrow">EVIDENCE COVERAGE</span><h2>每个发现都保留边界</h2><p className="muted" style={{fontSize:13}}>当前证据不足以判断的知识，保持“未观察”。单题结果不被写成稳定掌握或永久标签。</p><Button onClick={()=>onNavigate('knowledge')}>查看知识覆盖 <ArrowRight size={13}/></Button></Card></div></div><Card className="card-pad"><div className="card-header"><h2>支撑本次发现的作答</h2><Badge>{data.records?.length??0} 条记录</Badge></div>{data.records?.length?<div className="stack">{data.records.slice(0,8).map(record=><div className="row spread" key={record.record_id} style={{borderBottom:'1px solid var(--border)',paddingBottom:12}}><div><strong style={{fontSize:13}}>{record.question_display??record.question_id}</strong><p className="muted" style={{fontSize:11}}>{record.status} · 当前有效分 {record.effective_score??'未评估'} · 原分 {record.official_score??'未评估'}</p></div><Button variant="small" onClick={()=>onNavigate(`diagnosis/${record.record_id}`)}>查看具体步骤</Button></div>)}</div>:<Empty title="本轮尚无可用作答"/>}</Card></>;}

import { useEffect, useState } from 'react';
import { ArrowRight, Check, CircleDashed, Cpu, Play, Search, ShieldCheck, Sparkles } from 'lucide-react';
import { api, viewPath } from '../api.ts';
import { useCommand } from '../hooks.ts';
import { entries, object, text } from '../models.ts';
import type { Finding, RecordRow, ViewModel } from '../models.ts';
import type { FeatureProps } from './types.ts';
import { ClaimCard } from '../components/Findings.tsx';
import { Badge, Busy, Button, Card, Details, Empty, ErrorNotice, JsonDetails } from '../components/ui.tsx';
import { evidenceIds, stateLabel } from '../presentation.ts';
export const toolLabels:Record<string,string>={query_learning_records:'读取选定作答',get_evidence_item:'核对来源原文',search_knowledge:'检索知识关联',compute_stats:'复算有效统计',find_practice_items:'核对授权练习',get_data_state:'检查数据快照',analyze_responses:'独立核查解题步骤',create_assignment_draft:'建立待审核草稿',build_learning_profile:'组织学习画像',recommend_learning_tasks:'形成针对性练习建议'};
export function AgentResult({result,onEvidence,onNavigate}:{result:unknown;onEvidence:(id:string)=>void;onNavigate?:(target:string)=>void}) {
  const root=object(result);
  const gate=object(root.gate);
  const execution=object(root.result);
  const tools=object(execution.tool_results);
  const draft=object(tools.create_assignment_draft??root.task_draft_ref);
  const draftId=text(draft.draft_id,'');
  const assertions=entries(gate.assertions).map(object);
  const diagnoses=entries(root.diagnoses??object(tools.analyze_responses).diagnoses).map(object);
  const rawClaims=assertions.length ? assertions : entries(root.claims).map(object);
  const findings:Finding[]=rawClaims.map((claim,index)=>({
    id:text(claim.assertion_id,`claim-${index}`),
    title:text(claim.assertion_text??claim.text,'本次核验结果'),
    description:text(claim.scope_note??claim.explanation,''),
    verification:text(claim.result??claim.verification,'unverifiable'),
    evidence_refs:evidenceIds(claim.evidence_refs),
    kind:text(claim.assertion_type,''),
  }));
  const steps=entries(root.steps??root.tool_steps).map(object);
  const limitations=[...new Set([...entries(root.limitations),...entries(root.warnings)].filter((value):value is string=>typeof value==='string'))];
  const summary=text(execution.summary,'');
  const model=object(root.model_metadata);
  const modelCalls=execution.model_calls??model.calls;
  const profile=object(tools.build_learning_profile);
  const taskPlan=object(tools.recommend_learning_tasks);
  const advice=[...entries(root.ai_actions_pending_review),...entries(profile.ai_actions_pending_review),...entries(taskPlan.ai_actions_pending_review)]
    .map(object).filter(item=>typeof item.assertion_text==='string'&&evidenceIds(item.evidence_refs).length>0);
  const recommended=entries(root.recommended_items??object(tools.recommend_learning_tasks).recommended_items).map(object);
  const rationale=entries(root.why??object(tools.recommend_learning_tasks).why).filter((value):value is string=>typeof value==='string');
  return <div className="stack">
    <div className="row spread"><h2>本次分析结果</h2><Badge tone={gate.status==='GATE_PASS'?'success':'warning'}><ShieldCheck size={12}/>{gate.status==='GATE_PASS'?'已通过证据核对':gate.status==='GATE_PARTIAL'?'部分内容仍需核对':'按证据状态呈现'}</Badge></div>
    {(summary||typeof modelCalls==='number')&&<Card className="card-pad row spread"><strong>{summary||text(root.skill_version,'分析摘要')}</strong><Badge tone="ai">实际模型调用 {text(modelCalls,'未提供')} 次</Badge></Card>}
    {draftId&&onNavigate&&<Card className="card-pad row spread"><div><h3>已生成待核对的练习草稿</h3><p className="mono muted">{draftId}</p></div><Button variant="primary" onClick={()=>onNavigate(`assignments/${encodeURIComponent(draftId)}`)}>打开本次草稿 <ArrowRight size={14}/></Button></Card>}
    {recommended.length>0&&<Card className="card-pad stack"><h3>下一步练习建议</h3>{rationale.map(item=><p key={item}>{item}</p>)}<ul className="plain-list">{recommended.map((item,index)=><li key={index}>{text(item.exact_text??item.title,'授权练习')}</li>)}</ul></Card>}
    {advice.length>0&&<Card className="card-pad stack">
      <div className="row spread"><h3>待教师核对的练习动作</h3><Badge tone="ai"><Sparkles size={12}/>模型候选</Badge></div>
      <ul className="plain-list">{advice.map((item,index)=><li className="stack" key={index}>
        <p>{text(item.assertion_text)}</p>
        <div className="row">{evidenceIds(item.evidence_refs).map(id=><button className="evidence-link" key={id} onClick={()=>onEvidence(id)}>查看依据 <span className="mono">{id}</span></button>)}</div>
      </li>)}</ul>
    </Card>}
    {findings.length ? <><ClaimCard finding={findings[0]} onEvidence={onEvidence}/>{findings.length>1&&<Details title={`更多核验结论 · ${findings.length-1} 条`}><div className="stack">{findings.slice(1).map(finding=><ClaimCard key={finding.id} finding={finding} onEvidence={onEvidence}/>)}</div></Details>}</> : <Card className="card-pad"><p className="muted">本次服务返回了执行摘要，暂未形成可直接展示的事实结论。</p></Card>}
    {steps.length>0&&<Card className="card-pad"><h3 style={{marginBottom:18}}>实际执行摘要</h3><ul className="timeline">{steps.map((step,index)=>{
      const response=object(step.response);const id=text(step.tool_id??step.tool,'');
      const failed=response.ok===false||step.status==='FAILED';
      return <li key={index}><span className="timeline-icon">{failed?<CircleDashed size={12}/>:<Check size={12}/>}</span><div><h3>{toolLabels[id]??(id||'受控工具')}</h3><p>{failed?text(object(response.error).message,'本步骤未取得完整结果。'):'结果已由服务返回。'}</p></div></li>;
    })}</ul></Card>}
    {diagnoses.length>0&&<Details title="双路诊断与数学核验摘要"><div className="stack">{diagnoses.map((diagnosis,index)=><div key={index}>
      <div className="row"><Badge>{stateLabel(text(diagnosis.model_status))}</Badge><span className="mono muted">{text(diagnosis.record_id??diagnosis.response_ref,'')}</span></div>
      <JsonDetails value={{mathematics:diagnosis.mathematics,path_a:diagnosis.path_a,path_b_initial:diagnosis.path_b_initial,path_b:diagnosis.path_b,arbitration:diagnosis.arbitration}} title="查看双方结果与适用范围"/>
    </div>)}</div></Details>}
    {limitations.length>0&&<Details title="本次结果的适用范围"><ul className="plain-list">{limitations.map(limitation=><li key={limitation}>{limitation}</li>)}</ul></Details>}
    <JsonDetails value={result} title="计划、工具返回与验证依据"/>
  </div>;
}
export function AgentRun({data,context,scope,onEvidence,onNavigate}:FeatureProps) {
  const [goal,setGoal]=useState('');const [useModel,setUseModel]=useState(false);const [permit,setPermit]=useState(false);const [records,setRecords]=useState<RecordRow[]>([]);const [selected,setSelected]=useState<string[]>([]);const [error,setError]=useState<unknown>();const [authorizing,setAuthorizing]=useState(false);const command=useCommand(`${context.snapshot_hash}:${scope.student_id}:${scope.node_id}`);
  useEffect(()=>{setPermit(false);setSelected([]);if(!useModel)return;const controller=new AbortController();api.get<ViewModel>(viewPath('diagnosis',scope),controller.signal).then(value=>setRecords(value.records??[])).catch(error=>{if(error.name!=='AbortError')setError(error);});return()=>controller.abort();},[useModel,context.snapshot_hash,scope.student_id,scope.node_id]);
  async function run(){if(authorizing||command.busy)return;setError(undefined);setAuthorizing(true);try{if(useModel)await api.post('/external-authorization',{snapshot_hash:context.snapshot_hash,confirmed:permit,response_refs:selected});await command.run('/agent',{goal:goal.trim(),student_id:scope.student_id,node_id:scope.node_id,use_model:useModel});}catch(failure){if(!(failure instanceof Error)||failure.name!=='AbortError')setError(failure);}finally{setAuthorizing(false);}}
  return <><div className="agent-workspace"><Card className="agent-hero stack"><div className="row spread"><Badge tone="ai"><Sparkles size={12}/>受控教学任务代理</Badge><span className="muted">{data.registry?.tools?.length ?? 0} 个工具 · {data.registry?.skills?.length ?? 0} 个 Skill</span></div><h2 style={{fontSize:25,maxWidth:600,lineHeight:1.5}}>从一个教学目标出发，<br/>把证据组织成下一步行动。</h2><p className="muted" style={{fontSize:13}}>说明想分析谁、观察什么、希望如何练习。Agent只使用注册工具，不会替教师批准任务。</p><label className="field">希望完成什么<textarea aria-label="教学分析目标" rows={5} maxLength={2000} value={goal} onChange={event=>setGoal(event.target.value)} placeholder={`例如：分析${scope.student_id||'当前学生'}本次不等式变向操作，核对原始步骤并准备针对性练习草稿。`}/></label><label className="check"><input type="checkbox" checked={useModel} onChange={event=>setUseModel(event.target.checked)} disabled={!context.model_configured}/>使用DeepSeek规划与独立诊断（只有点击执行才发起请求）</label>{useModel&&<div className="stack"><label className="field">本次允许分析的具体作答<select multiple aria-label="允许外发的作答" value={selected} onChange={event=>setSelected(Array.from(event.target.selectedOptions,option=>option.value))}>{records.map(record=><option key={record.record_id} value={record.record_id}>{record.question_display??record.question_id} · {record.record_id}</option>)}</select><span className="field-hint">服务端会再次限制许可范围。选择不代表获得额外授权。</span></label><label className="check"><input type="checkbox" checked={permit} onChange={event=>setPermit(event.target.checked)}/>允许发送所选作答与必要知识片段用于本次分析</label></div>}<div className="row spread"><span className="muted" style={{fontSize:11}}>显式执行 · 后端预算控制 · 证据出口核验</span><Button variant="primary" disabled={!goal.trim()||authorizing||command.busy||(useModel&&(!permit||!selected.length))} onClick={run}><Play size={13}/>执行分析目标</Button></div></Card><Card className="card-pad stack"><span className="eyebrow">HOW THE WORK GETS DONE</span><h2>看得见的分析路径</h2><ul className="timeline">{[['理解教学目标','限定学生、测验与当前快照'],['读取真实证据','作答、知识、评分点和可用来源'],['核对并组织结果','数学检查、受控工具与验证Gate'],['交给教师作决定','建议可改，草稿必须由教师确认']].map(([title,description],index)=><li key={title}><span className="timeline-icon">{index===0?<Search size={12}/>:index===3?<ShieldCheck size={12}/>:<Cpu size={12}/>}</span><div><h3>{title}</h3><p>{description}</p></div></li>)}</ul><Button onClick={()=>onNavigate('assignments')}>打开任务中心 <ArrowRight size={13}/></Button></Card></div>{data.skill_contract_status==='LEGACY_WORKFLOW'&&<div className="banner">当前Skill沿用已实现工作流；独立契约加载仍需接入，未标为完整协议实现。</div>}{(error||command.error)&&<ErrorNotice error={error??command.error}/>} {command.busy&&<Busy label={typeof command.progress?.progress==='string'?command.progress.progress:'正在执行已限定范围的教学分析'} cancel={command.cancel}/>} {command.result!=null&&<AgentResult result={command.result} onEvidence={onEvidence} onNavigate={onNavigate}/>}<Details title="可用工具与Skill"><div className="row">{entries(data.registry?.tools).map((value,index)=>{const item=object(value);const id=text(item.tool_id??item.id??item.name??value,'受控工具');return <Badge key={index}>{toolLabels[id]??id}</Badge>;})}{entries(data.registry?.skills).map((value,index)=>{const item=object(value);const id=text(item.skill_id??item.id??item.name??value,'Skill');return <Badge key={index} tone="ai">{toolLabels[id]??id}</Badge>;})}</div></Details></>;
}

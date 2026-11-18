import { useEffect, useState } from 'react';
import { ArrowRight, BookOpen, Check, ShieldCheck, Sparkles } from 'lucide-react';
import { api } from '../api.ts';
import { useCommand } from '../hooks.ts';
import { entries, object, text } from '../models.ts';
import type { ResponseDetail } from '../models.ts';
import type { FeatureProps } from './types.ts';
import { Badge, Busy, Button, Card, Details, Empty, ErrorNotice, JsonDetails, Status } from '../components/ui.tsx';
import { AgentResult } from './AgentRun.tsx';
import { evidenceIds, stateLabel } from '../presentation.ts';
type SkillId = 'build_learning_profile' | 'recommend_learning_tasks';
export function Diagnosis({data,context,scope,entity,onEvidence,onNavigate}:FeatureProps) {
  const records=data.records??[];const [selected,setSelected]=useState(entity??data.selected_record_id??records[0]?.record_id??'');const [response,setResponse]=useState<ResponseDetail|undefined>(data.response);const [error,setError]=useState<unknown>();const [authorizationError,setAuthorizationError]=useState<unknown>();const [authorizing,setAuthorizing]=useState(false);const [permit,setPermit]=useState(false);const command=useCommand(`${context.snapshot_hash}:${scope.student_id}:${selected}`);
  useEffect(()=>{setSelected(entity??data.selected_record_id??records[0]?.record_id??'');},[entity,data]);
  useEffect(()=>{
    setError(undefined);
    setAuthorizationError(undefined);
    setPermit(false);
    if (!selected) return;
    if (data.response?.record.record_id === selected) {setResponse(data.response); return;}
    const controller=new AbortController();
    setResponse(undefined);
    api.get<ResponseDetail>(`/responses/${encodeURIComponent(selected)}`,controller.signal).then(value=>{if(!controller.signal.aborted)setResponse(value);}).catch(error=>{if(!controller.signal.aborted)setError(error);});
    return()=>controller.abort();
  },[selected,data.response]);
  if(!records.length)return <Card><Empty title="当前范围还没有可逐题查看的作答" description="换一个测验范围，或先完成一次自己的练习。"/></Card>;
  const math=response?.mathematics;const question=object(response?.question);const rawSteps=response?.student_steps??response?.record.student_steps;
  async function analyze(skill?:SkillId) {
    if (authorizing || command.busy || !permit) return;
    setAuthorizationError(undefined);
    setAuthorizing(true);
    try {
      await api.post('/external-authorization',{snapshot_hash:context.snapshot_hash,confirmed:permit,response_refs:[selected]});
    } catch (failure) {
      setAuthorizationError(failure);
      return;
    } finally { setAuthorizing(false); }
    try {
      if (skill) {
        const identity=crypto.randomUUID();
        await command.run('/skills/'+skill,{student_id:scope.student_id,request:{
          schema_version:'1.1',request_id:'S-'+identity,trace_id:'RUN-'+identity,locale:'zh-CN',
          skill_id:skill,snapshot_hash:context.snapshot_hash,input_refs:[selected],
          goal:'核对当前获授权作答，说明具体数学操作并给出可执行的学习检查动作。',
          constraints:{
            student_refs:[scope.student_id],node_ids:[scope.node_id],
            target_knowledge_refs:response?.record.knowledge_refs??[],
            item_count:2,max_model_calls:4,
          },
        }});
      }
      else await command.run('/tools/analyze_responses',{student_id:scope.student_id,arguments:{response_refs:[selected],diagnostic_goal:'核对具体数学操作、首错、评分点与下一步建议',verification_mode:'A_AND_B',max_model_calls:4}});
    } catch { /* useCommand displays the bounded command failure. */ }
  }
  return <><Card className="card-pad"><div className="row spread"><label className="field" style={{flex:1,maxWidth:700}}>当前作答<select aria-label="选择具体作答" value={selected} onChange={event=>setSelected(event.target.value)}>{records.map(record=><option key={record.record_id} value={record.record_id}>{record.question_display??record.question_id} · {record.student_id}</option>)}</select></label><Badge><ShieldCheck size={12}/>原答保持只读</Badge></div></Card>{error!=null&&<ErrorNotice error={error}/>} {!response&&!error?<Busy label="正在读取这道题的原始过程"/>:response&&<><div className="split"><Card className="card-pad stack"><div className="row spread"><span className="eyebrow">THE ORIGINAL RESPONSE</span><Badge>{response.record.student_id}</Badge></div><h2 style={{lineHeight:1.7}}>{text(question.exact_text??question['题干或内容']??records.find(record=>record.record_id===selected)?.question_display,'原题内容尚未提供')}</h2><div><h3 style={{marginBottom:10}}>原始解题过程</h3><div className="diagnosis-original">{typeof rawSteps==='string'?rawSteps:entries(rawSteps).map((step,index)=><p key={index}>{text(object(step).text??object(step).exact_text??step)}</p>)}{!rawSteps&&'本次没有提供过程。'}</div></div><div className="row spread"><span className="muted">原始最终作答</span><strong>{text(response.record.student_answer,'本次未提供最终作答')}</strong></div></Card><div className="stack"><Card className="card-pad stack"><span className="eyebrow">WHERE TO LOOK NEXT</span><h2>先看具体操作</h2>{math?<><div className="row"><Status value={math.answer_validity}/>{typeof math.first_error_step==="number" && <Badge tone="warning">首处错误 · 第 {math.first_error_step} 步</Badge>}</div><p style={{fontSize:14,lineHeight:1.9}}>{math.next_action}</p>{math.steps?.map((step,index)=><div className={`step-row ${step.local_validity==='INVALID'?'invalid':''}`} key={index}><span className="step-number">{step.step}</span><div><div className="muted" style={{fontSize:11}}>{step.source}</div><strong style={{fontSize:14}}>{step.target}</strong></div>{step.local_validity==='VALID'?<Check size={15} color="var(--success)"/>:<Badge tone="warning">{step.local_validity==='INVALID'?'核对这一步':'待核对'}</Badge>}</div>)}</>:<Empty title="数学核验结果尚未提供"/>}</Card><Button onClick={()=>onNavigate(context.role==="TEACHER"?"assignments":"practice")}>{context.role==="TEACHER"?"准备针对性练习":"自己试一道练习"} <ArrowRight size={14}/></Button></div></div><Card className="card-pad stack"><div className="card-header"><h2>查看分析依据</h2><BookOpen size={18} color="var(--cyan)"/></div><Details title={context.role==="TEACHER"?"官方评分点与当前有效口径":"本轮得分记录"}><div className="row"><Badge>官方原分 {response.record.official_score??'未评估'}</Badge>{context.role==="TEACHER"&&<Badge>AI 候选分 {response.record.candidate_score??"未评估"}</Badge>}<Badge tone="info">当前有效分 {response.record.effective_score??'未评估'}</Badge></div>{(response.rubric_points??[]).map((point,index)=><div key={index} style={{padding:'10px 0',borderBottom:'1px solid var(--border)'}}><p>{text(point.exact_text??point.label)}</p><small className="muted">满分 {text(point.max_score)} · 来源保留</small></div>)}</Details><Details title="关联知识与教材定位"><div className="stack">{(response.source_passages??[]).map(passage=>{const source=object(passage.source_ref);const id=text(passage.id,"");return <section className="stack" key={id}><div className="row"><strong>{text(source.resource_title,"来源片段")}</strong><Badge>{stateLabel(text(passage.review_status))}</Badge><span className="muted">物理页 {text(source.physical_page)} · 印刷页 {text(source.printed_page)}</span></div><blockquote className="source-quote" style={{margin:0}}>{text(passage.exact_text)}</blockquote>{id&&<button className="evidence-link" onClick={()=>onEvidence(id)}>查看版次与核对记录</button>}</section>;})}{evidenceIds(response.evidence_refs).filter(ref=>!(response.source_passages??[]).some(passage=>passage.id===ref)).map(ref=><button className="evidence-link" key={ref} onClick={()=>onEvidence(ref)}>查看关联依据 <span className="mono muted">{ref}</span></button>)}</div></Details>{context.role==="TEACHER"&&<Details title="逐步操作与评分点关联"><div className="stack">{entries(response.step_rubric_links).map(object).map((link,index)=><div className="row" key={index}><Badge tone={link.operation_match==="CONTRADICTED"?"warning":"info"}>{link.operation_match==="CONTRADICTED"?"本步操作未满足评分点":link.operation_match==="FULFILLED"?"本步操作符合评分点":stateLabel(text(link.operation_match))}</Badge><span className="mono">{text(link.step_id)} → {text(link.rubric_id)}</span>{evidenceIds(link.evidence_refs).map(ref=><button className="evidence-link" key={ref} onClick={()=>onEvidence(ref)}>{ref}</button>)}</div>)}<JsonDetails value={{alignment:response.step_alignment,rubric:response.rubric_verification,knowledge:response.step_node_links}} title="关联版本与审核信息"/></div></Details>}{math?.limitations?.map((limitation,index)=><div className="banner" key={index}>{limitation}</div>)}</Card></>}{context.role==='TEACHER'&&<Card className="card-pad stack"><div className="row spread"><div><span className="eyebrow">A SECOND LOOK</span><h2 style={{marginTop:5}}>DeepSeek 双路复核</h2></div><Sparkles size={19} color="var(--purple)"/></div><p className="muted" style={{fontSize:12}}>显式运行后，服务仅按许可范围读取选定作答与必要知识片段。A/B结果与数学核验分别保留。</p><label className="check"><input type="checkbox" checked={permit} onChange={event=>setPermit(event.target.checked)}/>允许将当前选定作答及必要来源片段用于本次DeepSeek分析</label><div><Button variant="ai" disabled={!permit||!context.model_configured||authorizing||command.busy} onClick={()=>void analyze()}><Sparkles size={14}/>运行本题双路复核</Button> <Button disabled={!permit||!context.model_configured||authorizing||command.busy} onClick={()=>void analyze('build_learning_profile')}>运行学习画像 Skill</Button> <Button disabled={!permit||!context.model_configured||authorizing||command.busy} onClick={()=>void analyze('recommend_learning_tasks')}>运行练习建议 Skill</Button></div>{authorizationError!=null&&<ErrorNotice error={authorizationError}/>} {command.busy&&<Busy cancel={command.cancel}/>} {command.error!=null&&<ErrorNotice error={command.error}/>} {command.result!=null&&<AgentResult result={command.result} onEvidence={onEvidence}/>}</Card>}</>;
}

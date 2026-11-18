import { useEffect, useState } from 'react';
import type { FormEvent, ReactNode } from 'react';
import { Database, Plus } from 'lucide-react';
import { api } from '../api.ts';
import type { Envelope } from '../api.ts';
import { Badge, Button, Details, Dialog, Empty, ErrorNotice, Loading, Status, Tabs } from '../components/ui.tsx';
import type { KnowledgeNode, PracticeItem } from '../models.ts';
import { entries, object, text } from '../models.ts';
import { stateLabel } from '../presentation.ts';

const tabs = [
  {id:'practice',label:'独立练习题库'},
  {id:'relations',label:'题目与知识关联'},
  {id:'sources',label:'教材原文'},
];
const endpoints: Record<string, string> = {
  practice:'/knowledge/practice', relations:'/knowledge/relations', sources:'/knowledge/sources',
};
type CatalogItem = PracticeItem & {
  practice_id:string; source_description:string; author:string; authorized_use:boolean; math_validation?:string;
};
type Relation = {
  relation_id:string; question_id:string; rubric_id?:string; node_id:string; relation_type:string;
  review_status:string; mapping_version:string; evidence_refs:string[];
};
type Editor = {kind:'practice'|'source'} | {kind:'practice-review';item:CatalogItem} | {kind:'relation-review';item:Relation};
type Fields = Record<string, FormDataEntryValue>;

function Field({ name, label, value, maxLength=160 }: {name:string;label:string;value?:string;maxLength?:number}) {
  return <label className="field">{label}<input name={name} required defaultValue={value} maxLength={maxLength}/></label>;
}

function Reviewer() {
  return <Field name="reviewer_id" label="审核者标记" maxLength={120}/>;
}

function KnowledgeChoice({ nodes, label }: {nodes:KnowledgeNode[];label:string}) {
  return <label className="field">{label}
    <select name="node_ids" aria-label={label} multiple required>
      {nodes.map(node => <option value={node.id} key={node.id}>{node.id} · {node.label}</option>)}
    </select>
  </label>;
}

export function KnowledgeLibrary({ nodes, onRefresh }: {nodes:KnowledgeNode[];onRefresh:()=>void}) {
  const [open, setOpen] = useState(false);
  const [section, setSection] = useState('practice');
  const [editor, setEditor] = useState<Editor>();
  const [catalog, setCatalog] = useState<Envelope<Record<string, unknown>>>();
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<unknown>();
  const [changed, setChanged] = useState(false);
  const [feedback, setFeedback] = useState('');
  const [question, setQuestion] = useState('');
  const [reload, setReload] = useState(0);

  useEffect(() => {
    if (!open) return;
    const controller = new AbortController();
    setCatalog(undefined);
    setError(undefined);
    api.requestEnvelope<Record<string, unknown>>(endpoints[section], {signal:controller.signal})
      .then(result => {if (!controller.signal.aborted) setCatalog(result);})
      .catch(failure => {if (!controller.signal.aborted) setError(failure);});
    return () => controller.abort();
  }, [open, section, reload]);

  function close() {
    if (pending) return;
    setOpen(false);
    setEditor(undefined);
    setFeedback('');
    setError(undefined);
    if (changed) onRefresh();
    setChanged(false);
  }
  function switchSection(next: string) {
    if (next === section || pending) return;
    setSection(next);
    setCatalog(undefined);
    setEditor(undefined);
    setFeedback('');
    setQuestion('');
  }
  async function mutate(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!editor || !catalog?.snapshot_hash || pending) return;
    const form = new FormData(event.currentTarget, (event.nativeEvent as SubmitEvent).submitter);
    const fields = Object.fromEntries(form) as Fields;
    const { reviewer_id, ...values } = Object.fromEntries(
      Object.entries(fields).map(([key,value]) => [key, String(value).trim()])
    );
    const audit = { reviewer_id, expected_snapshot_hash:catalog.snapshot_hash };
    const nodeIds = form.getAll('node_ids');
    if (editor.kind === 'practice-review' && values.action === 'approve' && !form.has('reviewed')) {
      setError(new Error('请先确认题干、答案、解析和使用授权。'));
      return;
    }
    setPending(true);
    setError(undefined);
    try {
      if (editor.kind === 'practice') {
        const {practice_id, exact_text, reference_answer, reference_explanation, source_description, author} = values;
        await api.post(endpoints.practice,{...audit,items:[{
          practice_id, exact_text, reference_answer, reference_explanation, source_description, author,
          knowledge_refs:nodeIds, authorized_use:form.has('authorized_use'),
        }]});
        setFeedback('已登记候选练习，等待教师审核。');
      } else if (editor.kind === 'practice-review') {
        const approved = values.action === 'approve';
        await api.post(endpoints.practice + '/' + encodeURIComponent(editor.item.practice_id) + '/review',{
          ...audit, approved, authorized_use:approved || editor.item.authorized_use,
        });
        setFeedback(approved ? '审核已记录，可在可用练习中查找。' : '已保留为候选练习。');
      } else if (editor.kind === 'relation-review') {
        await api.post(endpoints.relations + '/' + encodeURIComponent(editor.item.relation_id) + '/reviews',{
          ...audit, review_status:values.review_status, reason:values.reason,
        });
        setFeedback('已保存新的关联审核版本。');
      } else {
        const {source_id, passage_id, exact_text, physical_page, printed_page, resource_title, edition} = values;
        await api.post(endpoints.sources + '/passages',{
          ...audit, source_id, passage_id, exact_text, physical_page:Number(physical_page),
          printed_page:printed_page || null, resource_title, edition, node_ids:nodeIds,
        });
        setFeedback('已登记与本地 PDF 核对一致的原文候选。');
      }
      setChanged(true);
      setEditor(undefined);
      setReload(current => current + 1);
    } catch (failure) {
      setError(failure);
    } finally {
      setPending(false);
    }
  }

  const data = catalog?.data ?? {};
  const items = entries(data.items) as CatalogItem[];
  const links = [...entries(data.question_node_links), ...entries(data.rubric_node_links)] as Relation[];
  const questionIds = [...new Set(links.map(link => link.question_id))];
  const visibleLinks = links.filter(link => link.question_id === (question || questionIds[0]));
  const sources = entries(data.sources).map(object);
  const passages = entries(data.passages).map(object);
  const editable = !!catalog?.snapshot_hash && !pending;
  const cancel = <Button disabled={pending} onClick={() => {setEditor(undefined);setError(undefined);}}>取消</Button>;
  let content: ReactNode;

  if (editor?.kind === 'practice') {
    content = <form className="stack" onSubmit={mutate}>
      <h3>登记独立练习</h3>
      <div className="form-grid"><Field name="practice_id" label="题源编号"/><Field name="author" label="题目作者"/></div>
      <label className="field">题干<textarea name="exact_text" rows={2} required maxLength={6000}/></label>
      <Field name="reference_answer" label="参考答案" maxLength={4000}/>
      <label className="field">解析<textarea name="reference_explanation" rows={2} required maxLength={6000}/></label>
      <Field name="source_description" label="来源说明" maxLength={4000}/>
      <KnowledgeChoice nodes={nodes} label="练习关联知识"/>
      <Reviewer/>
      <label className="check"><input name="authorized_use" type="checkbox"/>题源允许用于学生练习</label>
      <div className="row">{cancel}<Button type="submit" variant="primary" disabled={!editable}>登记为候选</Button></div>
    </form>;
  } else if (editor?.kind === 'practice-review') {
    const {item} = editor;
    content = <form className="stack" onSubmit={mutate}>
      <h3>核对练习题源</h3>
      <div className="source-quote">{item.exact_text}</div>
      <p><strong>参考答案</strong>　{item.reference_answer}</p>
      <p><strong>解题解析</strong>　{item.reference_explanation}</p>
      <p className="muted">{item.source_description} · {item.author}</p>
      <Reviewer/>
      <label className="check"><input name="reviewed" type="checkbox"/>我已核对题干、答案、解析和使用授权。</label>
      <div className="row">{cancel}<Button type="submit" name="action" value="hold" disabled={!editable}>保留候选</Button>
        <Button type="submit" name="action" value="approve" variant="primary" disabled={!editable}>审核通过</Button></div>
    </form>;
  } else if (editor?.kind === 'relation-review') {
    content = <form className="stack" onSubmit={mutate}>
      <h3>审核评分点与知识关联</h3>
      <p className="mono">{editor.item.rubric_id} → {editor.item.node_id}</p>
      <label className="field">审核结果<select name="review_status" required>
        <option value="">选择审核结果</option><option value="HUMAN_VERIFIED">确认关联</option>
        <option value="DISPUTED">存在争议</option><option value="REJECTED">驳回</option><option value="UNRESOLVED">继续核对</option>
      </select></label>
      <label className="field">审核依据<textarea name="reason" rows={3} required maxLength={4000}/></label>
      <Reviewer/>
      <div className="row">{cancel}<Button type="submit" variant="primary" disabled={!editable}>保存关联审核</Button></div>
    </form>;
  } else if (editor?.kind === 'source') {
    content = <form className="stack" onSubmit={mutate}>
      <h3>录入可核对的教材原文</h3>
      <label className="field">本地教材<select name="source_id" required><option value="">选择已核验文件</option>
        {sources.map(source => <option value={text(source.id)} key={text(source.id)}>{text(source.title)}</option>)}
      </select></label>
      <div className="form-grid"><Field name="passage_id" label="原文片段编号" maxLength={120}/><Field name="resource_title" label="教材名称"/></div>
      <div className="form-grid"><Field name="edition" label="版次" maxLength={120}/>
        <label className="field">PDF 物理页<input name="physical_page" type="number" min={1} required/></label>
      </div>
      <label className="field">印刷页码（可选）<input name="printed_page" maxLength={60}/></label>
      <label className="field">教材原文<textarea name="exact_text" rows={4} required maxLength={6000}/></label>
      <KnowledgeChoice nodes={nodes} label="原文关联知识"/>
      <Reviewer/>
      <div className="row">{cancel}<Button type="submit" variant="primary" disabled={!editable}>核对并登记原文</Button></div>
    </form>;
  } else if (section === 'practice') {
    content = <>
      <div className="row spread"><h3>独立练习的审核记录</h3><Button variant="primary" disabled={!editable || !nodes.length} onClick={() => setEditor({kind:'practice'})}><Plus size={14}/>新增待审核练习</Button></div>
      {items.length ? <ul className="plain-list">{items.map(item => <li className="stack" data-practice-id={item.practice_id} key={item.practice_id}>
        <div className="row spread"><strong>{item.exact_text}</strong><Status value={item.source_status}/></div>
        <p className="muted">{item.source_description} · {item.practice_id}</p>
        <div className="row"><Button variant="small" onClick={() => setEditor({kind:'practice-review',item})}>核对题源</Button></div>
      </li>)}</ul> : <Empty title="还没有新增的独立练习" description="登记题干、答案、来源与关联知识，审核通过后再供学生使用。"/>}
      {entries(data.reviews).length > 0 && <Details title="查看练习审核历史">
        <ul className="plain-list">{entries(data.reviews).map(object).reverse().map((event,index) => <li key={index}>
          <strong>{text(event.practice_id)} · {text(event.reviewer_id)}</strong><p className="muted">{event.approved ? '审核通过' : '候选记录'} · {text(event.recorded_at)}</p>
        </li>)}</ul>
      </Details>}
    </>;
  } else if (section === 'relations') {
    content = <div className="stack">
      <label className="field">筛选题目<select aria-label="筛选题目" value={question || questionIds[0] || ''} onChange={event => setQuestion(event.target.value)}>
        {questionIds.map(id => <option key={id}>{id}</option>)}
      </select></label>
      {visibleLinks.length ? <ul className="plain-list">{visibleLinks.map(item => <li key={item.relation_id} className="stack">
        <div className="row spread"><strong>{item.rubric_id ?? item.question_id} → {nodes.find(node => node.id === item.node_id)?.definition ?? item.node_id}</strong><Badge>{stateLabel(item.review_status)}</Badge></div>
        <p className="mono muted">{item.mapping_version} · {item.relation_type === 'DIRECT_ASSESSED' ? '题目直接考查' : '评分点关联'}</p>
        {item.rubric_id && <div className="row"><Button variant="small" onClick={() => setEditor({kind:'relation-review',item})}>审核关联</Button></div>}
      </li>)}</ul> : <Empty title="当前范围没有可展示的关联"/>}
      {entries(data.reviews).length > 0 && <Details title="查看关联审核历史">
        <ul className="plain-list">{entries(data.reviews).map(object).reverse().map((event,index) => <li key={index}>
          <strong>{stateLabel(text(event.review_status))} · {text(event.reviewer_id)}</strong><p>{text(event.reason)}</p><p className="mono muted">{text(event.relation_id)} · {text(event.recorded_at)}</p>
        </li>)}</ul>
      </Details>}
    </div>;
  } else {
    content = <section className="stack" aria-label="教材原文索引">
      <div className="row spread"><h3>教材与原文索引</h3>
        {sources.length > 0 && <Button variant="primary" disabled={!editable} onClick={() => setEditor({kind:'source'})}><Plus size={14}/>录入原文</Button>}
      </div>
      {sources.length > 0 && <Details title="查看本地教材与校验值">
        <ul className="plain-list">{sources.map(source => <li key={text(source.id)}><strong>{text(source.title)}</strong><p className="mono muted">{text(source.sha256)}</p></li>)}</ul>
      </Details>}
      {passages.length ? <ul className="plain-list">{passages.map(passage => {
        const source = object(passage.source_ref);
        return <li key={text(passage.id)}><Details title={`${text(source.resource_title,'教材片段')} · PDF 第 ${text(source.physical_page)} 页`}>
          <div className="stack"><p className="mono muted">{text(passage.id)} · {text(source.edition)} · 印刷页 {text(source.printed_page)}</p>
            <div className="source-quote">{text(passage.exact_text)}</div>
          </div>
        </Details></li>;
      })}</ul> : <Empty title="当前知识库没有已录入的教材片段" description="正式工作空间可从已核验的本地教材登记原文。"/>}
    </section>;
  }

  return <>
    <Button onClick={() => setOpen(true)}><Database size={15}/>管理题源与教材</Button>
    <Dialog title="题源与教材" open={open} onClose={close} description="核对练习使用授权、课程关联与教材原文。">
      {open && <div className="knowledge-library stack">
        <Tabs tabs={tabs} value={section} onChange={switchSection}/>
        {feedback && <p role="status" className="banner info">{feedback}</p>}
        {error != null && <ErrorNotice error={error} onRetry={() => {
          setEditor(undefined);
          setReload(current => current + 1);
        }}/>}
        {catalog ? content : !error && <Loading/>}
      </div>}
    </Dialog>
  </>;
}

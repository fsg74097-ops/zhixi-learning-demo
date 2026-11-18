import { useEffect, useRef, useState } from 'react';
import type { KeyboardEvent, PointerEvent } from 'react';
import { ArrowRight, BookOpen, Layers, MousePointer2, Plus, RotateCcw, Search } from 'lucide-react';
import type { FeatureProps } from './types.ts';
import type { KnowledgeEdge, KnowledgeNode, PracticeItem } from '../models.ts';
import { entries, object, text } from '../models.ts';
import { useCommand } from '../hooks.ts';
import { api, emitUiEvent } from '../api.ts';
import { findGraphDropTarget } from '../knowledgeGraph.ts';
import type { GraphPoint } from '../knowledgeGraph.ts';
import { formatRate, stateLabel } from '../presentation.ts';
import { Badge, Busy, Button, Card, Empty, ErrorNotice, Tabs } from '../components/ui.tsx';

interface DragState {id: string; start: GraphPoint; position: GraphPoint; moved: boolean}

function initialPositions(nodes: KnowledgeNode[]) {
  const columns = nodes.length <= 4 ? 2 : 3;
  const spacing = nodes.length <= 4 ? 300 : 245;
  return Object.fromEntries(nodes.map((node, index) => [
    node.id, {x: 125 + (index % columns) * spacing, y: 110 + Math.floor(index / columns) * 138},
  ]));
}

function nodeColor(node: KnowledgeNode) {
  if (!node.n || node.rate == null) return '#9da9b9';
  if (node.rate >= .75) return '#158489';
  return node.rate >= .4 ? '#416bcc' : '#b68a3c';
}

function nodeRadius(node: KnowledgeNode) {
  return 25 + Math.min(10, node.n ?? 0);
}

function lineStyle(edge: KnowledgeEdge) {
  const kind = edge.relation_type.toLowerCase();
  if (kind.includes('prerequisite')) return {label: '先修关系', color: '#a9b8cc', dash: undefined};
  if (kind.includes('recommend')) return {label: '练习推荐关系', color: '#a99ac6', dash: '3 5'};
  return {label: '联合考查关系', color: '#b5c2d2', dash: '7 5'};
}

function isActivationKey(event: KeyboardEvent) {
  return event.key === 'Enter' || event.key === ' ';
}

export function KnowledgeMap({data, context, scope, onEvidence, onNavigate, notify}: FeatureProps) {
  const nodes = data.nodes ?? [];
  const edges = data.edges ?? [];
  const [mode, setMode] = useState('map');
  const [selected, setSelected] = useState<string[]>([]);
  const [selectedEdge, setSelectedEdge] = useState<KnowledgeEdge>();
  const [positions, setPositions] = useState(() => initialPositions(nodes));
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState<unknown>();
  const svg = useRef<SVGSVGElement>(null);
  const drag = useRef<DragState | null>(null);
  const command = useCommand(context.snapshot_hash + ':' + scope.student_id);
  const nodeById = new Map(nodes.map(node => [node.id, node]));
  const height = Math.max(430, Math.ceil(nodes.length / 3) * 138 + 80);
  const isTeacher = context.role === 'TEACHER';

  useEffect(() => {
    setPositions(initialPositions(nodes));
    setSelected([]);
    setSelectedEdge(undefined);
  }, [nodes.map(node => node.id).join('|')]);

  function toggle(id: string) {
    const wasSelected = selected.includes(id);
    emitUiEvent(context, 'NODE_SELECTED', {node_id: id, selected: !wasSelected});
    setSelected(wasSelected ? selected.filter(value => value !== id) : [...selected.slice(-1), id]);
  }

  function selectEdge(edge: KnowledgeEdge) {
    setSelectedEdge(edge);
    emitUiEvent(context, 'EDGE_SELECTED', {source: edge.source, target: edge.target, relation_type: edge.relation_type});
  }

  function moveNode(id: string, x: number, y: number) {
    const position = {x: Math.max(55, Math.min(705, x)), y: Math.max(70, Math.min(height - 65, y))};
    setPositions(current => ({...current, [id]: position}));
  }

  function coordinate(event: PointerEvent<SVGElement>): GraphPoint {
    const point = new DOMPoint(event.clientX, event.clientY);
    const position = point.matrixTransform(svg.current?.getScreenCTM()?.inverse());
    return {x: position.x, y: position.y};
  }

  function startDrag(event: PointerEvent<SVGGElement>, id: string, position: GraphPoint) {
    if (event.button !== 0) return;
    svg.current?.setPointerCapture(event.pointerId);
    drag.current = {id, start: coordinate(event), position, moved: false};
  }

  function moveDrag(event: PointerEvent<SVGSVGElement>) {
    const activeDrag = drag.current;
    if (!activeDrag) return;
    const now = coordinate(event);
    const dx = now.x - activeDrag.start.x;
    const dy = now.y - activeDrag.start.y;
    if (Math.abs(dx) + Math.abs(dy) > 4) activeDrag.moved = true;
    if (activeDrag.moved) moveNode(activeDrag.id, activeDrag.position.x + dx, activeDrag.position.y + dy);
  }

  function finishDrag(event: PointerEvent<SVGSVGElement>) {
    const activeDrag = drag.current;
    drag.current = null;
    if (!activeDrag) return;
    const {id} = activeDrag;
    if (!activeDrag.moved) {
      toggle(id);
      return;
    }
    if (!isTeacher) return;
    const targets = nodes.flatMap(node => positions[node.id] ? [{
      id: node.id, position: positions[node.id], radius: nodeRadius(node) + 9,
    }] : []);
    const pairedId = findGraphDropTarget(coordinate(event), id, targets);
    if (!pairedId) return;
    setPositions(current => ({...current, [id]: activeDrag.position}));
    for (const nodeId of [id, pairedId]) {
      if (!selected.includes(nodeId)) emitUiEvent(context, 'NODE_SELECTED', {node_id: nodeId, selected: true});
    }
    setSelected([id, pairedId]);
    void find([id, pairedId]);
  }

  function handleNodeKey(event: KeyboardEvent, id: string, position: GraphPoint) {
    if (isActivationKey(event)) {
      event.preventDefault();
      toggle(id);
      return;
    }
    const shifts: Record<string, GraphPoint> = {
      ArrowLeft: {x: -12, y: 0}, ArrowRight: {x: 12, y: 0},
      ArrowUp: {x: 0, y: -12}, ArrowDown: {x: 0, y: 12},
    };
    const shift = shifts[event.key];
    if (!shift) return;
    event.preventDefault();
    moveNode(id, position.x + shift.x, position.y + shift.y);
  }

  async function find(refs = selected) {
    try {
      await command.run('/tools/find_practice_items', {
        student_id: scope.student_id,
        arguments: {knowledge_refs: refs, item_count: 3, source_policy: 'approved_practice_only', constraints: {}},
      });
    } catch { /* The command exposes errors to the workspace. */ }
  }

  async function create() {
    setCreating(true);
    setCreateError(undefined);
    try {
      const draft = await api.post<{draft_id: string}>('/assignments', {
        request: {student_refs: [scope.student_id], target_knowledge_refs: selected, item_count: 3, max_minutes: 15},
      });
      notify('已创建待教师核对的任务草稿。');
      onNavigate('assignments/' + draft.draft_id);
    } catch (error) {
      setCreateError(error);
    } finally {
      setCreating(false);
    }
  }

  const result = object(command.result);
  const practices = entries(result.items ?? result.practice_items) as PracticeItem[];
  const active = nodes.filter(node => selected.includes(node.id));
  const actions = <>
    <Button disabled={!selected.length || command.busy} onClick={() => void find()}><Search size={14}/>检索关联练习</Button>
    {isTeacher && <Button variant="primary" disabled={!selected.length || creating} onClick={create}><Plus size={14}/>创建任务草稿</Button>}
  </>;

  if (!nodes.length) return <Card><Empty title="当前知识节点尚未就绪" description="知识节点必须来自真实知识库。请先核对来源与关联，再进行探索。"/></Card>;

  return <>
    <div className="row spread">
      <Tabs value={mode} onChange={setMode} tabs={[{id:'map',label:'知识地图'},{id:'list',label:'列表与证据'},{id:'matrix',label:'覆盖矩阵'}]}/>
      <Badge><Layers size={11}/>{nodes.length} 个真实知识节点</Badge>
    </div>
    {mode === 'map' ? <Card className="knowledge-map">
      <div className="graph-shell">
        <div className="graph-canvas">
          <div className="graph-toolbar">
            <Badge><MousePointer2 size={11}/>拖动探索 · 最多选择两点</Badge>
            <Button variant="icon small" aria-label="重置节点位置" onClick={() => setPositions(initialPositions(nodes))}><RotateCcw size={13}/></Button>
          </div>
          <svg ref={svg} viewBox={'0 0 760 ' + height} aria-label="当前观察知识图谱"
            onPointerMove={moveDrag} onPointerUp={finishDrag} onPointerCancel={() => { drag.current = null; }}>
            <g>{edges.map((edge, index) => {
              const source = positions[edge.source];
              const target = positions[edge.target];
              if (!source || !target) return null;
              const style = lineStyle(edge);
              const activeEdge = selectedEdge === edge;
              const label = [nodeById.get(edge.source)?.label, nodeById.get(edge.target)?.label, style.label].join(' · ');
              const coordinates = {x1: source.x, y1: source.y, x2: target.x, y2: target.y};
              return <g key={index} data-edge-source={edge.source} data-edge-target={edge.target} className="graph-edge"
                role="button" tabIndex={0} aria-label={label} aria-pressed={activeEdge} onClick={() => selectEdge(edge)}
                onKeyDown={event => {
                  if (!isActivationKey(event)) return;
                  event.preventDefault();
                  selectEdge(edge);
                }}>
                <title>{label}</title>
                <line {...coordinates} stroke={activeEdge ? 'var(--primary)' : style.color} strokeWidth={activeEdge ? 2.5 : 1.4} strokeDasharray={style.dash} className="graph-edge-path"/>
                <line {...coordinates} stroke="transparent" strokeWidth="16" pointerEvents="stroke"/>
              </g>;
            })}</g>
            {nodes.map(node => {
              const point = positions[node.id];
              if (!point) return null;
              const color = nodeColor(node);
              const radius = nodeRadius(node);
              const picked = selected.includes(node.id);
              const words = node.label.length > 13 ? [node.label.slice(0, 13), node.label.slice(13, 26), node.label.slice(26)] : [node.label];
              return <g key={node.id} data-node-id={node.id} className="graph-node" transform={'translate(' + point.x + ',' + point.y + ')'}
                tabIndex={0} role="button" aria-pressed={picked}
                aria-label={node.label + '，' + (node.n ? node.n + '次观察' : '未观察') + '，' + (picked ? '已选择' : '未选择')}
                onPointerDown={event => startDrag(event, node.id, point)}
                onKeyDown={event => handleNodeKey(event, node.id, point)}>
                <circle r={radius + 9} fill={picked ? '#e7efff' : '#ffffffaa'} stroke={picked ? 'var(--primary)' : '#dbe3ed'} strokeWidth={picked ? 2 : 1}/>
                <circle r={radius + 4} fill="none" stroke={color} opacity=".55" strokeWidth="2"
                  strokeDasharray={Math.max(0, Math.min(1, node.coverage ?? 0)) * 2 * Math.PI * (radius + 4) + ' 999'} transform="rotate(-90)"/>
                <circle r={radius} fill={node.n ? color + '18' : '#edf1f5'} stroke={color} strokeWidth="1.2"/>
                <text textAnchor="middle" y="5" fontSize="15" fill={node.n ? color : '#7e8b9e'} fontWeight="600">{node.n ? node.n + ' 次' : '—'}</text>
                {words.filter(Boolean).map((word, index) => <text key={index} textAnchor="middle" y={radius + 28 + index * 14} fontSize="10" fill="var(--text)">{word}</text>)}
              </g>;
            })}
          </svg>
        </div>
        <aside className="graph-side">
          <span className="eyebrow">CONNECTED EVIDENCE</span>
          {selectedEdge && <section className="graph-selection" aria-label="关系说明" aria-live="polite">
            <h3>关系说明</h3>
            <p>{nodeById.get(selectedEdge.source)?.label} → {nodeById.get(selectedEdge.target)?.label}</p>
            <div className="row"><Badge>{lineStyle(selectedEdge).label}</Badge>{selectedEdge.review_status && <Badge>{stateLabel(selectedEdge.review_status)}</Badge>}</div>
          </section>}
          <h2>{active.length ? '从知识走向行动' : '选择一个起点'}</h2>
          {active.length ? active.map(node => <div className="graph-selection" key={node.id}>
            <h3>{node.label}</h3>
            <p>{node.definition || '请查看当前节点的真实知识依据。'}</p>
            <div className="row"><Badge>{node.n ? 'n=' + node.n : '未观察'}</Badge><Badge>{formatRate(node.rate)}</Badge></div>
            <button className="evidence-link" onClick={() => onEvidence(node.id)}><BookOpen size={12}/>查看知识原文</button>
          </div>) : <p className="muted" style={{fontSize:13}}>
            {isTeacher ? '选择两个节点，或将一个节点拖到另一个节点上，检索已授权的关联练习。' : '选择知识节点，检索已授权的关联练习。'}
          </p>}
          {actions}
        </aside>
      </div>
      <div className="graph-legend">
        <span><i className="legend-dot"/>颜色：本次观测得分率</span><span>大小：已评分事件 n</span><span>外圈：证据覆盖</span>
        <span>灰色：未观察</span><span>实线：先修 · 虚线：联合考查 · 点线：推荐</span>
      </div>
    </Card> : <Card>
      <div className="table-wrap"><table className="data-table">
        <thead><tr><th>知识名称</th><th>观察 n</th><th>覆盖率</th><th>本次得分率</th><th>依据与选择</th></tr></thead>
        <tbody>{nodes.map(node => <tr key={node.id}>
          <td><strong>{node.label}</strong></td><td>{node.n || '未观察'}</td>
          <td>{formatRate(node.coverage)}</td><td>{formatRate(node.rate)}</td>
          <td><div className="row">
            <label className="check"><input type="checkbox" checked={selected.includes(node.id)} onChange={() => toggle(node.id)}/>选择</label>
            <button className="evidence-link" onClick={() => onEvidence(node.id)}>查看知识依据</button>
          </div></td>
        </tr>)}</tbody>
      </table></div>
      <div className="card-pad row">{actions}</div>
    </Card>}
    {command.busy && <Busy label="正在核对知识关联与练习授权" cancel={command.cancel}/>}
    {(command.error || createError) != null && <ErrorNotice error={command.error ?? createError}/>}
    {command.result != null && <Card className="card-pad">
      <header className="card-header"><h2>可继续尝试的练习</h2><Badge>{practices.length} 道真实可用题</Badge></header>
      {practices.length ? <div className="stack">{practices.map((item, index) => {
        const id = item.practice_id ?? item.question_id ?? item.id ?? '';
        return <div className="row spread" key={id || index}>
          <div><strong>{item.exact_text}</strong>
            <p className="muted" style={{fontSize:11}}>题源：已授权练习 · {item.knowledge_refs?.map(id => nodeById.get(id)?.label ?? '关联知识').join(' / ')}</p>
          </div>
          <Button variant="small" onClick={() => onNavigate(isTeacher ? 'assignments' : 'practice/self:' + encodeURIComponent(id))}>继续 <ArrowRight size={12}/></Button>
        </div>;
      })}</div> : <Empty title="当前授权题库没有满足条件的练习" description="调整所选知识点，或由教师补充并审核真实题源。系统不会编造题号补足。"/>}
      {Number(result.shortfall) > 0 && <p className="muted" style={{marginTop:14,fontSize:12}}>可用题量不足，仍缺 {text(result.shortfall)} 道；现有题目保持原样。</p>}
    </Card>}
  </>;
}

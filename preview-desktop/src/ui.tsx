import { useEffect, useRef, type ReactNode } from "react";
import { descendants, knowledgeTree, toggleValue, type KnowledgeNode } from "./domain";

export function Modal({ title, onClose, children, wide = false }: {
  title: string; onClose: () => void; children: ReactNode; wide?: boolean;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const dialog = ref.current!;
    dialog.showModal();
    return () => dialog.close();
  }, []);
  return <dialog ref={ref} className={wide ? "wide" : ""} onCancel={event => { event.preventDefault(); onClose(); }}>
    <header className="dialog-header"><strong>{title}</strong><button onClick={onClose} aria-label="关闭">×</button></header>
    {children}
  </dialog>;
}

export type Ask = (request: { title: string; text?: string; initial?: string; buttons: [string, string][] }) => Promise<string | undefined>;

export function Check({ value, onChange, children }: { value: boolean; onChange: () => void; children: ReactNode }) {
  return <label className="check"><input type="checkbox" checked={value} onChange={onChange} />{children}</label>;
}

function KnowledgeBranch({ node, selected, toggle, query, expanded, setExpanded }: {
  node: KnowledgeNode; selected: string[]; toggle: (node: KnowledgeNode) => void; query: string;
  expanded: string[]; setExpanded: (path: string) => void;
}) {
  if (query && !descendants(node).some(path => path.toLowerCase().includes(query))) return null;
  const open = !!query || expanded.includes(node.path);
  return <li>
    <div className="tree-row">
      {node.children && <button className="arrow" aria-label={open ? "收起" : "展开"} aria-expanded={open} onClick={() => setExpanded(node.path)}>{open ? "▾" : "▸"}</button>}
      <Check value={selected.includes(node.path)} onChange={() => toggle(node)}>{node.label}</Check>
    </div>
    {node.children && open && <ul>{node.children.map(child => <KnowledgeBranch key={child.path}
      {...{ node: child, selected, toggle, query, expanded, setExpanded }} />)}</ul>}
  </li>;
}

export function KnowledgeTree({ selected, change, cascade, query = "", expanded, setExpanded }: {
  selected: string[]; change: (paths: string[]) => void; cascade?: boolean; query?: string;
  expanded: string[]; setExpanded: (value: string[]) => void;
}) {
  function toggle(node: KnowledgeNode) {
    if (!cascade) { change(toggleValue(selected, node.path)); return; }
    const paths = descendants(node);
    change(selected.includes(node.path) ? selected.filter(path => !paths.includes(path)) : [...new Set([...selected, ...paths])]);
  }
  return <ul className="knowledge-tree">{knowledgeTree.map(node => <KnowledgeBranch key={node.path}
    {...{ node, selected, toggle, query: query.trim().toLowerCase(), expanded, setExpanded: path => setExpanded(toggleValue(expanded, path)) }} />)}</ul>;
}

export function Stars({ value, onChange }: { value: number; onChange?: (value: number) => void }) {
  return <span className="stars" aria-label={`${value} 星`}>{Array.from({ length: 5 }, (_, i) => {
    const percent = Math.max(0, Math.min(1, value - i)) * 100;
    return <span className="star" key={i}>☆<span className="filled" style={{ width: `${percent}%` }}>★</span>
      {onChange && <><button aria-label={`${i + 0.5} 星`} onClick={() => onChange(i + 0.5)} /><button aria-label={`${i + 1} 星`} onClick={() => onChange(i + 1)} /></>}
    </span>;
  })}</span>;
}

import { useEffect, useRef, useState, type ClipboardEvent, type Dispatch, type DragEvent, type SetStateAction } from "react";
import { invoke } from "@tauri-apps/api/core";
import catalog from "./latex-catalog.json";
import {
  applyImageWidths, choice, codeWidth, errorText, imageCode, imagePattern, keyForImage, knowledgeTree, chapterNodes,
  loadImage, marker, matchImage, options, questionTypes, raster, toggleValue, uid,
  type Field, type ImageContext, type ImageFiles, type Question, type Target,
} from "./domain";
import { Analysis, MathText, normalizeText, QuestionContent, QuestionImage } from "./math";
import { Check, KnowledgeTree, Modal, Stars } from "./ui";
import { Drawing } from "./drawing";

const fields: Record<Field, string> = { stem: "题目", answer: "答案", analysis: "解析", note: "备注" };
const placeholder: Record<Field, string> = { stem: "行内公式用 $...$，向量用 \\v{a_1} 或 \\v{AB}，填空横线用 @", answer: "答案支持 LaTeX", analysis: "在光标处粘贴或拖入图片", note: "备注" };
export const initialExpanded = [...knowledgeTree, ...chapterNodes].map(node => node.path);

export interface Draft {
  q: Question;
  baseline: string;
  existing: boolean;
  files: ImageFiles;
  keys: Record<string, string>;
  questionCodes: string;
}

export function makeDraft(q: Question, keys: Record<string, string>, existing: boolean): Draft {
  q = structuredClone(q);
  q.analysis = q.analysis.replace(imagePattern(), (...parts) => {
    const code = parts.slice(0, 5);
    const path = matchImage(code, q.analysis_image_paths, keys);
    return path ? imageCode(keys[path], codeWidth(code) ?? q.image_widths[marker(keys[path])]) : code[0];
  });
  const questionCodes = q.image_paths.map(path => imageCode(keys[path], q.image_widths[marker(keys[path])])).join("\n");
  return { q, baseline: JSON.stringify([q, questionCodes]), existing, keys: {}, files: {}, questionCodes };
}

export const dirty = (draft: Draft) => draft.baseline !== JSON.stringify([draft.q, draft.questionCodes]);

export function finalQuestion(draft: Draft, keys: Record<string, string>) {
  const q = { ...draft.q };
  q.image_widths = applyImageWidths(q.analysis, q.analysis_image_paths, { ...keys, ...draft.keys }, q.image_widths);
  q.image_path = q.image_paths[0] ?? "";
  q.source = q.source.trim();
  for (const field of Object.keys(fields) as Field[]) q[field] = normalizeText(q[field]);
  q.options = q.options.map(normalizeText);
  return q;
}

type Attachment = { path: string; key: string; data: string };
async function prepare(sources: (File | string)[], id: string): Promise<Attachment[]> {
  return Promise.all(sources.map(async source => {
    const data = typeof source === "string" && source.startsWith("data:image/png;base64,") ? source : raster(await loadImage(source));
    const path = `images/${id.replace(/[^A-Za-z0-9_-]/g, "")}-${uid()}.png`;
    return { path, data, key: await keyForImage(path) };
  }));
}
function newAssets(rows: Attachment[]) {
  return { keys: Object.fromEntries(rows.map(r => [r.path, r.key])), files: Object.fromEntries(rows.map(r => [r.path, r.data])) };
}
function sourceFiles(event: ClipboardEvent | DragEvent) {
  const data = "clipboardData" in event ? event.clipboardData : event.dataTransfer;
  return [...data.files].filter(file => !file.type || file.type.startsWith("image/"));
}

export type MethodAction = (action: "add" | "rename" | "delete", name?: string) => void;

export function MethodTags({ library, selected, change, manage }: { library: string[]; selected: string[]; change: (tags: string[]) => void; manage: MethodAction }) {
  const [query, setQuery] = useState("");
  const [current, setCurrent] = useState("");
  return <>
    <div className="row">
      <button onClick={() => manage("add")}>新增</button>
      <button disabled={!library.includes(current)} onClick={() => manage("rename", current)}>编辑</button>
      <button disabled={!library.includes(current)} onClick={() => manage("delete", current)}>删除</button>
    </div>
    <input type="search" aria-label="搜索方法或思想" placeholder="搜索方法 / 思想" value={query} onChange={e => setQuery(e.target.value)} />
    <div className="method-list">{library.filter(tag => tag.toLowerCase().includes(query.trim().toLowerCase())).map(tag =>
      <div key={tag} className={current === tag ? "tag-row active" : "tag-row"} onClick={() => setCurrent(tag)}>
        <input type="checkbox" aria-label={tag} checked={selected.includes(tag)} onChange={() => change(toggleValue(selected, tag))} />
        <button className="text-button" onClick={() => setCurrent(tag)}>{tag}</button>
      </div>)}</div>
  </>;
}

export function Editor({ draft, update, keys, methods, manage, report, recognize }: {
  draft: Draft; update: Dispatch<SetStateAction<Draft | null>>; keys: Record<string, string>; methods: string[];
  manage: MethodAction; report: (error: unknown) => void; recognize: () => void;
}) {
  const q = draft.q;
  const context = { keys: { ...keys, ...draft.keys }, files: draft.files };
  const [showPreview, setShowPreview] = useState(true);
  const [tags, setTags] = useState(true);
  const [expanded, setExpanded] = useState(initialExpanded);
  const [full, setFull] = useState<Field | null>(null);
  const [draw, setDraw] = useState(false);
  const [gallerySelected, selectGallery] = useState({ question: "", analysis: "" });
  const refs = useRef<Partial<Record<Target, HTMLTextAreaElement | HTMLInputElement | null>>>({});
  const input = useRef<HTMLInputElement>(null);
  const set = (fields: Partial<Question>) => update(current => current && ({ ...current, q: { ...current.q, ...fields } }));

  const focus = (field: Target, offset: number) => {
    const el = refs.current[field];
    if (!el) return;
    el.focus();
    el.setSelectionRange(offset, offset);
  };

  function attach(rows: Attachment[], analysis = false, position?: number, selectionEnd = position) {
    if (!rows.length) return;
    const assets = newAssets(rows);
    update(current => {
      if (!current || current.q.id !== q.id) return current;
      const next = { ...current.q };
      const codes = rows.map(row => imageCode(row.key, analysis ? 68 : undefined)).join("\n");
      if (analysis) {
        const at = position ?? next.analysis.length;
        next.analysis = next.analysis.slice(0, at) + "\n" + codes + "\n" + next.analysis.slice(selectionEnd ?? at);
        next.analysis_image_paths = [...next.analysis_image_paths, ...rows.map(row => row.path)];
      } else {
        next.image_paths = [...next.image_paths, ...rows.map(row => row.path)];
      }
      return {
        ...current, q: next, keys: { ...current.keys, ...assets.keys }, files: { ...current.files, ...assets.files },
        questionCodes: analysis ? current.questionCodes : [current.questionCodes, codes].filter(Boolean).join("\n"),
      };
    });
    selectGallery(old => ({ ...old, [analysis ? "analysis" : "question"]: rows.at(-1)!.path }));
  }

  async function add(sources: (File | string)[], analysis = false, start?: number, end?: number) {
    try { attach(await prepare(sources, q.id), analysis, start, end); } catch (e) { report(e); }
  }
  async function paste(analysis: boolean) {
    const element = refs.current.analysis;
    const start = element?.selectionStart ?? q.analysis.length;
    const end = element?.selectionEnd ?? start;
    try { await add([await invoke<string>("recognition_paste_image")], analysis, start, end); }
    catch (e) { report(e); }
  }
  function handleImage(event: ClipboardEvent | DragEvent, analysis: boolean) {
    const files = sourceFiles(event);
    if (!files.length) return;
    event.preventDefault();
    const element = refs.current.analysis;
    void add(files, analysis, element?.selectionStart ?? undefined, element?.selectionEnd ?? undefined);
  }

  function gallery(analysis: boolean) {
    const paths = analysis ? q.analysis_image_paths : q.image_paths;
    const field = analysis ? "analysis" : "question";
    const selected = paths.includes(gallerySelected[field]) ? gallerySelected[field] : paths.at(-1);
    function remove() {
      if (!selected) return;
      update(current => {
        if (!current) return current;
        const next = { ...current.q, image_widths: { ...current.q.image_widths } };
        const field = analysis ? "analysis_image_paths" : "image_paths";
        const oldPaths = next[field];
        next[field] = oldPaths.filter(path => path !== selected);
        delete next.image_widths[marker(context.keys[selected!])];
        const removeCodes = (source: string) => source.replace(imagePattern(), (...parts) =>
          matchImage(parts.slice(0, 5), oldPaths, context.keys) === selected ? "" : parts[0]);
        if (analysis) next.analysis = removeCodes(next.analysis);
        const files = { ...current.files };
        delete files[selected!];
        return { ...current, q: next, files, questionCodes: analysis ? current.questionCodes : removeCodes(current.questionCodes).trim() };
      });
    }
    function insert() {
      if (!selected) return;
      const remove = (text: string) => text.replace(imagePattern(), (...parts) =>
        matchImage(parts.slice(0, 5), paths, context.keys) === selected ? "" : parts[0]);
      const pos = refs.current.analysis?.selectionStart ?? q.analysis.length;
      const index = remove(q.analysis.slice(0, pos)).length;
      const clean = remove(q.analysis);
      const code = "\n" + imageCode(context.keys[selected], q.image_widths[marker(context.keys[selected])] ?? 68) + "\n";
      set({ analysis: clean.slice(0, index) + code + clean.slice(index) });
      requestAnimationFrame(() => focus("analysis", index + code.length));
    }
    return <div className="gallery" tabIndex={analysis ? undefined : 0}
      onDragOver={e => e.preventDefault()} onDrop={e => handleImage(e, analysis)} onPaste={e => handleImage(e, analysis)}>
      {!analysis && <p className="muted">可拖入或粘贴多张图片。</p>}
      {!!paths.length && <div className="thumbnails">{paths.map((path, i) => <button key={path + i}
        className={path === selected ? "active" : ""} onClick={() => selectGallery(old => ({ ...old, [field]: path }))}>
        <QuestionImage path={path} context={context} thumb />图片 {i + 1}</button>)}</div>}
      <div className="row wrap">
        <button onClick={() => void paste(analysis)}>{analysis ? "粘贴解析图片" : "从剪贴板粘贴"}</button>
        {analysis ? <button disabled={!selected} onClick={insert}>将选中图片放到光标处</button> : <>
          <button onClick={() => input.current?.click()}>选择图片文件</button>
          <input hidden type="file" multiple accept="image/*,.tif,.tiff" ref={input} onChange={e => {
            void add([...(e.target.files ?? [])]); e.target.value = "";
          }} />
          <button onClick={() => setDraw(true)}>软件内画图</button>
        </>}
        <button disabled={!selected} onClick={remove}>{analysis ? "移除选中解析图片" : "移除配图"}</button>
      </div>
      {!analysis && !!paths.length && <textarea rows={3} aria-label="题图图片代码" value={draft.questionCodes}
        onChange={e => {
          const value = e.target.value;
          update(current => current && ({ ...current, questionCodes: value, q: { ...current.q,
            image_widths: applyImageWidths(value, current.q.image_paths, context.keys, current.q.image_widths) } }));
        }} />}
    </div>;
  }

  const preview = { ...q, image_widths: applyImageWidths(q.analysis, q.analysis_image_paths, context.keys, q.image_widths) };
  return <div className={showPreview ? "edit-grid" : "edit-grid no-preview"}>
    <main className="edit-form scroll">
      <section className="panel edit-meta">
        <label>题型<select value={q.question_type} onChange={e => set({ question_type: e.target.value })}>
          {questionTypes.map(type => <option key={type}>{type}</option>)}</select></label>
        <div><div className="muted">难度</div><Stars value={q.difficulty} onChange={difficulty => set({ difficulty })} /></div>
        <label>来源<input value={q.source} placeholder="可留空，例如 2025 年高考全国卷" onChange={e => set({ source: e.target.value })} /></label>
        <div className="muted">创建日期 <b>{q.created_at}</b></div>
      </section>
      {(Object.keys(fields) as Field[]).map(field => <section key={field} className="panel">
        <div className="section-heading"><h3>{fields[field]}</h3><span className="spacer" />
          {field === "stem" && <button onClick={recognize}>识别题目图片</button>}
          <button onClick={() => setFull(field)}>完整编辑</button></div>
        <textarea ref={element => { refs.current[field] = element; }} aria-label={fields[field]} rows={field === "stem" ? 6 : 4}
          placeholder={placeholder[field]} value={q[field]} onChange={e => set({ [field]: e.target.value })}
          onPaste={field === "analysis" ? e => handleImage(e, true) : undefined}
          onDragOver={field === "analysis" ? e => e.preventDefault() : undefined}
          onDrop={field === "analysis" ? e => handleImage(e, true) : undefined} />
        {field === "analysis" && gallery(true)}
        {field === "stem" && choice(q.question_type) && <div className="edit-options">
          {q.options.map((text, i) => <label key={i}>{options[i]}
            <input ref={element => { refs.current[`option_${i}`] = element; }} value={text} onChange={e =>
              set({ options: q.options.map((value, j) => i === j ? e.target.value : value) })} />
          </label>)}
        </div>}
      </section>)}
      <section className="panel"><div className="section-heading"><h3>题目配图</h3></div>{gallery(false)}</section>
      <section className="panel"><div className="section-heading"><h3>标签</h3><span className="spacer" />
        <button onClick={() => setTags(!tags)}>{tags ? "收起标签区" : "展开标签区"}</button></div>
        {tags && <div className="edit-tags">
          <div><h4>知识点标签</h4><KnowledgeTree selected={q.knowledge_tags} change={knowledge_tags => set({ knowledge_tags })}
            expanded={expanded} setExpanded={setExpanded} /></div>
          <div><h4>方法 / 思想标签</h4><MethodTags library={methods} selected={q.method_tags} change={method_tags => set({ method_tags })} manage={manage} /></div>
        </div>}
      </section>
    </main>
    <aside className="preview-pane scroll">
      <div className="section-heading"><b>实时预览</b><span className="spacer" /><button onClick={() => setShowPreview(!showPreview)}>{showPreview ? "隐藏预览" : "显示预览"}</button></div>
      {showPreview && <><p className="muted">⌘ / Ctrl + 点击可定位原文</p><QuestionContent q={preview} context={context} details onJump={focus} /></>}
    </aside>
    {full && <FullEditor field={full} q={q} context={context} close={() => setFull(null)} report={report} save={(text, rows) => {
      const assets = newAssets(rows);
      update(current => current && ({ ...current, files: { ...current.files, ...assets.files }, keys: { ...current.keys, ...assets.keys },
        q: { ...current.q, [full]: text, analysis_image_paths: [...current.q.analysis_image_paths, ...rows.map(row => row.path)] } }));
      setFull(null);
    }} />}
    {draw && <Drawing close={() => setDraw(false)} use={png => { setDraw(false); void add([png]); }} />}
  </div>;
}

function FullEditor({ field, q, context, save, close, report }: {
  field: Field; q: Question; context: ImageContext; save: (text: string, images: Attachment[]) => void; close: () => void; report: (error: unknown) => void;
}) {
  const [text, setText] = useState(q[field]);
  const [added, setAdded] = useState<Attachment[]>([]);
  const [reference, setReference] = useState(false);
  const [query, setQuery] = useState("");
  const editor = useRef<HTMLTextAreaElement>(null);
  const assets = newAssets(added);
  const localContext = { keys: { ...context.keys, ...assets.keys }, files: { ...context.files, ...assets.files } };
  function select(start: number, end = start) {
    requestAnimationFrame(() => { editor.current?.focus(); editor.current?.setSelectionRange(start, end); });
  }
  function insert(code: string, fallback = "x") {
    const start = editor.current?.selectionStart ?? text.length;
    const end = editor.current?.selectionEnd ?? start;
    const selected = text.slice(start, end).replace(/^\$([\s\S]*)\$$/, "$1") || fallback;
    const math = text.slice(0, start).match(/(?<!\\)\$\$?/g) ?? [];
    let delimiter = "";
    for (const item of math) {
      if (!delimiter) delimiter = item;
      else if (delimiter === item) delimiter = "";
    }
    const wrap = !delimiter && code !== "@" && !code.startsWith("$") ? "$" : "";
    const content = wrap + code.replace("__SEL__", selected) + wrap;
    setText(text.slice(0, start) + content + text.slice(end));
    const offset = code.indexOf("__SEL__");
    if (offset < 0) select(start + content.length);
    else select(start + offset + wrap.length, start + offset + wrap.length + selected.length);
  }
  async function attach(e: ClipboardEvent | DragEvent) {
    if (field !== "analysis") return;
    const sources = sourceFiles(e);
    if (!sources.length) return;
    e.preventDefault();
    const start = editor.current?.selectionStart ?? text.length;
    const end = editor.current?.selectionEnd ?? start;
    try {
      const rows = await prepare(sources, q.id);
      const insertion = "\n" + rows.map(row => imageCode(row.key, 68)).join("\n") + "\n";
      setAdded(old => [...old, ...rows]);
      setText(old => old.slice(0, start) + insertion + old.slice(end));
      select(start + insertion.length);
    } catch (e) { report(e); }
  }
  return <Modal title={`${fields[field]} · 完整编辑`} wide onClose={close}>
    <div className="modal-content">
      {(field === "answer" || field === "analysis") && <div className="context-preview"><QuestionContent q={q} context={context} details={false} /></div>}
      <div className="row wrap">{catalog.tools.map(tool => <button key={tool.label} onClick={() => insert(tool.code, tool.default)}>{tool.label}</button>)}
        <button onClick={() => setReference(true)}>LaTeX 参考</button></div>
      <div className="full-edit-grid">
        <textarea aria-label={fields[field]} ref={editor} value={text} onChange={e => setText(e.target.value)}
          onPaste={e => void attach(e)} onDragOver={field === "analysis" ? e => e.preventDefault() : undefined} onDrop={e => void attach(e)} />
        <div className="question-text scroll">{field === "analysis" ?
          <Analysis q={{ ...q, analysis: text, analysis_image_paths: [...q.analysis_image_paths, ...added.map(row => row.path)] }}
            context={localContext} onJump={select} /> : <MathText text={text} onJump={select} />}</div>
      </div>
    </div>
    <footer className="dialog-footer"><button onClick={close}>取消</button><button className="primary" onClick={() => save(text, added)}>应用修改</button></footer>
    {reference && <Modal title="LaTeX 参考" onClose={() => setReference(false)}>
      <div className="modal-content"><input type="search" placeholder="搜索符号或命令" value={query} onChange={e => setQuery(e.target.value)} />
        <div className="reference-list scroll">{Object.entries(catalog.groups).map(([group, rows]) => {
          const matched = rows.filter(([name, code]) => (name + code).toLowerCase().includes(query.toLowerCase()));
          return matched.length ? <section key={group}><h4>{group}</h4>{matched.map(([name, code]) => <div className="row" key={name}>
            <span>{name}</span><code>{code}</code><span className="spacer" />
            <button onClick={() => void navigator.clipboard.writeText(code).catch(report)}>复制</button>
            <button onClick={() => { setReference(false); insert(code); }}>插入</button></div>)}</section> : null;
        })}</div>
      </div>
    </Modal>}
  </Modal>;
}

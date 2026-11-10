import { useEffect, useMemo, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { invoke } from "@tauri-apps/api/core";
import { getCurrentWindow } from "@tauri-apps/api/window";
import {
  basketQuestions, chapterNodes, currentBasket, descendants, errorText, filteredQuestions, newFilters,
  newQuestion, questionTypes, sorts, title, toggleValue, uid,
  type Basket, type ImageContext, type ImageFiles, type Library, type Question, type Snapshot, type Sort,
} from "./domain";
import { MathText, QuestionContent } from "./math";
import { Check, KnowledgeTree, Modal, Stars, type Ask } from "./ui";
import { Editor, dirty, finalQuestion, initialExpanded, makeDraft, MethodTags, type Draft, type MethodAction } from "./editor";
import { Recognition } from "./recognition";
import { ExportDialog } from "./export";
import "katex/dist/katex.min.css";
import "./style.css";

type AskRequest = Parameters<Ask>[0];
type AskState = AskRequest & { finish: (value?: string) => void };

function Prompt({ request }: { request: AskState }) {
  const [value, setValue] = useState(request.initial ?? "");
  return <Modal title={request.title} onClose={() => request.finish()}>
    <form onSubmit={e => { e.preventDefault(); request.finish(request.initial === undefined ? request.buttons[0][0] : value); }}>
      <div className="modal-content">
        {request.text && <p>{request.text}</p>}
        {request.initial !== undefined && <input autoFocus aria-label="名称" value={value} onChange={e => setValue(e.target.value)} />}
      </div>
      <footer className="dialog-footer">
        <button type="button" onClick={() => request.finish()}>取消</button>
        {request.buttons.map(([key, label], i) => <button type="button" key={key} className={i === 0 ? "primary" : ""}
          onClick={() => request.finish(request.initial === undefined ? key : value)}>{label}</button>)}
      </footer>
    </form>
  </Modal>;
}

function App() {
  const [snapshot, setSnapshot] = useState<Snapshot>();
  const [draft, setDraft] = useState<Draft | null>(null);
  const [view, setView] = useState<"browse" | "overview">("browse");
  const [filters, setFilters] = useState(newFilters);
  const [query, setQuery] = useState("");
  const [expanded, setExpanded] = useState(initialExpanded);
  const [detail, setDetail] = useState<string[]>([]);
  const [prompt, setPrompt] = useState<AskState>();
  const [recognition, setRecognition] = useState(false);
  const [baskets, setBaskets] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState("");
  const working = useRef(false);
  const asks = useRef(false);
  const closeCheck = useRef<() => Promise<boolean>>(async () => true);
  const newAction = useRef<() => void>(() => {});
  const scroll = useRef<HTMLElement>(null);
  const report = (value: unknown) => setStatus(errorText(value));

  useEffect(() => {
    void invoke<Snapshot>("load_library").then(setSnapshot, report);
    const window = getCurrentWindow();
    let stop: (() => void) | undefined, disposed = false;
    void window.onCloseRequested(async event => {
      event.preventDefault();
      if (await closeCheck.current()) await window.destroy().catch(report);
    }).then(unlisten => { if (disposed) unlisten(); else stop = unlisten; }).catch(report);
    function key(e: KeyboardEvent) {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "n") {
        e.preventDefault();
        newAction.current();
      }
    }
    document.addEventListener("keydown", key);
    return () => { disposed = true; stop?.(); document.removeEventListener("keydown", key); };
  }, []);

  const ask: Ask = request => {
    if (asks.current) return Promise.resolve(undefined);
    asks.current = true;
    return new Promise(resolve => setPrompt({ ...request, finish: value => {
      asks.current = false;
      setPrompt(undefined);
      resolve(value);
    } }));
  };
  const confirm = async (title: string, text: string) => await ask({ title, text, buttons: [["yes", "确定"]] }) === "yes";
  const name = async (title: string, initial = "") => (await ask({ title, initial, buttons: [["apply", "确定"]] }))?.trim();
  const lib = snapshot?.library;
  const basket = lib && currentBasket(lib);
  const results = useMemo(() => lib ? filteredQuestions(lib.questions, filters) : [], [lib, filters]);
  const exportQuestions = useMemo(() => lib ? basketQuestions(lib) : [], [lib]);
  const context: ImageContext = { keys: snapshot?.image_keys ?? {} };

  async function perform<T>(work: () => Promise<T>) {
    if (working.current) return;
    working.current = true;
    setBusy(true);
    try { return await work(); } catch (e) { report(e); }
    finally { working.current = false; setBusy(false); }
  }
  async function mutate(change: (library: Library) => void, images: ImageFiles = {}) {
    if (!snapshot || working.current) return false;
    return await perform(async () => {
      const library = structuredClone(snapshot.library);
      change(library);
      const next = await invoke<Snapshot>("save_library", { revision: snapshot.revision, library, images });
      setSnapshot(next);
      setStatus("已保存到题库。");
      return true;
    }) === true;
  }
  async function saveDraft() {
    if (!draft || !snapshot) return false;
    if (!draft.q.stem.trim()) { report("请先填写题干后再保存。"); return false; }
    const q = finalQuestion(draft, snapshot.image_keys);
    if (!await mutate(library => {
      const index = library.questions.findIndex(item => item.id === q.id);
      if (index >= 0) library.questions[index] = q;
      else library.questions.push(q);
    }, draft.files)) return false;
    setDraft(null);
    setView("browse");
    return true;
  }

  async function canLeave() {
    if (working.current || exporting || recognition || asks.current) return false;
    if (!draft || !dirty(draft)) return true;
    const action = await ask({ title: "未保存的更改", text: "这道题有尚未保存的修改。",
      buttons: [["save", "保存并离开"], ["discard", "放弃修改"]] });
    return action === "discard" || action === "save" && await saveDraft();
  }
  async function navigate(next: "browse" | "overview", q?: Question, existing = false) {
    if (!await canLeave()) return;
    setView(next);
    setDraft(q ? makeDraft(q, context.keys, existing) : null);
  }
  closeCheck.current = canLeave;
  newAction.current = () => { if (!baskets) void navigate("browse", newQuestion()); };

  async function applyRecognition(text: string) {
    setRecognition(false);
    if (!draft) {
      const next = makeDraft(newQuestion(), context.keys, false);
      next.q.stem = text;
      setDraft(next);
      return;
    }
    if (draft.q.stem.trim() && !await confirm("替换题干", "将校对后的识别文本填入当前题干？")) return;
    setDraft(current => current && ({ ...current, q: { ...current.q, stem: text } }));
  }

  const manage: MethodAction = (action, old = "") => {
    void (async () => {
      const next = action === "delete" ? "" : await name(action === "add" ? "新增方法标签" : "编辑方法标签", old);
      if (action !== "delete" && (!next || next === old)) return;
      if (action === "delete" && !await confirm("删除方法标签", `删除“${old}”并从所有题目中移除吗？`)) return;
      if (action === "rename" && lib?.method_tag_library.includes(next!)) { report("这个标签名称已存在。"); return; }
      const changed = (tags: string[]) => action === "add" ? [...new Set([...tags, next!])] :
        tags.flatMap(tag => tag === old ? next ? [next] : [] : [tag]);
      if (await mutate(library => {
        library.method_tag_library = changed(library.method_tag_library);
        if (action !== "add") for (const q of library.questions) q.method_tags = changed(q.method_tags);
      })) {
        setDraft(current => current && ({ ...current, q: { ...current.q, method_tags: changed(current.q.method_tags) } }));
        if (action !== "add") setFilters(current => ({ ...current, methods: changed(current.methods) }));
      }
    })();
  };

  async function restore() {
    if (!await canLeave()) return;
    setDraft(null);
    const mode = await ask({ title: "恢复题库", text: "合并按题目 ID 更新记录并保留试用库的其余内容。覆盖将替换试用库中的题目、标签和试题篮。",
      buttons: [["merge", "合并到题库"], ["replace", "覆盖题库"]] });
    if (!mode || mode === "replace" && !await confirm("最终确认覆盖", "覆盖题库后无法撤销。确定继续？")) return;
    await perform(async () => {
      const next = await invoke<Snapshot | null>("restore_library", { merge: mode === "merge" });
      if (!next) return;
      setSnapshot(next);
      setDetail([]);
      report(`恢复完成，共 ${next.library.questions.length} 道题。`);
    });
  }

  function updateFilters(change: Partial<typeof filters>) {
    setFilters(old => ({ ...old, ...change }));
    if (scroll.current) scroll.current.scrollTop = 0;
  }
  const summary = [
    filters.text.trim() && `搜索“${filters.text.trim()}”`, filters.types.length !== 4 && `题型 ${filters.types.join("、") || "未选择"}`,
    filters.knowledge.length > 0 && "知识点", filters.methods.length > 0 && `方法 / 思想 ${filters.methods.length} 项`,
  ].filter(Boolean).join(" · ");
  const switchBasket = (id: string) => { void mutate(library => { library.active_basket_id = id; }); };
  const removeQuestion = async (id: string) => {
    if (!await confirm("确认删除", "确定删除题库中的这道题吗？")) return;
    if (await mutate(library => {
      library.questions = library.questions.filter(q => q.id !== id);
      for (const basket of library.baskets) basket.question_ids = basket.question_ids.filter(item => item !== id);
    })) setDetail(current => current.filter(item => item !== id));
  };

  return <div className="app">
    <header className="topbar">
      <strong>高中数学题目管理器</strong>
      <button className={view === "browse" && !draft ? "active" : ""} onClick={() => void navigate("browse")}>题库</button>
      <button className={view === "overview" && !draft ? "active" : ""} onClick={() => void navigate("overview")}>题库概览</button>
      <span className="spacer" />
      <button disabled={!lib || busy} onClick={() => void perform(async () => {
        const saved = await invoke<string | null>("backup_library");
        if (saved) report(`题库已备份到 ${saved}`);
      })}>备份题库</button>
      <button disabled={!lib || busy} onClick={() => void restore()}>恢复题库</button>
      <button disabled={!lib || busy} onClick={() => setRecognition(true)}>识别题目图片</button>
      <button className="primary" disabled={!lib || busy} onClick={() => newAction.current()}>＋ 新建题目</button>
    </header>
    {!snapshot || !lib || !basket ? <div className="empty">{status || "正在打开题库…"}</div> : draft ? <>
      <div className="toolbar">
        <b>{draft.existing ? "编辑题目" : "新建题目"}</b>
        {dirty(draft) && <span className="muted">尚未保存</span>}
        <span className="spacer" />
        <button disabled={busy} onClick={() => void navigate("browse")}>取消并返回</button>
        <button disabled={busy} className="primary" onClick={() => void saveDraft()}>保存并返回浏览</button>
      </div>
      <Editor key={draft.q.id} draft={draft} update={setDraft} keys={snapshot.image_keys} methods={lib.method_tag_library}
        manage={manage} report={report} recognize={() => setRecognition(true)} />
    </> : view === "overview" ? <main className="overview scroll">
      <div className="section-heading"><h1>题库概览</h1><span className="spacer" /><button onClick={() => setView("browse")}>返回题库</button></div>
      <div className="metric"><strong>{lib.questions.length}</strong><span>题目总数</span></div>
      <div className="overview-grid">
        <section className="panel"><h2>知识章节题量</h2>{chapterNodes.map(chapter => {
          const count = lib.questions.filter(q => q.knowledge_tags.some(tag => tag.startsWith(chapter.path))).length;
          return count > 0 && <button className="overview-row" key={chapter.path} onClick={() => {
            setFilters({ ...newFilters(), sort: filters.sort, knowledge: descendants(chapter) }); setQuery(""); setView("browse");
          }}><span>{chapter.path}</span><b>{count}</b></button>;
        })}</section>
        <section className="panel"><h2>近期新增</h2>{[...lib.questions].sort((a, b) => b.created_at.localeCompare(a.created_at)).slice(0, 6).map(q =>
          <article className="recent" key={q.id}><time>{q.created_at}</time><div className="question-text"><MathText text={q.stem} /></div></article>)}</section>
      </div>
    </main> : <>
      <div className="toolbar browse-toolbar">
        <input aria-label="全文检索" type="search" placeholder="搜索题干、答案、解析、备注或标签" value={filters.text} onChange={e => updateFilters({ text: e.target.value })} />
        <select aria-label="当前试题篮" value={basket.id} disabled={busy} onChange={e => switchBasket(e.target.value)}>
          {lib.baskets.map(b => <option key={b.id} value={b.id}>{b.name}（{b.question_ids.length}）</option>)}
        </select>
        <button disabled={busy} onClick={() => setBaskets(true)}>管理试题篮</button>
        <button disabled={!exportQuestions.length || busy} className="primary" onClick={() => setExporting(true)}>导出当前篮子</button>
      </div>
      <div className="workspace">
        <aside className="sidebar scroll">
          <section>
            <h2>标签检索</h2><p className="muted">同组取并集，知识点与方法标签之间取交集。</p>
            <div className="row">
              <button onClick={() => updateFilters({ knowledge: [], methods: [] })}>清空标签</button>
              <button onClick={() => setExpanded(initialExpanded)}>展开全部</button>
            </div>
          </section>
          <section>
            <h3>知识点</h3>
            <input type="search" aria-label="搜索知识点" placeholder="搜索教材、章节或知识点" value={query} onChange={e => setQuery(e.target.value)} />
            <KnowledgeTree selected={filters.knowledge} change={knowledge => updateFilters({ knowledge })} cascade
              query={query} expanded={expanded} setExpanded={setExpanded} />
          </section>
          <section><h3>方法 / 思想</h3>
            <MethodTags library={lib.method_tag_library} selected={filters.methods} change={methods => updateFilters({ methods })} manage={manage} />
          </section>
        </aside>
        <main className="browse">
          <div className="toolbar filter-bar">
            <span>题型</span>{questionTypes.map(type => <Check key={type} value={filters.types.includes(type)} onChange={() =>
              updateFilters({ types: toggleValue(filters.types, type) })}>{type}</Check>)}
            <span className="spacer" /><span>排序</span>
            <select aria-label="题目排序" value={filters.sort} onChange={e => updateFilters({ sort: e.target.value as Sort })}>
              {Object.entries(sorts).map(([value, label]) => <option value={value} key={value}>{label}</option>)}
            </select>
          </div>
          <div className="result-line"><b>当前显示 {results.length} 道题，共 {lib.questions.length} 道题</b><span className="muted">{summary || "全部题目"}</span></div>
          <section className="cards scroll" ref={scroll}>
            {!results.length && <div className="empty">没有符合当前筛选条件的题目。</div>}
            {results.map((q, i) => <article className="question-card" key={q.id}>
              <header className="card-header">
                <span className="number">{i + 1}.</span><span className="type">{q.question_type}</span>
                <div className="tags">{[...q.knowledge_tags, ...q.method_tags].map((tag, j) => <span key={tag + j}>{tag}</span>)}</div>
                <div className="row card-actions">
                  <button onClick={() => setDetail(old => toggleValue(old, q.id))}>{detail.includes(q.id) ? "收起详情" : "显示详情"}</button>
                  <label className="check"><input type="checkbox" checked={basket.question_ids.includes(q.id)} disabled={busy} onChange={() =>
                    void mutate(library => { const b = currentBasket(library); b.question_ids = toggleValue(b.question_ids, q.id); })} />
                    {basket.question_ids.includes(q.id) ? `移出 ${basket.name}` : `加入 ${basket.name}`}</label>
                  <button disabled={busy} onClick={() => void navigate("browse", q, true)}>编辑</button>
                  <button disabled={busy} className="danger" onClick={() => void removeQuestion(q.id)}>删除</button>
                </div>
              </header>
              <div className="metadata"><span>难度 <Stars value={q.difficulty} /></span>
                <span>来源 {q.source || "未填写来源"}</span><span>创建 {q.created_at}</span></div>
              <QuestionContent q={q} context={context} details={detail.includes(q.id)} />
            </article>)}
          </section>
        </main>
      </div>
    </>}
    <footer className="statusbar"><span role="status">{status || "题库已打开"}</span><span title={snapshot?.location}>{snapshot?.location}</span></footer>
    {recognition && <Recognition close={() => setRecognition(false)} use={text => void applyRecognition(text)} />}
    {baskets && lib && <BasketDialog library={lib} busy={busy} ask={ask} mutate={mutate} report={report}
      close={() => setBaskets(false)} openExport={() => { setBaskets(false); setExporting(true); }} />}
    {exporting && basket && <ExportDialog questions={exportQuestions} context={context} basketName={basket.name} close={() => setExporting(false)} report={report} />}
    {prompt && <Prompt request={prompt} />}
  </div>;
}

function BasketDialog({ library, busy, ask, mutate, report, close, openExport }: {
  library: Library; busy: boolean; ask: Ask; mutate: (change: (library: Library) => void) => Promise<boolean>;
  report: (value: unknown) => void; close: () => void; openExport: () => void;
}) {
  const basket = currentBasket(library);
  const questions = basketQuestions(library);
  const [selected, setSelected] = useState<string[]>([]);
  async function change(action: "create" | "rename" | "delete") {
    if (action === "delete") {
      const result = await ask({ title: "删除试题篮", text: `确定删除“${basket.name}”？篮中的题目仍保留在题库中。`,
        buttons: [["yes", "删除"]] });
      if (result !== "yes") return;
      await mutate(next => {
        next.baskets = next.baskets.filter(b => b.id !== basket.id);
        next.active_basket_id = next.baskets[0]?.id ?? "";
      });
      return;
    }
    const name = (await ask({ title: action === "create" ? "新建试题篮" : "重命名试题篮",
      initial: action === "create" ? "" : basket.name, buttons: [["apply", "确定"]] }))?.trim();
    if (!name || action === "rename" && name === basket.name) return;
    if (library.baskets.some(b => b.name === name)) { report("试题篮名称已存在。"); return; }
    await mutate(next => {
      if (action === "create") {
        const basket: Basket = { id: uid(), name, question_ids: [] };
        next.baskets.push(basket);
        next.active_basket_id = basket.id;
      } else currentBasket(next).name = name;
    });
  }
  return <Modal title="管理试题篮" onClose={close}>
    <div className="modal-content">
      <select aria-label="选择试题篮" value={basket.id} disabled={busy} onChange={e => {
        const id = e.target.value; setSelected([]); void mutate(next => { next.active_basket_id = id; });
      }}>{library.baskets.map(b => <option value={b.id} key={b.id}>{b.name}</option>)}</select>
      <p>“{basket.name}”中共有 {questions.length} 道题。</p>
      <div className="basket-items scroll">{questions.map((q, i) => <Check key={q.id} value={selected.includes(q.id)} onChange={() =>
        setSelected(old => toggleValue(old, q.id))}>{title(q, i)}</Check>)}</div>
      <div className="row wrap">
        <button disabled={busy} onClick={() => void change("create")}>新建试题篮</button>
        <button disabled={busy} onClick={() => void change("rename")}>重命名</button>
        <button disabled={busy} onClick={() => void change("delete")}>删除当前篮子</button>
        <button disabled={busy || !questions.some(q => selected.includes(q.id))} onClick={() => void mutate(next => {
          const b = currentBasket(next); b.question_ids = b.question_ids.filter(id => !selected.includes(id)); setSelected([]);
        })}>移除选中题目</button>
      </div>
    </div>
    <footer className="dialog-footer"><button onClick={close}>关闭</button>
      <button disabled={!questions.length || busy} className="primary" onClick={openExport}>导出题目篮</button></footer>
  </Modal>;
}

createRoot(document.getElementById("root")!).render(<App />);

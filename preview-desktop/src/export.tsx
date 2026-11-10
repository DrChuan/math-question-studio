import { useEffect, useMemo, useRef, useState, type MouseEvent } from "react";
import { invoke } from "@tauri-apps/api/core";
import { type ImageContext, type Question, codeWidth, errorText, imagePattern, marker, matchImage, title } from "./domain";
import { normalizeFormula, segments, QuestionImage } from "./math";
import { Check, Modal } from "./ui";

type Settings = { title: string; include_analysis: boolean; include_note: boolean; leave_solution_space: boolean };
type Location = { index: number; page: number; top: number };
type Preview = { ticket: string; pages: string[]; locations: Location[]; pdf?: string };
type Widths = Record<string, Record<string, number | null>>;

export function ExportDialog({ questions, context, basketName, close, report }: {
  questions: Question[]; context: ImageContext; basketName: string; close: () => void; report: (text: unknown) => void;
}) {
  const [settings, setSettings] = useState<Settings>({ title: "高中数学题目", include_analysis: false, include_note: false, leave_solution_space: false });
  const [started, setStarted] = useState(false);
  const [order, setOrder] = useState(() => questions.map(q => q.id));
  const [selected, setSelected] = useState(questions[0]?.id ?? "");
  const [selectedImage, setSelectedImage] = useState("");
  const [gaps, setGaps] = useState<Record<string, number>>({});
  const [widths, setWidths] = useState<Widths>({});
  const [rendered, setRendered] = useState<{ preview: Preview; order: string[]; signature: string }>();
  const [status, setStatus] = useState("");
  const [saving, setSaving] = useState(false);
  const pending = useRef<Promise<unknown>>(Promise.resolve());
  const drag = useRef("");
  const view = useRef<HTMLDivElement>(null);
  const pages = useRef<(HTMLDivElement | null)[]>([]);

  const math = useMemo(() => {
    const entries: Record<string, string> = Object.create(null);
    for (const question of questions) for (const text of [question.stem, question.answer, question.analysis, question.note, ...question.options]) {
      for (const part of segments(text)) if (part.math) entries[part.text] = normalizeFormula(part.text);
    }
    return entries;
  }, [questions]);
  const signature = JSON.stringify([settings, order, gaps, widths]);
  useEffect(() => {
    if (!started) return;
    let current = true;
    const timer = setTimeout(() => {
      setStatus("正在编译排版…");
      const compile = async () => {
        if (!current) return;
        try {
          const preview = await invoke<Preview>("render_export", { spec: { ...settings, question_ids: order, gaps, widths, math } });
          if (current) {
            setRendered({ preview, order, signature });
            setStatus(preview.pages.length ? `共 ${order.length} 道题 · ${preview.pages.length} 页` :
              "PDF 已生成。安装 Ghostscript 或 Poppler 可使用题目双向定位。");
          }
        } catch (e) { if (current) setStatus(errorText(e)); }
      };
      pending.current = pending.current.then(compile);
    }, 320);
    return () => { current = false; clearTimeout(timer); };
  }, [signature, started, math]);

  const preview = rendered?.preview;
  const pdf = preview?.pdf;
  const [pdfUrl, setPdfUrl] = useState("");
  useEffect(() => {
    setPdfUrl("");
    if (!pdf) return;
    const bytes = Uint8Array.from(atob(pdf), ch => ch.charCodeAt(0));
    const url = URL.createObjectURL(new Blob([bytes], { type: "application/pdf" }));
    setPdfUrl(url);
    return () => URL.revokeObjectURL(url);
  }, [pdf]);

  const q = questions.find(q => q.id === selected);
  const imagePaths = q ? [...q.image_paths, ...(settings.include_analysis ? q.analysis_image_paths : [])] : [];
  const path = imagePaths.includes(selectedImage) ? selectedImage : imagePaths[0];
  const imageKey = path && marker(context.keys[path]);
  const originalWidth = path && q ? [...q.analysis.matchAll(imagePattern())]
    .filter(code => matchImage(code, q.analysis_image_paths, context.keys) === path).map(codeWidth).at(-1) ?? q.image_widths[imageKey] : undefined;
  const override = imageKey && widths[selected] ? widths[selected][imageKey] : undefined;
  const width = override === undefined ? originalWidth : override;

  function jump(id: string) {
    if (!rendered) return;
    const point = rendered.preview.locations.find(loc => loc.index === rendered.order.indexOf(id));
    if (!point) return;
    const page = pages.current[point.page];
    if (!page || !view.current) return;
    view.current.scrollTo({ top: page.offsetTop + point.top * page.offsetHeight - 18, behavior: "smooth" });
  }
  useEffect(() => { if (rendered) jump(selected); }, [rendered]);

  function clickPage(e: MouseEvent<HTMLDivElement>, page: number) {
    if (!rendered) return;
    const bounds = e.currentTarget.getBoundingClientRect();
    const y = (e.clientY - bounds.top) / bounds.height;
    const loc = rendered.preview.locations.filter(location => location.page < page || location.page === page && location.top <= y).at(-1);
    const id = loc && rendered.order[loc.index];
    if (id) setSelected(id);
  }
  function move(id: string, target: number) {
    if (!id || target < 0 || target >= order.length || !order.includes(id)) return;
    const next = order.filter(item => item !== id);
    next.splice(target, 0, id);
    setOrder(next);
    setSelected(id);
  }
  function resize(next: number | null) {
    if (!imageKey || !selected) return;
    setWidths({ ...widths, [selected]: { ...widths[selected], [imageKey]: next === null ? null : Math.max(10, Math.min(100, next)) } });
  }
  async function save() {
    if (!preview || rendered?.signature !== signature || saving) return;
    setSaving(true);
    try {
      const path = await invoke<string | null>("save_export", { ticket: preview.ticket, name: basketName });
      if (path) { close(); report(`已导出 LaTeX 和 PDF · ${path}`); }
    } catch (e) { setStatus(errorText(e)); }
    finally { setSaving(false); }
  }

  return <Modal title={started ? `预览排版 · ${basketName}` : "导出选项"} wide={started} onClose={close}>
    {!started ? <div className="modal-content">
      <label>标题<input value={settings.title} onChange={e => setSettings({ ...settings, title: e.target.value })} /></label>
      {([["include_analysis", "导出答案和解析"], ["include_note", "导出备注"], ["leave_solution_space", "为解答题留答题空白"]] as const)
        .map(([key, label]) => <Check key={key} value={settings[key]} onChange={() => setSettings({ ...settings, [key]: !settings[key] })}>{label}</Check>)}
    </div> : <>
      <p className="export-status" role="status">{status || "正在准备排版…"}</p>
      <div className="export-grid">
        <div className="export-side scroll">
          <div className="row"><button disabled={order.indexOf(selected) <= 0} onClick={() => move(selected, order.indexOf(selected) - 1)}>上移</button>
            <button disabled={order.indexOf(selected) >= order.length - 1} onClick={() => move(selected, order.indexOf(selected) + 1)}>下移</button></div>
          <div className="export-order scroll">{order.map((id, i) => {
            const q = questions.find(q => q.id === id)!;
            return <button key={id} className={id === selected ? "active" : ""} draggable
              onDragStart={e => { drag.current = id; e.dataTransfer.setData("text/plain", id); }}
              onDragOver={e => e.preventDefault()} onDrop={e => { e.preventDefault(); move(drag.current, i); drag.current = ""; }}
              onClick={() => { setSelected(id); jump(id); }}>{title(q, i)}</button>;
          })}</div>
          {selected && <label>与下一题的额外留白（mm）
            <input type="number" min="0" max="250" step="0.5" value={gaps[selected] ?? 0} disabled={order.at(-1) === selected}
              onChange={e => setGaps({ ...gaps, [selected]: Math.min(250, Math.max(0, Number(e.target.value))) })} /></label>}
          {!!imagePaths.length && <>
            <h4>插图宽度</h4><div className="thumbnails">{imagePaths.map((path, i) => <button key={path + i}
              className={path === selectedImage ? "active" : ""} onClick={() => setSelectedImage(path)}>
              <QuestionImage path={path} context={context} thumb />{q?.image_paths.includes(path) ? "题图" : "解析图"} {i + 1}
            </button>)}</div>
            <div className="row"><input type="range" aria-label="图片宽度" min="10" max="100" value={width ?? 68} onChange={e => resize(Number(e.target.value))} />
              <input type="number" aria-label="宽度百分比" min="10" max="100" value={width ?? 68} onChange={e => resize(Number(e.target.value))} />%</div>
            <button onClick={() => resize(null)}>恢复自动大小</button>
          </>}
        </div>
        <div className="pdf-scroll scroll" ref={view}>
          {preview?.pages.map((src, page) => {
            const locations = preview.locations;
            const index = locations.findIndex(loc => rendered?.order[loc.index] === selected);
            const start = locations[index], end = locations[index + 1];
            const highlight = start && start.page <= page && (!end || end.page >= page);
            const top = start?.page === page ? start.top : 0.08;
            const bottom = end?.page === page ? end.top : 0.92;
            return <div key={page} className="pdf-page" ref={element => { pages.current[page] = element; }}
              onClick={e => clickPage(e, page)}>
              <img alt={`PDF 第 ${page + 1} 页`} src={src} />
              {highlight && bottom > top && <span className="pdf-highlight" style={{ top: `${top * 100}%`, height: `${(bottom - top) * 100}%` }} />}
            </div>;
          })}
          {pdfUrl && <iframe title="PDF 排版预览" src={pdfUrl} />}
        </div>
      </div>
    </>}
    <footer className="dialog-footer"><button onClick={close}>取消</button>
      {started ? <button className="primary" disabled={saving || rendered?.signature !== signature} onClick={() => void save()}>导出 LaTeX 和 PDF</button> :
        <button className="primary" onClick={() => setStarted(true)}>下一步：预览排版</button>}
    </footer>
  </Modal>;
}

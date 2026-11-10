import { useRef, useState, type PointerEvent } from "react";
import { invoke } from "@tauri-apps/api/core";
import { errorText, loadImage, raster } from "./domain";
import { MathText } from "./math";
import { Modal } from "./ui";
import { textFromOcr, type Region } from "./ocr-text";

type Settings = { region: string; workspace: string; ocrModel: string; configured: boolean };
type Box = [number, number, number, number];

export function Recognition({ close, use }: { close: () => void; use: (text: string) => void }) {
  const [image, setImage] = useState<HTMLImageElement>();
  const [crop, setCrop] = useState<Box>();
  const [pickingCrop, setPickingCrop] = useState(false);
  const [regions, setRegions] = useState<Region[]>([]);
  const [text, setText] = useState("");
  const [original, setOriginal] = useState("");
  const [warnings, setWarnings] = useState<string[]>([]);
  const [preview, setPreview] = useState(false);
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(false);
  const [status, setStatus] = useState("");
  const [settings, setSettings] = useState<Settings>();
  const [key, setKey] = useState("");
  const generation = useRef(0);
  const start = useRef<[number, number] | null>(null);

  const dismiss = () => { generation.current++; close(); };
  async function pick(clipboard: boolean) {
    if (busy || loading) return;
    const ticket = ++generation.current;
    setLoading(true);
    setStatus("");
    try {
      const data = await invoke<string | null>(clipboard ? "recognition_paste_image" : "recognition_choose_image");
      if (!data) return;
      const decoded = await loadImage(data);
      if (decoded.naturalWidth > 12000 || decoded.naturalHeight > 12000 || decoded.naturalWidth * decoded.naturalHeight > 16_000_000)
        throw Error("图片过大，请先裁剪题干");
      if (ticket !== generation.current) return;
      setImage(decoded);
      setCrop(undefined);
      setRegions([]);
      setText("");
      setOriginal("");
      setWarnings([]);
      setPickingCrop(false);
    } catch (e) { if (ticket === generation.current) setStatus(errorText(e)); }
    finally { if (ticket === generation.current) setLoading(false); }
  }

  function move(e: PointerEvent<HTMLDivElement>) {
    if (!start.current || !image) return;
    const rect = e.currentTarget.getBoundingClientRect();
    const x = Math.round(Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width)) * image.naturalWidth);
    const y = Math.round(Math.max(0, Math.min(1, (e.clientY - rect.top) / rect.height)) * image.naturalHeight);
    const [a, b] = start.current;
    setCrop([Math.min(a, x), Math.min(b, y), Math.abs(a - x), Math.abs(b - y)]);
  }

  async function recognize() {
    if (!image || busy || loading) return;
    const ticket = ++generation.current;
    setBusy(true);
    setStatus("正在识别题文与公式…");
    try {
      const box = crop && crop[2] > 3 && crop[3] > 3 ? crop : undefined;
      const data = raster(image, ...(box ?? []));
      if (data.length > 6 * 1024 * 1024) throw Error("截图超过识别大小限制，请裁剪或压缩");
      const width = box?.[2] ?? image.naturalWidth, height = box?.[3] ?? image.naturalHeight;
      const reply = await invoke("recognition_call", { image: data });
      if (ticket !== generation.current) return;
      const result = textFromOcr(reply, width, height);
      setRegions(result.regions.map(({ box: [x, y, w, h], ...region }) => ({
        ...region, box: [x + (box?.[0] ?? 0), y + (box?.[1] ?? 0), w, h],
      })));
      setText(result.text);
      setOriginal(result.text);
      setWarnings(result.warnings);
      setStatus("识别完成，请核对题文与公式。");
    } catch (e) { if (ticket === generation.current) setStatus(errorText(e)); }
    finally { if (ticket === generation.current) setBusy(false); }
  }
  function position(box: Box) {
    if (!image) return {};
    return { left: `${box[0] / image.naturalWidth * 100}%`, top: `${box[1] / image.naturalHeight * 100}%`,
      width: `${box[2] / image.naturalWidth * 100}%`, height: `${box[3] / image.naturalHeight * 100}%` };
  }
  async function openSettings() {
    try { setKey(""); setSettings(await invoke<Settings>("recognition_settings")); } catch (e) { setStatus(errorText(e)); }
  }
  async function saveSettings(forget = false) {
    try {
      await invoke("recognition_save_settings", { settings, key: key.trim(), forget });
      setKey("");
      setSettings(undefined);
      setStatus(forget ? "已移除 API Key。" : "百炼设置已保存。");
    } catch (e) { setStatus(errorText(e)); }
  }

  return <Modal title="识别题目图片" wide onClose={dismiss}>
    <div className="modal-content" onPaste={e => {
      if (e.target instanceof HTMLTextAreaElement || e.target instanceof HTMLInputElement) return;
      if (e.clipboardData.types.includes("Files")) { e.preventDefault(); void pick(true); }
    }} onKeyDown={e => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "v" && !(e.target instanceof HTMLTextAreaElement || e.target instanceof HTMLInputElement)) {
        e.preventDefault();
        void pick(true);
      }
    }}>
      <div className="row wrap">
        <button disabled={busy || loading} onClick={() => void pick(false)}>选择截图</button>
        <button disabled={busy || loading} onClick={() => void pick(true)}>粘贴截图</button>
        <button disabled={!image || busy} className={pickingCrop ? "active" : ""} onClick={() => setPickingCrop(!pickingCrop)}>{pickingCrop ? "在原图上拖出范围" : "裁剪题干"}</button>
        {crop && <button disabled={busy} onClick={() => setCrop(undefined)}>恢复整图</button>}
        <span className="spacer" /><button disabled={busy} onClick={() => void openSettings()}>百炼设置</button>
        <button className="primary" disabled={!image || busy || loading} onClick={() => void recognize()}>识别题文</button>
        {busy && <button onClick={() => {
          generation.current++; setBusy(false); setStatus("已停止等待识别结果。");
        }}>停止等待</button>}
      </div>
      <div className="ocr-grid">
        <div className="ocr-image scroll">
          {image ? <div className={pickingCrop ? "source-image cropping" : "source-image"}
            onPointerDown={e => {
              if (!pickingCrop) return;
              const rect = e.currentTarget.getBoundingClientRect();
              start.current = [Math.round((e.clientX - rect.left) / rect.width * image.naturalWidth),
                Math.round((e.clientY - rect.top) / rect.height * image.naturalHeight)];
              e.currentTarget.setPointerCapture(e.pointerId);
              move(e);
            }}
            onPointerMove={move} onPointerUp={e => { move(e); start.current = null; setPickingCrop(false); }}
            onPointerCancel={() => { start.current = null; setPickingCrop(false); }}>
            <img src={image.src} alt="题目截图" draggable={false} />
            {regions.map(region => <span className="ocr-region" key={region.id} style={position(region.box)} title={region.text} />)}
            {crop && <span className="crop-region" style={position(crop)} />}
          </div> : <div className="empty">选择或粘贴一张 PNG / JPEG 截图</div>}
        </div>
        <div className="ocr-result">
          <div className="row">
            <button className={!preview ? "active" : ""} onClick={() => setPreview(false)}>LaTeX 源码</button>
            <button className={preview ? "active" : ""} onClick={() => setPreview(true)}>排版预览</button>
            <span className="spacer" />
            <button disabled={!original || text === original} onClick={() => setText(original)}>恢复识别原文</button>
          </div>
          {preview ? <div className="question-text scroll ocr-text"><MathText text={text} /></div> :
            <textarea aria-label="识别题文" className="ocr-text" value={text} placeholder="识别后可在这里校对"
              onChange={e => setText(e.target.value)} />}
        </div>
      </div>
      <p role="status">{status || "点击「识别题文」后，将所选截图或裁剪范围发送到配置的百炼地域。"}</p>
      {warnings.length > 0 && <ul className="warning">{warnings.map(w => <li key={w}>{w}</li>)}</ul>}
      {settings && <Modal title="百炼设置" onClose={() => setSettings(undefined)}>
        <div className="modal-content">
          <label>地域<select value={settings.region} onChange={e => setSettings({ ...settings, region: e.target.value })}>
            <option value="beijing">中国内地（北京）</option><option value="singapore">国际（新加坡）</option>
          </select></label>
          <label>工作空间（可选）<input value={settings.workspace} onChange={e => setSettings({ ...settings, workspace: e.target.value })} /></label>
          <label>OCR 模型<input value={settings.ocrModel} onChange={e => setSettings({ ...settings, ocrModel: e.target.value })} /></label>
          <label>API Key（仅保存到本机配置）<input type="password" value={key} autoComplete="off"
            placeholder={settings.configured ? "已配置，留空保留" : "输入百炼 API Key"} onChange={e => setKey(e.target.value)} /></label>
          {status && <p role="status">{status}</p>}
        </div>
        <footer className="dialog-footer"><button disabled={!settings.configured} onClick={() => void saveSettings(true)}>移除 API Key</button>
          <span className="spacer" /><button className="primary" onClick={() => void saveSettings()}>保存设置</button></footer>
      </Modal>}
    </div>
    <footer className="dialog-footer">
      <button disabled={!text.trim()} onClick={() => void navigator.clipboard.writeText(text).then(() => setStatus("已复制全文。"), e => setStatus(errorText(e)))}>复制全文</button>
      <span className="spacer" /><button onClick={dismiss}>取消</button>
      <button className="primary" disabled={busy || !text.trim()} onClick={() => use(text)}>填入题干</button>
    </footer>
  </Modal>;
}

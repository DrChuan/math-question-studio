import { useEffect, useRef, useState, type PointerEvent } from "react";
import { Modal } from "./ui";

const tools = ["画笔", "直线", "矩形", "圆/椭圆", "文字"];
type Point = { x: number; y: number };

export function Drawing({ close, use }: { close: () => void; use: (png: string) => void }) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const history = useRef<ImageData[]>([]);
  const stroke = useRef<{ start: Point; last: Point; image: ImageData } | null>(null);
  const [tool, setTool] = useState(tools[0]);
  const [color, setColor] = useState("#18251f");
  const [width, setWidth] = useState(3);
  const [label, setLabel] = useState("");
  const ctx = () => canvas.current!.getContext("2d")!;
  const clear = () => { const c = ctx(); c.fillStyle = "#ffffff"; c.fillRect(0, 0, 1080, 720); };
  useEffect(clear, []);
  const snapshot = () => {
    const image = ctx().getImageData(0, 0, 1080, 720);
    history.current.push(image);
    if (history.current.length > 30) history.current.shift();
    return image;
  };
  const point = (e: PointerEvent<HTMLCanvasElement>) => {
    const box = e.currentTarget.getBoundingClientRect();
    return { x: (e.clientX - box.left) * 1080 / box.width, y: (e.clientY - box.top) * 720 / box.height };
  };
  function draw(c: CanvasRenderingContext2D, start: Point, end: Point) {
    c.strokeStyle = color;
    c.lineWidth = width;
    c.lineCap = "round";
    c.lineJoin = "round";
    if (tool === "矩形") c.strokeRect(start.x, start.y, end.x - start.x, end.y - start.y);
    else {
      c.beginPath();
      if (tool === "圆/椭圆") c.ellipse((start.x + end.x) / 2, (start.y + end.y) / 2,
        Math.abs(end.x - start.x) / 2, Math.abs(end.y - start.y) / 2, 0, 0, Math.PI * 2);
      else { c.moveTo(start.x, start.y); c.lineTo(end.x, end.y); }
      c.stroke();
    }
  }
  function begin(e: PointerEvent<HTMLCanvasElement>) {
    if (e.button !== 0) return;
    const at = point(e);
    if (tool === "文字") {
      if (!label.trim()) return;
      snapshot();
      const c = ctx();
      c.fillStyle = color;
      c.font = `${Math.max(12, width * 6)}pt "Times New Roman", serif`;
      c.fillText(label, at.x, at.y);
      return;
    }
    e.currentTarget.setPointerCapture(e.pointerId);
    stroke.current = { start: at, last: at, image: snapshot() };
  }
  function move(e: PointerEvent<HTMLCanvasElement>, finish = false) {
    const state = stroke.current;
    if (!state) return;
    const at = point(e), c = ctx();
    if (tool !== "画笔") c.putImageData(state.image, 0, 0);
    draw(c, tool === "画笔" ? state.last : state.start, at);
    state.last = at;
    if (finish) stroke.current = null;
  }
  return <Modal title="软件内画图" wide onClose={close}>
    <div className="modal-content">
      <div className="row wrap">
        {tools.map(t => <button key={t} className={tool === t ? "active" : ""} onClick={() => setTool(t)}>{t}</button>)}
        <input aria-label="颜色" type="color" value={color} onChange={e => setColor(e.target.value)} />
        <label>线宽 <input type="range" min="1" max="18" value={width} onChange={e => setWidth(Number(e.target.value))} /></label>
        <button onClick={() => { const previous = history.current.pop(); if (previous) ctx().putImageData(previous, 0, 0); }}>撤销</button>
        <button onClick={() => { snapshot(); clear(); }}>清空</button>
      </div>
      {tool === "文字" && <input placeholder="输入标注文字，再点击画布" value={label} onChange={e => setLabel(e.target.value)} />}
      <canvas className="draw-canvas" ref={canvas} width="1080" height="720" onPointerDown={begin}
        onPointerMove={e => move(e)} onPointerUp={e => move(e, true)} onPointerCancel={() => { stroke.current = null; }} />
    </div>
    <footer className="dialog-footer"><button onClick={close}>取消</button><button className="primary" onClick={() => use(canvas.current!.toDataURL("image/png"))}>作为题目配图</button></footer>
  </Modal>;
}

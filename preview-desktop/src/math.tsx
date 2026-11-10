import { Fragment, type MouseEvent, type ReactNode, useEffect, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import katex from "katex";
import { type ImageContext, type Question, type Target, choice, imagePattern, marker, matchImage, codeWidth, options } from "./domain";

type Segment = { text: string; start: number; end: number; math: boolean; display?: boolean };

export function segments(text: string): Segment[] {
  const result: Segment[] = [];
  let from = 0;
  for (let at = 0; at < text.length; at++) {
    if (text.startsWith("\\$", at)) { at++; continue; }
    if (text[at] !== "$") continue;
    const delimiter = text.startsWith("$$", at) ? "$$" : "$";
    let end = text.indexOf(delimiter, at + delimiter.length);
    while (delimiter === "$" && end > 0 && (text.slice(0, end).match(/\\+$/)?.[0].length ?? 0) % 2) {
      end = text.indexOf(delimiter, end + 1);
    }
    if (end < 0) break;
    if (from < at) result.push({ text: text.slice(from, at), start: from, end: at, math: false });
    const source = text.slice(at + delimiter.length, end);
    result.push({ text: source.trim(), start: at + delimiter.length + source.length - source.trimStart().length,
      end: end + delimiter.length, math: true, display: delimiter === "$$" });
    from = end + delimiter.length;
    at = from - 1;
  }
  if (from < text.length) result.push({ text: text.slice(from), start: from, end: text.length, math: false });
  return result;
}

const groups: Record<string, string> = { "(": ")", "[": "]", "{": "}" };
const token = /[A-Za-z0-9._^'{}]/;

function matching(s: string, index: number, backwards = false) {
  const from = s[index];
  const to = backwards ? Object.keys(groups).find(key => groups[key] === from) : groups[from];
  if (!to) return -1;
  const step = backwards ? -1 : 1;
  let depth = 0;
  for (let i = index; i >= 0 && i < s.length; i += step) {
    if (s[i] === from) depth++;
    else if (s[i] === to && --depth === 0) return i;
  }
  return -1;
}

function leftOperand(s: string, index: number): [number, number] | undefined {
  while (index >= 0 && /\s/.test(s[index])) index--;
  if (index < 0) return;
  if (Object.values(groups).includes(s[index])) {
    let start = matching(s, index, true);
    if (start < 0) return;
    const command = s.slice(0, start).match(/\\[A-Za-z]+$/);
    if (command) start -= command[0].length;
    if (start > 0 && "^_".includes(s[start - 1])) start = leftOperand(s, start - 2)?.[0] ?? start;
    return [start, index + 1];
  }
  if (!token.test(s[index])) return;
  let start = index;
  while (start > 0 && token.test(s[start - 1])) start--;
  if (s[start - 1] === "\\") start--;
  return [start, index + 1];
}

function rightOperand(s: string, index: number): [number, number] | undefined {
  while (index < s.length && /\s/.test(s[index])) index++;
  if (index >= s.length) return;
  if (groups[s[index]]) {
    const end = matching(s, index);
    return end < 0 ? undefined : [index, end + 1];
  }
  const command = s.slice(index).match(/^\\[A-Za-z]*\s*/);
  if (command) {
    const end = index + command[0].length;
    const groupEnd = s[end] === "{" ? matching(s, end) : -1;
    return [index, groupEnd < 0 ? end : groupEnd + 1];
  }
  let end = index;
  while (end < s.length && token.test(s[end])) end++;
  return end > index ? [index, end] : undefined;
}

function texAtom(s: string, index: number): [string, number] | undefined {
  while (index < s.length && /\s/.test(s[index])) index++;
  if (index >= s.length) return;
  if (s[index] === "{") {
    const end = matching(s, index);
    return end >= 0 ? [s.slice(index + 1, end), end + 1] : undefined;
  }
  const command = s.slice(index).match(/^\\(?:[A-Za-z]+|.)/);
  const atom = command?.[0] ?? String.fromCodePoint(s.codePointAt(index)!);
  return [atom, index + atom.length];
}

export function normalizeFormula(source: string, expand = true) {
  let math = source.trim();
  if (expand) {
    const vector = /^[a-z](?:_[A-Za-z0-9]|_\{[^{}]+\}|'+|\\prime|\^(?:\\prime|\{(?:\\prime\s*|')+\}))*$/;
    math = math.replace(/\\v\{((?:[^{}]|\{[^{}]*\})*)\}/g, (_, v: string) =>
      `\\${vector.test(v) ? "boldsymbol" : "overrightarrow"}{${v}}`).replaceAll("@", "\\underline{\\quad\\quad}");
  }
  math = math.replace(/\\(?:tfrac|frac)(?![A-Za-z])/g, "\\dfrac")
    .replace(/\\+blank\b|\\+underline\{\\+quad\\+quad\}/g, "\\underline{\\quad\\quad}")
    .replace(/(\\mid)(?=\S)/g, "$1 ").replace(/\s*([<>])\s*/g, " $1 ");
  let compact = "", cursor = 0;
  for (let next = math.indexOf("\\dfrac"); next >= 0; next = math.indexOf("\\dfrac", cursor)) {
    compact += math.slice(cursor, next) + "\\dfrac";
    const numerator = texAtom(math, next + 6);
    const denominator = numerator && texAtom(math, numerator[1]);
    if (numerator && denominator) {
      compact += `{${numerator[0]}}{${denominator[0]}}`;
      cursor = denominator[1];
    } else cursor = next + 6;
  }
  math = compact + math.slice(cursor);
  for (let round = 0; round < 24; round++) {
    let changed = false;
    for (let i = math.indexOf("/"); i >= 0; i = math.indexOf("/", i + 1)) {
      const left = leftOperand(math, i - 1);
      const right = rightOperand(math, i + 1);
      if (!left || !right) continue;
      math = math.slice(0, left[0]) + `\\dfrac{${math.slice(...left).trim()}}{${math.slice(...right).trim()}}` + math.slice(right[1]);
      changed = true;
      break;
    }
    if (!changed) break;
  }
  return math;
}

export function normalizeText(text: string) {
  return segments(text).map(part => {
    if (!part.math) return part.text;
    const delimiter = part.display ? "$$" : "$";
    return delimiter + normalizeFormula(part.text, false) + delimiter;
  }).join("");
}

function jump(event: MouseEvent<HTMLElement>, offset: number, onJump?: (offset: number) => void, math = false) {
  if (!onJump || !(event.metaKey || event.ctrlKey)) return;
  event.preventDefault();
  if (math) { onJump(offset); return; }
  const doc = document as Document & { caretRangeFromPoint?: (x: number, y: number) => Range | null };
  const range = doc.caretRangeFromPoint?.(event.clientX, event.clientY);
  if (!range || !event.currentTarget.contains(range.startContainer)) { onJump(offset); return; }
  const before = document.createRange();
  before.selectNodeContents(event.currentTarget);
  before.setEnd(range.startContainer, range.startOffset);
  onJump(offset + before.toString().length);
}

const formulaCache = new Map<string, string>();

export function MathText({ text, start = 0, onJump }: { text: string; start?: number; onJump?: (offset: number) => void }) {
  if (!text) return <span className="muted">（空）</span>;
  return <>{segments(text).map(part => {
    if (!part.math) {
      let offset = part.start;
      return <Fragment key={part.start}>{part.text.split("@").map((value, i) => {
        const at = offset;
        offset += value.length + 1;
        return <Fragment key={i}>{i > 0 && <span className="blank" onClick={e => jump(e, start + at - 1, onJump, true)} />}
          <span onClick={e => jump(e, start + at, onJump)}>{value.replaceAll("\\$", "$")}</span>
        </Fragment>;
      })}</Fragment>;
    }
    const formula = normalizeFormula(part.text);
    const key = (part.display ? "$$" : "$") + formula;
    let html = formulaCache.get(key);
    if (!html) {
      html = katex.renderToString(formula, { throwOnError: false, strict: "ignore", trust: false,
        maxExpand: 1000, maxSize: 50, displayMode: !!part.display });
      if (formulaCache.size > 4000) formulaCache.clear();
      formulaCache.set(key, html);
    }
    return <span key={part.start} onClick={e => jump(e, start + part.start, onJump, true)} dangerouslySetInnerHTML={{ __html: html }} />;
  })}</>;
}

const cachedImages = new Map<string, Promise<string>>();

export function QuestionImage({ path, context, width, thumb = false }: {
  path: string; context: ImageContext; width?: number; thumb?: boolean;
}) {
  const local = context.files?.[path];
  const [loaded, setLoaded] = useState("");
  useEffect(() => {
    setLoaded("");
    if (local) return;
    let active = true;
    let pending = cachedImages.get(path);
    if (!pending) {
      pending = invoke<string>("read_image", { path });
      cachedImages.set(path, pending);
    }
    pending.then(value => { if (active) setLoaded(value); }).catch(() => cachedImages.delete(path));
    return () => { active = false; };
  }, [local, path]);
  const src = local || loaded;
  if (!src) return null;
  return <img className={thumb ? "thumbnail" : "question-image"} src={src} alt="题目配图"
    style={thumb || width == null ? undefined : { width: `${width}%`, maxHeight: "none" }} />;
}

export function Analysis({ q, context, onJump }: { q: Question; context: ImageContext; onJump?: (offset: number) => void }) {
  const nodes: ReactNode[] = [];
  const used = new Set<string>();
  let offset = 0;
  for (const code of q.analysis.matchAll(imagePattern())) {
    const at = code.index!;
    if (at > offset) nodes.push(<MathText key={`text-${offset}`} text={q.analysis.slice(offset, at)} start={offset} onJump={onJump} />);
    offset = at + code[0].length;
    const path = matchImage(code, q.analysis_image_paths, context.keys);
    if (!path) continue;
    used.add(path);
    const width = codeWidth(code) ?? q.image_widths[marker(context.keys[path])];
    nodes.push(<QuestionImage key={`img-${at}`} path={path} width={width} context={context} />);
  }
  if (offset < q.analysis.length) nodes.push(<MathText key={`text-${offset}`} text={q.analysis.slice(offset)} start={offset} onJump={onJump} />);
  for (const path of q.analysis_image_paths) {
    if (!used.has(path)) nodes.push(<QuestionImage key={path} path={path} context={context} width={q.image_widths[marker(context.keys[path])]} />);
  }
  return nodes.length ? <>{nodes}</> : <MathText text="" />;
}

export function QuestionContent({ q, context, details, onJump }: {
  q: Question; context: ImageContext; details: boolean; onJump?: (field: Target, offset: number) => void;
}) {
  const at = (target: Target) => onJump && ((offset: number) => onJump(target, offset));
  return <div className="question-text">
    <div><MathText text={q.stem} onJump={at("stem")} /></div>
    {q.image_paths.map((path, i) => <QuestionImage key={`${path}-${i}`} path={path} context={context}
      width={q.image_widths[marker(context.keys[path])]} />)}
    {choice(q.question_type) && q.options.some(option => option.trim()) && <div className="options">
      {q.options.map((option, i) => option.trim() && <div key={i}><strong>{options[i]}.</strong> <MathText text={option} onJump={at(`option_${i}`)} /></div>)}
    </div>}
    {details && <div className="question-details">
      <h4>答案</h4><MathText text={q.answer} onJump={at("answer")} />
      <h4>解析</h4><Analysis q={q} context={context} onJump={at("analysis")} />
      <h4>备注</h4><MathText text={q.note} onJump={at("note")} />
    </div>}
  </div>;
}

import catalog from "./catalog.json";

export const questionTypes = ["单选题", "多选题", "填空题", "解答题"];
export const options = ["A", "B", "C", "D", "E", "F"];
export const choice = (type: string) => type === questionTypes[0] || type === questionTypes[1];
export const uid = () => crypto.randomUUID().replaceAll("-", "");
export const today = () => new Date().toLocaleDateString("sv-SE");

export interface Question {
  id: string;
  question_type: string;
  difficulty: number;
  source: string;
  created_at: string;
  image_path: string;
  image_paths: string[];
  analysis_image_paths: string[];
  image_widths: Record<string, number>;
  stem: string;
  options: string[];
  answer: string;
  analysis: string;
  note: string;
  knowledge_tags: string[];
  method_tags: string[];
  learning_tags: string[];
}

export interface Basket { id: string; name: string; question_ids: string[] }
export interface Library {
  questions: Question[];
  baskets: Basket[];
  active_basket_id: string;
  method_tag_library: string[];
  learning_tag_library: string[];
  vmware_profile: Record<string, unknown>;
}
export interface Snapshot {
  library: Library;
  revision: number;
  image_keys: Record<string, string>;
  location: string;
}
export type Field = "stem" | "answer" | "analysis" | "note";
export type Target = Field | `option_${number}`;
export type ImageFiles = Record<string, string>;
export type ImageContext = { keys: Record<string, string>; files?: ImageFiles };

export function newQuestion(stem = ""): Question {
  return {
    id: uid(), question_type: questionTypes[0], difficulty: 3, created_at: today(),
    source: "", stem, options: options.map(() => ""), answer: "", analysis: "", note: "",
    image_path: "", image_paths: [], analysis_image_paths: [], image_widths: {},
    knowledge_tags: [], method_tags: [], learning_tags: [],
  };
}

export function title(question: Question, index: number) {
  const chars = [...question.stem.trim().replace(/\s+/g, " ")];
  const preview = chars.length ? chars.slice(0, 20).join("") + (chars.length > 20 ? "..." : "") : "未命名题目";
  const tag = question.knowledge_tags[0]?.split("/").at(-1)?.trim();
  return `${index + 1}. ${question.question_type} | ${preview}${tag ? " | " + tag : ""}`;
}

export const sorts = {
  date_desc: "创建时间 · 最新优先",
  date_asc: "创建时间 · 最早优先",
  chapter: "教材章节",
  difficulty_asc: "难度 · 从低到高",
  difficulty_desc: "难度 · 从高到低",
};
export type Sort = keyof typeof sorts;
export interface Filters { text: string; types: string[]; knowledge: string[]; methods: string[]; sort: Sort }
export const newFilters = (): Filters => ({ text: "", types: [...questionTypes], knowledge: [], methods: [], sort: "chapter" });
export interface KnowledgeNode { label: string; path: string; children?: KnowledgeNode[] }

export const knowledgeTree: KnowledgeNode[] = Object.entries(catalog.knowledge).map(([book, chapters]) => ({
  label: book, path: book, children: Object.entries(chapters).map(([chapter, topics]) => {
    const path = `${book} / ${chapter}`;
    return { label: chapter, path, children: topics.map(topic => ({ label: topic, path: `${path} / ${topic}` })) };
  }),
}));
export const chapterNodes = knowledgeTree.flatMap(book => book.children ?? []);
const leafNodes = chapterNodes.flatMap(chapter => chapter.children ?? []);
const ranks = new Map(leafNodes.map((node, index) => [node.path, index]));
for (const node of leafNodes) {
  const parts = node.path.split(" / ");
  for (const parent of [parts[0], parts.slice(0, 2).join(" / ")]) {
    if (!ranks.has(parent)) ranks.set(parent, ranks.get(node.path)!);
  }
}
const chapterRank = (question: Question) => Math.min(ranks.size + 1, ...question.knowledge_tags.map(tag => ranks.get(tag) ?? ranks.size + 1));
const compare = (a: string, b: string) => a < b ? -1 : a > b ? 1 : 0;

export function filteredQuestions(questions: Question[], filters: Filters) {
  const query = filters.text.trim().toLowerCase();
  const prefixes = filters.knowledge.filter(path => !filters.knowledge.some(parent => path.startsWith(parent + " / ")));
  return questions.map((q, i) => ({ q, i })).filter(({ q }) => {
    const text = [q.stem, q.source, q.created_at, q.difficulty.toFixed(1), q.answer, q.analysis, q.note,
      q.knowledge_tags.join(" "), q.method_tags.join(" ")].join(" ").toLowerCase();
    return (!query || text.includes(query)) && filters.types.includes(q.question_type) &&
      (!prefixes.length || q.knowledge_tags.some(tag => prefixes.some(prefix => tag.startsWith(prefix)))) &&
      (!filters.methods.length || q.method_tags.some(tag => filters.methods.includes(tag)));
  }).sort((a, b) => {
    const date = compare(a.q.created_at, b.q.created_at) || a.i - b.i;
    if (filters.sort === "chapter") {
      const aDate = /^\d{4}-\d{2}-\d{2}$/.test(a.q.created_at) ? a.q.created_at : "";
      const bDate = /^\d{4}-\d{2}-\d{2}$/.test(b.q.created_at) ? b.q.created_at : "";
      return chapterRank(a.q) - chapterRank(b.q) || compare(bDate, aDate) || b.i - a.i;
    }
    const ordering = (filters.sort.startsWith("difficulty") ? a.q.difficulty - b.q.difficulty : 0) || date;
    return filters.sort.endsWith("desc") ? -ordering : ordering;
  }).map(({ q }) => q);
}

export function descendants(node: KnowledgeNode): string[] {
  return [node.path, ...(node.children ?? []).flatMap(descendants)];
}

export const currentBasket = (library: Library) =>
  library.baskets.find(basket => basket.id === library.active_basket_id) ?? library.baskets[0];

export function basketQuestions(library: Library) {
  const byId = new Map(library.questions.map(q => [q.id, q]));
  return currentBasket(library).question_ids.flatMap(id => byId.get(id) ?? []);
}

export const toggleValue = (list: string[], value: string) =>
  list.includes(value) ? list.filter(item => item !== value) : [...list, value];

export const basename = (path: string) => path.split(/[\\/]/).at(-1) ?? path;

export const marker = (key: string) => `[[image:${key}]]`;
export const imageCode = (key: string, width?: number) => `![图片](image:${key})${width == null ? "" : `{width=${width}%}`}`;
export const imagePattern = () => /!\[[^\]]*\]\(image:([A-Za-z0-9_.-]+)\)(?:\{width=(auto|\d+(?:\.\d+)?%?)\})?|\[\[image:([A-Za-z0-9_.-]+)(?:\|width=(auto|\d+(?:\.\d+)?%?))?\]\]/g;

export function matchImage(code: ReadonlyArray<string | undefined>, paths: string[], keys: Record<string, string>) {
  return paths.find(path => keys[path] === (code[1] ?? code[3]) || basename(path) === (code[1] ?? code[3]));
}

export function codeWidth(code: ReadonlyArray<string | undefined>) {
  const raw = code[2] ?? code[4];
  if (!raw || raw === "auto") return undefined;
  return Math.min(100, Math.max(10, Math.round(parseFloat(raw))));
}

export function applyImageWidths(source: string, paths: string[], keys: Record<string, string>, previous: Record<string, number>) {
  const result = { ...previous };
  for (const code of source.matchAll(imagePattern())) {
    const path = matchImage(code, paths, keys);
    if (!path) continue;
    const key = marker(keys[path]);
    const width = codeWidth(code);
    if (width === undefined) delete result[key];
    else result[key] = width;
  }
  return result;
}

export async function keyForImage(path: string) {
  const hash = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(basename(path)));
  return [...new Uint8Array(hash)].slice(0, 6).map(byte => byte.toString(16).padStart(2, "0")).join("");
}

export async function loadImage(source: Blob | string) {
  const url = typeof source === "string" ? source : await new Promise<string>((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result));
    reader.onerror = () => reject(Error("无法读取图片"));
    reader.readAsDataURL(source);
  });
  const image = new Image();
  image.src = url;
  try { await image.decode(); } catch { throw Error("无法解码图片，请选择有效图片文件"); }
  return image;
}

export function raster(image: HTMLImageElement, x = 0, y = 0, width = image.naturalWidth, height = image.naturalHeight) {
  if (!width || !height || width > 12000 || height > 12000 || width * height > 16_000_000) {
    throw Error("图片尺寸过大，请先裁剪");
  }
  const canvas = document.createElement("canvas");
  canvas.width = width;
  canvas.height = height;
  canvas.getContext("2d")!.drawImage(image, x, y, width, height, 0, 0, width, height);
  return canvas.toDataURL("image/png");
}

export const errorText = (value: unknown) => value instanceof Error ? value.message : String(value);

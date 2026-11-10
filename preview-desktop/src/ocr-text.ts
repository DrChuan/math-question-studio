import { formatQuestionMath, questionMathWarnings } from "./ocr-math.ts";
export type Region = { id: string; text: string; box: [number, number, number, number] };
export function ocrRegions(raw:unknown,width:number,height:number):Region[]{
 const found:{text:string;rotate_rect:number[]}[]=[];
 const visit=(v:unknown)=>{if(!v||typeof v!=='object')return;if(Array.isArray(v)){v.forEach(visit);return;}const x=v as Record<string,unknown>;
  if(Array.isArray(x.words_info))for(const r of x.words_info){const b=r as Record<string,unknown>;const text=b.text??b.words;if(typeof text==='string'&&Array.isArray(b.rotate_rect))found.push({text,rotate_rect:b.rotate_rect as number[]});}else{};
  for(const [k,c]of Object.entries(x))if(k!=='words_info')visit(c);
 };visit(raw);
 return found.map((r,i)=>{const [cx,cy,w,h,angle=0]=r.rotate_rect;if(![cx,cy,w,h,angle].every(Number.isFinite)||w<=0||h<=0)throw Error('OCR 返回无效定位');const a=angle*Math.PI/180,bw=Math.abs(w*Math.cos(a))+Math.abs(h*Math.sin(a)),bh=Math.abs(w*Math.sin(a))+Math.abs(h*Math.cos(a));const x=Math.max(0,cx-bw/2),y=Math.max(0,cy-bh/2);if(x>=width||y>=height)throw Error('OCR 定位超出原图，请重新识别');return {id:`r${i+1}`,text:r.text,box:[x,y,Math.min(width-x,bw),Math.min(height-y,bh)] as [number,number,number,number]};});
}
export function recognitionText(text:string){
 const slash=String.fromCharCode(92),commands=['frac','dfrac','tfrac','sqrt','perp','parallel','angle','triangle','overrightarrow','vec','Gamma','Delta','pi','sin','cos','tan','left','right','text','mathrm','cdot','leq','geq'];let result='';
 for(let i=0;i<text.length;i++){if(text[i]===slash&&text[i+1]===slash&&text[i-1]!==slash&&commands.some(c=>text.startsWith(c,i+2)&&!/[A-Za-z]/.test(text[i+2+c.length]??''))){result+=slash;i++;}else result+=text[i];}return result;
}

export function normalizeQuestionParagraphs(rows:{text:string;regions:string[]}[]){
 const out:typeof rows=[];
 const boundary=(t:string)=>/^\s*(?:[（(]\s*[0-9ivxIVX]+\s*[）)]|[A-D][.．、]|\d+[.．、])/.test(t);
 for(const row of rows){const text=row.text.replace(/([^\n])\n(?!\s*\n|\s*[（(][0-9ivxIVX]+[）)]|\s*[A-D][.．、])/g,(_,a:string)=>a+(/[A-Za-z0-9]$/.test(a)?' ':'')).trim();
  const prev=out.at(-1);if(prev&&!boundary(text)&&!/[。！？!?；;：:]\s*$/.test(prev.text)){prev.text+=/[A-Za-z0-9]$/.test(prev.text)&&/^[A-Za-z0-9]/.test(text)?' '+text:text;prev.regions=[...new Set([...prev.regions,...row.regions])];}else out.push({...row,text});
 }return out;
}
export function replyText(value:unknown):string{
 const v=value as {output?:{choices?:{message?:{content?:{text?:string}[]|string}}[]}};
 const content=v.output?.choices?.[0]?.message?.content;
 if(typeof content==='string')return content;
 if(Array.isArray(content))return content.flatMap(p=>typeof p.text==='string'?[p.text]:[]).join('\n');
 throw Error('百炼未返回识别文本');
}

export function textFromOcr(raw: unknown, width: number, height: number) {
  let regions: Region[] = [];
  const warnings: string[] = [];
  try {
    regions = ocrRegions(raw, width, height);
  } catch {
    warnings.push("OCR 位置无效，保留题文供核对。");
  }
  const markdown = (value: string) => recognitionText(value.replace(/\\+[()]/g, () => "$").replace(/\\+[\[\]]/g, () => "$$"));
  let rows = regions.map(region => ({ text: markdown(region.text), regions: [region.id] }));
  if (!rows.length) {
    let text = replyText(raw).trim();
    try {
      const entries = JSON.parse(text.replace(/^```(?:json)?\s*/, "").replace(/\s*```$/, ""));
      if (Array.isArray(entries) && entries.every(row => row && typeof row.text === "string")) {
        text = entries.map(row => row.text).join("\n");
      }
    } catch {
      // Keep a plain text reply as-is.
    }
    rows = [{ text: markdown(text), regions: [] }];
  }
  const text = normalizeQuestionParagraphs(rows).map(row => formatQuestionMath(row.text)).join("\n\n").trim();
  if (!text) throw Error("OCR 未返回题文");
  warnings.push(...questionMathWarnings(text));
  return { text, regions, warnings };
}

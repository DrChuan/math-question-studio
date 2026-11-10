import katex from 'katex';

/** Presentation only: never apply this to executable condition source, OCR
 * regions, or quotes. In particular, formatting must not invent constraints. */
const symbols:Record<string,string>={
 'Γ':'Gamma','Δ':'Delta','Θ':'Theta','Λ':'Lambda','Ξ':'Xi','Π':'Pi','Σ':'Sigma','Φ':'Phi','Ψ':'Psi','Ω':'Omega',
 'α':'alpha','β':'beta','γ':'gamma','δ':'delta','ε':'epsilon','θ':'theta','λ':'lambda','μ':'mu','ν':'nu','ξ':'xi','π':'pi','ρ':'rho','σ':'sigma','τ':'tau','φ':'varphi','ψ':'psi','ω':'omega',
 '△':'triangle','∆':'triangle','∠':'angle','⊥':'perp','∥':'parallel','∣':'mid','≤':'leq','≥':'geq','≠':'ne','≈':'approx','∞':'infty','·':'cdot','×':'times','÷':'div','∈':'in','∉':'notin','∪':'cup','∩':'cap','°':'circ',
};
const supers='⁰¹²³⁴⁵⁶⁷⁸⁹',subs='₀₁₂₃₄₅₆₇₈₉';
const functions=new Set(['sin','cos','tan','cot','sec','csc','arcsin','arccos','arctan','log','ln','sqrt']);

function groupEnd(s:string,start:number){
 const open=s[start],close:Record<string,string>={'(':')','{':'}','[':']'};let depth=0;
 for(let i=start;i<s.length;i++){if(s[i]===open)depth++;if(s[i]===close[open]&&--depth===0)return i;}
 return -1;
}

export function normalizeMathNotation(value:string):string {
 // Text inside a LaTeX text command is prose, including any English words.
 const texts:string[]=[];let s=value.replace(/\\(?:text|mathrm|operatorname)\{[^{}]*\}/g,t=>`\uE000${texts.push(t)-1}\uE001`);
 s=s.replace(/[−–]/g,'-').replace(/＝/g,'=').replace(/√/g,'\\sqrt').replace(/([⁰¹²³⁴⁵⁶⁷⁸⁹]+)/g,t=>`^{${[...t].map(c=>supers.indexOf(c)).join('')}}`).replace(/([₀₁₂₃₄₅₆₇₈₉]+)/g,t=>`_{${[...t].map(c=>subs.indexOf(c)).join('')}}`);
 s=s.replace(/[ΓΔΘΛΞΠΣΦΨΩαβγδεθλμνξπρστφψω△∆∠⊥∥∣≤≥≠≈∞·×÷∈∉∪∩]/g,c=>`\\${symbols[c]} `).replace(/°/g,'^{\\circ}');
 s=s.replace(/\b([A-Za-z])(\d+)\b/g,'$1_{$2}').replace(/_(\d+|[A-Za-z])/g,'_{$1}');
 s=s.replace(/(^|[^\\A-Za-z])(sin|cos|tan|cot|sec|csc|arcsin|arccos|arctan|log|ln|sqrt)\b/g,'$1\\$2');
 // OCR commonly emits √3 or sqrt(3). Brace exactly its operand, never guess
 // where an unparenthesized compound radical ends.
 for(let at=0;(at=s.indexOf('\\sqrt',at))>=0;){
  const start=at+5;let p=start;while(s[p]===' ')p++;
  if(s[p]==='('){const end=groupEnd(s,p);if(end>=0){s=s.slice(0,start)+'{'+normalizeMathNotation(s.slice(p+1,end))+'}'+s.slice(end+1);}}
  else if(s[p]!=='{'&&s[p]!=='['){const atom=s.slice(p).match(/^(?:\d+(?:\.\d+)?|[A-Za-z](?:_\{[^{}]+\}|_\d+)?|\\[A-Za-z]+)/)?.[0];if(atom)s=s.slice(0,start)+'{'+atom+'}'+s.slice(p+atom.length);}
  at=start;
 }
 return s.replace(/\uE000(\d+)\uE001/g,(_,i:string)=>texts[Number(i)]).trim();
}

/** Conservative math lexer: English words, section/option labels, Markdown
 * links and code are not mathematical letters. Chinese prose ends a span. */
function mathToken(s:string):string|undefined {
 const command=s.match(/^\\[A-Za-z]+\*?/)?.[0];if(command)return command;
 const word=s.match(/^[A-Za-z]+/)?.[0];if(word)return word.length===1||/^[A-Z]{2,12}$/.test(word)||functions.has(word)?word:undefined;
 return s.match(/^(?:\d+(?:\.\d+)?|[ΓΔΘΛΞΠΣΦΨΩαβγδεθλμνξπρστφψω√△∆∠⊥∥∣≤≥≠≈∞·×÷∈∉∪∩°⁰¹²³⁴⁵⁶⁷⁸⁹₀₁₂₃₄₅₆₇₈₉]|[+\-−–=<>*/^_{}()[\]|,':]|[ \t]+)/)?.[0];
}
function formatPlain(s:string){
 let out='';
 for(let at=0;at<s.length;){
  // Numbered questions, roman subquestions and answer letters stay prose.
  const label=s.slice(at).match(/^(?:[（(](?:\d+|[ivxIVX]+)[）)]|\d+[.．、]|[A-D][.．、])/)?.[0];
  if(label&&(at===0||/[\n\s。；;]/.test(s[at-1]))){out+=label;at+=label.length;continue;}
  if(!/[A-Za-z0-9\\ΓΔΘΛΞΠΣΦΨΩαβγδεθλμνξπρστφψω√△∆∠(−-]/.test(s[at])){out+=s[at++];continue;}
  let end=at,token:string|undefined;
  while((token=mathToken(s.slice(end)))){if(end>at&&/[ \t]/.test(s[end-1])&&/^[A-D][.．、]/.test(s.slice(end)))break;end+=token.length;}
  if(end===at){const word=s.slice(at).match(/^[A-Za-z]+/)?.[0]??s[at];out+=word;at+=word.length;continue;}
  let raw=s.slice(at,end).replace(/[ \t,':+*/=<>^_-]+$/,'');
  // An unmatched closing parenthesis belongs to the surrounding sentence.
  for(const [open,close]of [['(',')'],['[',']'],['{','}']])while(raw.endsWith(close)&&raw.split(close).length>raw.split(open).length)raw=raw.slice(0,-1).trimEnd();
  if(!raw){out+=s[at++];continue;}
  const math=/[A-Za-z\\ΓΔΘΛΞΠΣΦΨΩαβγδεθλμνξπρστφψω√△∆∠⊥∥∣·×÷∈∉∪∩°+^_*/=<>≤≥≠≈∞⁰¹²³⁴⁵⁶⁷⁸⁹₀₁₂₃₄₅₆₇₈₉]/.test(raw)||/^\([^()]+,[^()]+\)$/.test(raw);
  // Leave an unfinished group alone rather than manufacture a valid formula.
  const balanced=['()','[]','{}'].every(([a,b])=>raw.split(a).length===raw.split(b).length);
  out+=math&&balanced?`$${normalizeMathNotation(raw)}$`:raw;at+=raw.length;
 }return out;
}

export function formatQuestionMath(text:string):string {
 let out='',plain='';const flush=()=>{out+=formatPlain(plain);plain='';};
 for(let at=0;at<text.length;){
  const rest=text.slice(at);
  const protectedText=rest.match(/^(?:```[\s\S]*?```|`[^`\n]*`|https?:\/\/[^\s)]+|\[[^\]\n]+\]\([^\n)]*\))/)?.[0];
  if(protectedText){flush();out+=protectedText;at+=protectedText.length;continue;}
  const delimiter=rest.startsWith('$$')?'$$':rest.startsWith('$')?'$':rest.startsWith('\\(')?'\\(':rest.startsWith('\\[')?'\\[':undefined;
  if(delimiter){flush();const close=delimiter==='\\('? '\\)':delimiter==='\\['?'\\]':delimiter;let end=text.indexOf(close,at+delimiter.length);while(end>=0&&text[end-1]==='\\')end=text.indexOf(close,end+close.length);
   if(end<0){out+=rest;break;}const marker=delimiter==='\\['||delimiter==='$$'?'$$':'$';out+=marker+normalizeMathNotation(text.slice(at+delimiter.length,end))+marker;at=end+close.length;continue;
  }
  if(rest.startsWith('\\$')){flush();out+='\\$';at+=2;continue;}
  plain+=text[at++];
 }flush();return out;
}

/** A provenance comparison may ignore presentation changes, never values. */
export function comparableQuestionText(text:string){return formatQuestionMath(text).replace(/\\[()[\]]|[$\s{}]/g,'');}

/** Invalid or incomplete model formulas stay visible for correction. Do not
 * silently guess missing symbols or publish a successful formatting claim. */
export function questionMathWarnings(text:string):string[]{
 const warnings:string[]=[],source=text.replace(/```[\s\S]*?```|`[^`\n]*`/g,'').replace(/\\\$/g,'');let at=0,n=0;
 while(at<source.length){const start=source.indexOf('$',at);if(start<0)break;const marker=source.startsWith('$$',start)?'$$':'$',end=source.indexOf(marker,start+marker.length);n++;
  if(end<0){warnings.push('题文中的数学定界符未闭合，请核对 LaTeX 源码。');break;}
  try{katex.renderToString(source.slice(start+marker.length,end),{throwOnError:true,trust:false,maxExpand:1000,maxSize:50,displayMode:marker==='$$'});}catch{warnings.push(`题文第 ${n} 处公式未通过 LaTeX 排版检查，请核对源码；未猜改原文。`);}
  at=end+marker.length;
 }return warnings.slice(0,20);
}

export const fulltextMathRules=String.raw`全文排版协议（仅 paragraphs.text 与可读说明，不适用于 source）：所有数学字母、对象名称、参数和公式必须在 $…$ 或 $$…$$ 内，中文叙述、题号、小问号和选项标签在外。包括单独的点名、线名、希腊字母、下标，不能只给长公式加定界符。示例：椭圆 $\Gamma$，点 $A,B,C$，直线 $AC$，短轴长为 $2\sqrt{3}$，斜率分别为 $k_1,k_2,k_3$，$\triangle ABC$ 的重心是原点 $O$。公式中用 \sqrt{3}、\frac{1}{2}、\Gamma、\triangle、\perp、\parallel，不能混入裸写的 √3、Γ、△ABC、k1。保留原有数学含义、顺序、所有题文；不求解、不补写、不将待求内容当已知。不要嵌套 $；JSON 中反斜线必须正确转义。返回前逐段检查是否仍有数学字母或公式落在定界符外。`;

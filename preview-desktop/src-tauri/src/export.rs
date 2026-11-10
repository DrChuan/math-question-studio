use crate::{
    images::{code_key, code_width, matches, IMAGE_CODE},
    library::{atomic_write, error, id, image_data, local_image, marker, Question, Result, Store},
};
use base64::{engine::general_purpose::STANDARD, Engine};
use regex::Regex;
use serde::{Deserialize, Serialize};
use std::{
    collections::{BTreeMap, HashMap},
    fs,
    path::{Path, PathBuf},
    process::Command,
    sync::{LazyLock, Mutex},
};
use tauri::{AppHandle, Manager, State};
use tauri_plugin_dialog::DialogExt;

#[derive(Default)]
pub struct ExportCache(Mutex<Option<Compiled>>);

struct Compiled {
    ticket: String,
    source: String,
    directory: tempfile::TempDir,
}

#[derive(Deserialize)]
pub struct ExportSpec {
    title: String,
    include_analysis: bool,
    include_note: bool,
    leave_solution_space: bool,
    question_ids: Vec<String>,
    gaps: HashMap<String, f64>,
    widths: HashMap<String, BTreeMap<String, Option<u32>>>,
    math: HashMap<String, String>,
}

#[derive(Serialize)]
pub struct Preview {
    ticket: String,
    pages: Vec<String>,
    locations: Vec<Location>,
    pdf: Option<String>,
}

#[derive(Serialize)]
struct Location {
    index: usize,
    page: usize,
    top: f64,
}

struct Assets<'a> {
    root: &'a Path,
    work: &'a Path,
    aliases: HashMap<String, (String, PathBuf)>,
}

impl Assets<'_> {
    fn image(&mut self, path: &str, caption: &str, percent: Option<u32>) -> Result<String> {
        let Some(source) = local_image(self.root, path) else {
            return Ok(String::new());
        };
        let next = self.aliases.len();
        let alias = if let Some((name, _)) = self.aliases.get(path) {
            name.clone()
        } else {
            let ext = source.extension().and_then(|s| s.to_str()).unwrap_or("png");
            let name = format!("pm-preview-image-{next}.{}", ext.to_ascii_lowercase());
            fs::copy(&source, self.work.join(&name)).map_err(error)?;
            self.aliases
                .insert(path.to_string(), (name.clone(), source));
            name
        };
        let width = percent.unwrap_or(68).clamp(10, 100) as f64 / 100.0;
        let height = if percent.is_some() { "0.72" } else { "0.30" };
        Ok(format!(
            r"\par\begin{{center}}\includegraphics[width={width:.2}\linewidth,height={height}\textheight,keepaspectratio]{{\detokenize{{{alias}}}}}\par {caption}\end{{center}}"
        ))
    }
}

pub fn math_segments(text: &str) -> Vec<(bool, String)> {
    let mut result = Vec::new();
    let mut plain = String::new();
    let mut at = 0;
    while at < text.len() {
        let tail = &text[at..];
        if tail.starts_with(r"\$") {
            plain.push('$');
            at += 2;
            continue;
        }
        if tail.starts_with('$') {
            let delimiter = if tail.starts_with("$$") { "$$" } else { "$" };
            let rest = &tail[delimiter.len()..];
            let end = rest.match_indices(delimiter).map(|(i, _)| i).find(|&i| {
                delimiter == "$$"
                    || rest[..i].bytes().rev().take_while(|&b| b == b'\\').count() % 2 == 0
            });
            if let Some(end) = end {
                if !plain.is_empty() {
                    result.push((false, std::mem::take(&mut plain)));
                }
                result.push((true, rest[..end].trim().to_string()));
                at += end + delimiter.len() * 2;
                continue;
            }
            plain.push_str(tail);
            break;
        }
        let ch = tail.chars().next().unwrap();
        plain.push(ch);
        at += ch.len_utf8();
    }
    if !plain.is_empty() {
        result.push((false, plain));
    }
    result
}

fn escape(text: &str) -> String {
    text.chars()
        .map(|ch| match ch {
            '\\' => r"\textbackslash{}".to_string(),
            '~' => r"\textasciitilde{}".to_string(),
            '^' => r"\textasciicircum{}".to_string(),
            '&' | '%' | '#' | '_' | '{' | '}' | '$' => format!("\\{ch}"),
            _ => ch.to_string(),
        })
        .collect()
}

fn inline(text: &str, breaks: bool, spec: &ExportSpec) -> String {
    static NEWLINES: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"\n[ \t]*\n+|\n").unwrap());
    let text = text.replace("\r\n", "\n").replace('\r', "\n");
    let mut output = String::new();
    for (math, part) in math_segments(text.trim_matches('\n')) {
        if math {
            let expression = spec.math.get(&part).unwrap_or(&part);
            if expression.is_empty() {
                continue;
            }
            output.push('$');
            if expression.contains(r"\dfrac") {
                output.push_str(r"\rule[-1.0em]{0pt}{3.2em}");
            }
            output.push_str(expression);
            output.push('$');
        } else {
            let text = part
                .split('@')
                .map(escape)
                .collect::<Vec<_>>()
                .join(r"\underline{\hspace{4em}}");
            let fragment = NEWLINES.replace_all(&text, |c: &regex::Captures<'_>| {
                if c[0].len() > 1 {
                    r"\par "
                } else if breaks {
                    r"\\ "
                } else {
                    " "
                }
            });
            output.push_str(&fragment);
        }
    }
    output.trim().to_string()
}

fn choice(question: &Question, spec: &ExportSpec) -> String {
    static COMMAND: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"\\[A-Za-z]+").unwrap());
    let choices = question
        .options
        .iter()
        .zip('A'..='F')
        .filter(|(option, _)| !option.trim().is_empty())
        .map(|(option, label)| {
            (
                option.split_whitespace().collect::<Vec<_>>().join(" "),
                label,
            )
        })
        .collect::<Vec<_>>();
    if choices.is_empty() {
        return String::new();
    }
    let widths = choices
        .iter()
        .map(|(text, _)| {
            COMMAND
                .replace_all(text, "x")
                .chars()
                .filter(|ch| !['{', '}', '$'].contains(ch))
                .map(|ch| if ch.is_ascii() { 0.55 } else { 1.0 })
                .sum::<f64>()
        })
        .collect::<Vec<_>>();
    let row_size = if widths.len() <= 4
        && widths.iter().all(|w| *w <= 4.8)
        && widths.iter().sum::<f64>() + (widths.len() - 1) as f64 * 3.2 <= 22.0
    {
        widths.len()
    } else if widths
        .chunks(2)
        .all(|row| row.iter().sum::<f64>() + (row.len() - 1) as f64 * 3.2 <= 17.0)
    {
        2
    } else {
        1
    };
    choices.chunks(row_size).map(|row| {
        let cells = row.iter().map(|(text, label)| format!("\\PMOptionCell{{{label}}}{{{}}}", inline(text, false, spec))).collect::<Vec<_>>();
        if row.len() == 1 {
            return format!("\\PMOptionBlock{{{}}}", cells[0]);
        }
        let width = match row.len() { 4 => "0.235", 3 => "0.30", _ => "0.47" };
        let columns = vec![format!(r">{{\RaggedRight\arraybackslash}}p{{{width}\linewidth}}"); row.len()].join(r"@{\extracolsep{\fill}}");
        format!("\\PMOptionBlock{{\\begin{{tabular*}}{{\\linewidth}}{{@{{}}{columns}@{{}}}}\n{} \\\\\n\\end{{tabular*}}}}", cells.join(" & "))
    }).collect::<Vec<_>>().join("\n")
}

fn question_source(
    q: &Question,
    index: usize,
    spec: &ExportSpec,
    assets: &mut Assets<'_>,
) -> Result<String> {
    let mut output = Vec::new();
    let lines = (q.stem.chars().count() / 55 + q.stem.matches('\n').count() + 3).clamp(4, 12);
    let extra = if q.image_paths.is_empty() {
        ""
    } else {
        r"+0.30\textheight+3\baselineskip"
    };
    let prefix = if q.question_type == "多选题" {
        "（多选）"
    } else {
        ""
    };
    output.push(format!(
        r"\Needspace{{\dimexpr {lines}\baselineskip{extra}\relax}}"
    ));
    output.push(format!(
        r"\PMQuestionLine{{{index}}}{{{prefix}}}{{{}}}",
        inline(&q.stem, true, spec)
    ));
    let key_width = |path: &str, source_width: Option<u32>| {
        let key = marker(path);
        spec.widths
            .get(&q.id)
            .and_then(|widths| widths.get(&key))
            .copied()
            .unwrap_or_else(|| source_width.or(q.image_widths.get(&key).copied()))
    };
    for (i, path) in q.image_paths.iter().enumerate() {
        let suffix = if q.image_paths.len() > 1 {
            format!("（{}）", i + 1)
        } else {
            String::new()
        };
        output.push(assets.image(
            path,
            &format!("第{index}题图{suffix}"),
            key_width(path, None),
        )?);
    }
    if matches!(q.question_type.as_str(), "单选题" | "多选题") {
        output.push(choice(q, spec));
    }
    if spec.include_analysis {
        let mut lines = vec![String::new()];
        for (math, text) in math_segments(&q.answer.replace("\r\n", "\n").replace('\r', "\n")) {
            if math {
                lines.last_mut().unwrap().push_str(&format!("${text}$"));
            } else {
                let mut parts = text.split('\n');
                lines.last_mut().unwrap().push_str(parts.next().unwrap());
                lines.extend(parts.map(str::to_string));
            }
        }
        let mut lines = lines
            .iter()
            .map(|line| inline(line, false, spec))
            .filter(|line| !line.is_empty());
        output.push(format!(
            r"\PMAnswerFirst{{{}}}",
            lines.next().unwrap_or_default()
        ));
        output.extend(lines.map(|line| format!(r"\PMAnswerParagraph{{{line}}}")));
        let mut first = true;
        let mut offset = 0;
        let mut used = Vec::new();
        let mut add_text = |out: &mut Vec<String>, part: &str| {
            if !part.trim().is_empty() {
                let text = inline(part, true, spec);
                out.push(if first {
                    format!(r"\PMLabelLine{{解析}}{{{text}}}")
                } else {
                    format!(r"{text}\par")
                });
                first = false;
            }
        };
        for code in IMAGE_CODE.captures_iter(&q.analysis) {
            let whole = code.get(0).unwrap();
            add_text(&mut output, &q.analysis[offset..whole.start()]);
            offset = whole.end();
            if let Some(path) = q
                .analysis_image_paths
                .iter()
                .find(|path| matches(code_key(&code), path))
            {
                used.push(path);
                output.push(assets.image(path, "", key_width(path, code_width(&code)))?);
            }
        }
        add_text(&mut output, &q.analysis[offset..]);
        for path in &q.analysis_image_paths {
            if !used.contains(&path) {
                output.push(assets.image(path, "", key_width(path, None))?);
            }
        }
    }
    if spec.include_note {
        output.push(format!(
            r"\PMLabelLine{{备注}}{{{}}}",
            inline(&q.note, true, spec)
        ));
    }
    if spec.leave_solution_space && q.question_type == "解答题" {
        output.push(r"\PMSolutionBlank".into());
    }
    Ok(output
        .into_iter()
        .filter(|s| !s.is_empty())
        .collect::<Vec<_>>()
        .join("\n"))
}

fn executable(name: &str) -> PathBuf {
    let candidates = std::env::var_os("PATH")
        .into_iter()
        .flat_map(|paths| std::env::split_paths(&paths).collect::<Vec<_>>())
        .chain(["/Library/TeX/texbin", "/opt/homebrew/bin", "/usr/local/bin"].map(PathBuf::from));
    let filename = format!("{name}{}", std::env::consts::EXE_SUFFIX);
    candidates
        .map(|p| p.join(&filename))
        .find(|p| p.is_file())
        .unwrap_or_else(|| PathBuf::from(filename))
}

fn run(name: &str, work: &Path, args: &[&str]) -> Result<()> {
    let executable = executable(name);
    let mut command = Command::new(&executable);
    command
        .current_dir(work)
        .args(args)
        .env("openin_any", "p")
        .env("openout_any", "p");
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        command.creation_flags(0x08000000);
    }
    let output = command
        .output()
        .map_err(|e| format!("无法启动 {name}，请检查 TeX 工具是否已安装。{e}"))?;
    if !output.status.success() {
        let log = format!(
            "{}\n{}",
            String::from_utf8_lossy(&output.stdout),
            String::from_utf8_lossy(&output.stderr)
        );
        let detail = log
            .lines()
            .rev()
            .take(36)
            .collect::<Vec<_>>()
            .into_iter()
            .rev()
            .collect::<Vec<_>>()
            .join("\n");
        return Err(format!("{name} 编译失败\n{detail}"));
    }
    Ok(())
}

fn render_pages(work: &Path) -> Vec<String> {
    // A4 at 216 DPI covers the 800 CSS-pixel preview on a 2× display.
    const PREVIEW_DPI: &str = "216";
    let resolution = format!("-r{PREVIEW_DPI}");
    let args = [
        "-dSAFER",
        "-dBATCH",
        "-dNOPAUSE",
        "-sDEVICE=png16m",
        "-dTextAlphaBits=4",
        "-dGraphicsAlphaBits=4",
        &resolution,
        "-sOutputFile=page-%03d.png",
        "paper.pdf",
    ];
    let rasterized = ["gs", "gswin64c", "gswin32c"]
        .iter()
        .any(|name| run(name, work, &args).is_ok())
        || run(
            "pdftoppm",
            work,
            &["-r", PREVIEW_DPI, "-png", "paper.pdf", "page"],
        )
        .is_ok();
    if !rasterized {
        return Vec::new();
    }
    let mut pages = fs::read_dir(work)
        .into_iter()
        .flatten()
        .flatten()
        .map(|f| f.path())
        .filter(|p| {
            p.file_name()
                .and_then(|n| n.to_str())
                .is_some_and(|n| n.starts_with("page-") && n.ends_with(".png"))
        })
        .collect::<Vec<_>>();
    pages.sort_by_key(|p| {
        p.file_stem().unwrap().to_string_lossy()[5..]
            .parse::<usize>()
            .unwrap_or(0)
    });
    pages
        .iter()
        .filter_map(|page| image_data(page).ok())
        .collect()
}

fn compile(root: &Path, questions: &[Question], spec: &ExportSpec) -> Result<(Compiled, Preview)> {
    let directory = tempfile::tempdir().map_err(error)?;
    let work = directory.path();
    let mut assets = Assets {
        root,
        work,
        aliases: HashMap::new(),
    };
    let title = if spec.title.trim().is_empty() {
        "高中数学题目"
    } else {
        spec.title.trim()
    };
    let mut body = vec![format!("\\begin{{center}}\n{{\\PMTitleCJK\\bfseries\\fontsize{{22pt}}{{28pt}}\\selectfont {}}}\n\\end{{center}}", escape(title))];
    for (index, question) in questions.iter().enumerate() {
        body.push(question_source(question, index + 1, spec, &mut assets)?);
        let gap = spec
            .gaps
            .get(&question.id)
            .copied()
            .unwrap_or(0.0)
            .clamp(0.0, 250.0);
        if index + 1 < questions.len() && gap > 0.0 {
            body.push(format!("\\par\\vspace{{{gap:.2}mm}}"));
        }
    }
    let preamble = include_str!("preamble.tex");
    let source = format!(
        "{preamble}\n\\begin{{document}}\n{}\n\\end{{document}}\n",
        body.join("\n\n")
    );
    fs::write(work.join("paper.tex"), &source).map_err(error)?;
    run(
        "xelatex",
        work,
        &[
            "-no-pdf",
            "-no-shell-escape",
            "-interaction=nonstopmode",
            "-halt-on-error",
            "-file-line-error",
            "paper.tex",
        ],
    )?;
    run("xdvipdfmx", work, &["-o", "paper.pdf", "paper.xdv"])?;
    let aux = fs::read_to_string(work.join("paper.aux")).map_err(error)?;
    let labels = Regex::new(r"pm-pos-(\d+)\}\{\\posx\{\d+\}\\posy\{(\d+)\}\\abspage\{(\d+)\}")
        .map_err(error)?;
    let locations = labels
        .captures_iter(&aux)
        .filter_map(|c| {
            let index: usize = c[1].parse().ok()?;
            let y: f64 = c[2].parse().ok()?;
            let page: usize = c[3].parse().ok()?;
            let top = (1.0 - y / (297.0 * 72.27 / 25.4 * 65536.0) - 0.015).clamp(0.0, 1.0);
            Some(Location {
                index: index.checked_sub(1)?,
                page: page.checked_sub(1)?,
                top,
            })
        })
        .collect();
    let pages = render_pages(work);
    let pdf = if pages.is_empty() {
        Some(STANDARD.encode(fs::read(work.join("paper.pdf")).map_err(error)?))
    } else {
        None
    };
    let ticket = id();
    let mut final_source = source;
    for (alias, original) in assets.aliases.values() {
        let destination = original.to_string_lossy().replace('\\', "/");
        final_source = final_source.replace(&format!("{{{alias}}}"), &format!("{{{destination}}}"));
    }
    Ok((
        Compiled {
            ticket: ticket.clone(),
            source: final_source,
            directory,
        },
        Preview {
            ticket,
            pages,
            locations,
            pdf,
        },
    ))
}

#[tauri::command]
pub async fn render_export(app: AppHandle, spec: ExportSpec) -> Result<Preview> {
    tauri::async_runtime::spawn_blocking(move || {
        let store = app.state::<Store>();
        let library = store.inner.lock().map_err(error)?.library.clone();
        let questions = spec
            .question_ids
            .iter()
            .map(|id| {
                library
                    .questions
                    .iter()
                    .find(|q| &q.id == id)
                    .cloned()
                    .ok_or("预览中的题目已不存在")
            })
            .collect::<std::result::Result<Vec<_>, _>>()?;
        if questions.is_empty() {
            return Err("试题篮为空".into());
        }
        let (compiled, preview) = compile(&store.root, &questions, &spec)?;
        *app.state::<ExportCache>().0.lock().map_err(error)? = Some(compiled);
        Ok(preview)
    })
    .await
    .map_err(error)?
}

#[tauri::command]
pub fn clear_export(cache: State<'_, ExportCache>) -> Result<()> {
    *cache.0.lock().map_err(error)? = None;
    Ok(())
}

#[tauri::command]
pub async fn save_export(app: AppHandle, ticket: String, name: String) -> Result<Option<String>> {
    tauri::async_runtime::spawn_blocking(move || {
        let Some(file) = app
            .dialog()
            .file()
            .set_title("导出 LaTeX 与 PDF")
            .add_filter("LaTeX", &["tex"])
            .set_file_name(format!("{name}.tex"))
            .blocking_save_file()
        else {
            return Ok(None);
        };
        let target = file.into_path().map_err(error)?.with_extension("tex");
        let cache = app.state::<ExportCache>();
        let cached = cache.0.lock().map_err(error)?;
        let compiled = cached
            .as_ref()
            .filter(|c| c.ticket == ticket)
            .ok_or("排版已经更新，请使用当前预览导出")?;
        let pdf = fs::read(compiled.directory.path().join("paper.pdf")).map_err(error)?;
        atomic_write(&target, compiled.source.as_bytes())?;
        atomic_write(&target.with_extension("pdf"), &pdf)?;
        Ok(Some(target.display().to_string()))
    })
    .await
    .map_err(error)?
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    #[ignore = "requires local XeLaTeX"]
    fn builds_preview_and_pdf_with_figures_and_question_positions() {
        let root = tempfile::tempdir().unwrap();
        fs::create_dir(root.path().join("images")).unwrap();
        fs::write(
            root.path().join("images/demo.png"),
            include_bytes!("../icons/icon.png"),
        )
        .unwrap();
        let questions = serde_json::from_value::<Vec<Question>>(serde_json::json!([
            {"id":"a","question_type":"单选题","stem":"函数 $x^2$ 的值为 @。","options":["$1$","$2$","$3$","$4$"],"image_paths":["images/demo.png"],
            "answer":"A","analysis":"第一段。\n\n第二段。","note":"测试备注"},
            {"id":"b","question_type":"解答题","stem":"求解方程 $x^2=4$。"}
        ])).unwrap();
        let spec = serde_json::from_value(serde_json::json!({
            "title":"试卷","include_analysis":true,"include_note":true,"leave_solution_space":true,
            "question_ids":["a","b"],"gaps":{"a":12},"widths":{},"math":{}
        }))
        .unwrap();
        let (compiled, preview) = compile(root.path(), &questions, &spec).unwrap();
        assert_eq!(preview.locations.len(), 2);
        assert_eq!(preview.locations[0].index, 0);
        assert!(
            preview.locations[0].top < preview.locations[1].top
                || preview.locations[0].page < preview.locations[1].page
        );
        assert!(fs::read(compiled.directory.path().join("paper.pdf"))
            .unwrap()
            .starts_with(b"%PDF-"));
        assert!(compiled.source.contains("images/demo.png"));
        assert!(compiled.source.contains("12.00mm"));
        assert!(compiled.source.contains(r"}\par 第1题图"));
        assert!(!compiled.source.contains(r"}\\par 第1题图"));
        if let Some(page) = preview.pages.first() {
            let png = STANDARD.decode(page.split_once(',').unwrap().1).unwrap();
            let width = u32::from_be_bytes(png[16..20].try_into().unwrap());
            assert!(width >= 1600, "preview should be sharp on a 2× display");
        }
        assert!(!preview.pages.is_empty() || preview.pdf.is_some());
    }
}

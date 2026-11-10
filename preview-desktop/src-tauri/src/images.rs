use crate::library::{basename, image_key};
use regex::{Captures, Regex};
use std::sync::LazyLock;

pub static IMAGE_CODE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"!\[[^\]]*\]\(image:([A-Za-z0-9_.-]+)\)(?:\{width=(auto|\d+(?:\.\d+)?%?)\})?|\[\[image:([A-Za-z0-9_.-]+)(?:\|width=(auto|\d+(?:\.\d+)?%?))?\]\]").unwrap()
});

pub fn code_key<'a>(code: &'a Captures<'a>) -> &'a str {
    code.get(1).or_else(|| code.get(3)).unwrap().as_str()
}

pub fn code_width(code: &Captures<'_>) -> Option<u32> {
    code.get(2)
        .or_else(|| code.get(4))?
        .as_str()
        .trim_end_matches('%')
        .parse::<f64>()
        .ok()
        .map(|n| n.round_ties_even().clamp(10.0, 100.0) as u32)
}

pub fn matches(key: &str, path: &str) -> bool {
    key == basename(path) || key == image_key(path)
}

pub fn replace_code(text: &str, from: &str, to: &str) -> String {
    IMAGE_CODE
        .replace_all(text, |code: &Captures<'_>| {
            if !matches(code_key(code), from) {
                return code[0].to_string();
            }
            let width = code_width(code)
                .map(|n| format!("{{width={n}%}}"))
                .unwrap_or_default();
            format!("![图片](image:{}){width}", image_key(to))
        })
        .into_owned()
}

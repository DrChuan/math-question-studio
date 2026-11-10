//! Mathdraw’s Bailian OCR request and image input, limited to text recognition.
use crate::library::{error, Result, Store};
use base64::Engine;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::{
    fs,
    io::Read,
    path::{Path, PathBuf},
    time::Duration,
};
use tauri::Manager;
use tauri_plugin_clipboard_manager::ClipboardExt;
use tauri_plugin_dialog::DialogExt;

// Read only a file explicitly selected by the user; no arbitrary path is exposed.
#[tauri::command]
pub async fn recognition_choose_image(app: tauri::AppHandle) -> Result<Option<String>> {
    tauri::async_runtime::spawn_blocking(move || {
        let Some(file) = app
            .dialog()
            .file()
            .set_title("选择题干截图")
            .add_filter("PNG / JPEG 截图", &["png", "jpg", "jpeg"])
            .blocking_pick_file()
        else {
            return Ok(None);
        };
        let path = file.into_path().map_err(|_| "无法读取所选截图")?;
        let mut bytes = Vec::new();
        fs::File::open(path)
            .map_err(|_| "无法打开所选截图")?
            .take(8 * 1024 * 1024 + 1)
            .read_to_end(&mut bytes)
            .map_err(|_| "读取截图失败")?;
        if bytes.len() > 8 * 1024 * 1024 {
            return Err("请选择 8 MB 以内的截图".into());
        }
        let mime = if bytes.starts_with(b"\x89PNG\r\n\x1a\n") {
            "image/png"
        } else if bytes.starts_with(&[0xff, 0xd8, 0xff]) {
            "image/jpeg"
        } else {
            return Err("请选择有效的 PNG/JPEG 截图".into());
        };
        Ok(Some(format!(
            "data:{mime};base64,{}",
            base64::engine::general_purpose::STANDARD.encode(bytes)
        )))
    })
    .await
    .map_err(|_| "读取截图失败".to_string())?
}

#[tauri::command]
pub async fn recognition_paste_image(app: tauri::AppHandle) -> Result<String> {
    tauri::async_runtime::spawn_blocking(move || {
        let image = app
            .clipboard()
            .read_image()
            .map_err(|_| "剪贴板中没有可读取的图片，请先复制截图或使用“选择截图”")?;
        let (width, height) = (image.width(), image.height());
        if width == 0
            || height == 0
            || width > 12000
            || height > 12000
            || u64::from(width) * u64::from(height) > 16_000_000
        {
            return Err("图片过大，请先裁剪题干".into());
        }
        let mut bytes = Vec::new();
        {
            let mut encoder = png::Encoder::new(&mut bytes, width, height);
            encoder.set_color(png::ColorType::Rgba);
            encoder.set_depth(png::BitDepth::Eight);
            let mut writer = encoder.write_header().map_err(|_| "处理剪贴板图片失败")?;
            writer
                .write_image_data(image.rgba())
                .map_err(|_| "处理剪贴板图片失败")?;
        }
        if bytes.len() > 8 * 1024 * 1024 {
            return Err("截图过大，请裁剪或压缩后重试".into());
        }
        Ok(format!(
            "data:image/png;base64,{}",
            base64::engine::general_purpose::STANDARD.encode(bytes)
        ))
    })
    .await
    .map_err(|_| "读取剪贴板图片失败".to_string())?
}

#[derive(Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct Settings {
    region: String,
    workspace: String,
    ocr_model: String,
}

impl Default for Settings {
    fn default() -> Self {
        Self {
            region: "beijing".into(),
            workspace: String::new(),
            ocr_model: "qwen3.5-ocr".into(),
        }
    }
}

fn paths(app: &tauri::AppHandle) -> (PathBuf, PathBuf) {
    let store = app.state::<Store>();
    (
        store.root.join("recognition-settings.json"),
        store.root.join("recognition-api-key"),
    )
}

fn validate(s: &Settings) -> Result<()> {
    if !["beijing", "singapore"].contains(&s.region.as_str())
        || s.workspace.len() > 100
        || !s
            .workspace
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b == b'-')
        || s.ocr_model.is_empty()
        || s.ocr_model.len() > 100
        || !s
            .ocr_model
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b"-_.".contains(&b))
    {
        return Err("识别地域、工作空间或模型名无效".into());
    }
    Ok(())
}

fn settings(path: &Path) -> Result<Settings> {
    match fs::read(path) {
        Ok(bytes) => serde_json::from_slice(&bytes).map_err(error),
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => Ok(Settings::default()),
        Err(e) => Err(error(e)),
    }
}

#[tauri::command]
pub fn recognition_settings(app: tauri::AppHandle) -> Result<Value> {
    let (config, key) = paths(&app);
    let mut value = serde_json::to_value(settings(&config)?).map_err(error)?;
    value["configured"] = key.is_file().into();
    Ok(value)
}

#[tauri::command]
pub fn recognition_save_settings(
    app: tauri::AppHandle,
    settings: Settings,
    key: String,
    forget: bool,
) -> Result<()> {
    validate(&settings)?;
    let (config, secret) = paths(&app);
    if !key.is_empty() && (key.len() > 1024 || key.chars().any(char::is_whitespace)) {
        return Err("API Key 格式无效".into());
    }
    if forget {
        match fs::remove_file(&secret) {
            Ok(()) => (),
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => (),
            Err(_) => return Err("移除本机 API Key 失败".into()),
        }
    } else if !key.is_empty() {
        write_secret(&secret, &key)?;
    }
    crate::library::atomic_write(&config, &serde_json::to_vec(&settings).map_err(error)?)
}

#[tauri::command]
pub async fn recognition_call(app: tauri::AppHandle, image: String) -> Result<Value> {
    if image.len() > 6 * 1024 * 1024 || !image.starts_with("data:image/png;base64,") {
        return Err("识别请求大小或格式无效".into());
    }
    tauri::async_runtime::spawn_blocking(move || {
        let (config, key) = paths(&app);
        let s = settings(&config)?;
        validate(&s)?;
        let secret = fs::read_to_string(key).map_err(|_| "请先在百炼设置中填写 API Key")?;
        call_cloud(&s, secret.trim(), &image)
    })
    .await
    .map_err(error)?
}

fn endpoint(s: &Settings) -> String {
    let host = if s.workspace.is_empty() {
        if s.region == "beijing" {
            "dashscope.aliyuncs.com".into()
        } else {
            "dashscope-intl.aliyuncs.com".into()
        }
    } else {
        format!(
            "{}.{}.maas.aliyuncs.com",
            s.workspace,
            if s.region == "beijing" {
                "cn-beijing"
            } else {
                "ap-southeast-1"
            }
        )
    };
    format!("https://{host}/api/v1/services/aigc/multimodal-generation/generation")
}
fn write_secret(settings: &std::path::Path, key: &str) -> Result<()> {
    use std::io::Write;
    let target = settings.to_path_buf();
    let temp = settings.with_file_name(format!(".recognition-key-{}", uuid::Uuid::new_v4()));
    let result = (|| {
        let mut options = fs::OpenOptions::new();
        options.write(true).create_new(true);
        #[cfg(unix)]
        {
            use std::os::unix::fs::OpenOptionsExt;
            options.mode(0o600);
        }
        let mut file = options.open(&temp).map_err(|_| "无法保存本机 API Key")?;
        file.write_all(key.as_bytes())
            .map_err(|_| "保存本机 API Key 失败")?;
        file.sync_all().map_err(|_| "保存本机 API Key 失败")?;
        fs::rename(&temp, &target).map_err(|_| "替换本机 API Key 失败")
    })();
    if result.is_err() {
        let _ = fs::remove_file(temp);
    }
    result.map_err(String::from)
}
fn call_cloud(s: &Settings, secret: &str, image: &str) -> Result<Value> {
    let body = json!({
        "model": s.ocr_model,
        "input": {"messages": [{"role":"user","content":[{"image":image,"enable_rotate":false}]}]},
        "parameters": {"ocr_options":{"task":"advanced_recognition"}}
    });
    let response = reqwest::blocking::Client::builder()
        .timeout(Duration::from_secs(90))
        .connect_timeout(Duration::from_secs(15))
        .redirect(reqwest::redirect::Policy::none())
        .build()
        .map_err(|_| "建立识别连接失败")?
        .post(endpoint(&s))
        .bearer_auth(secret)
        .header("Content-Type", "application/json")
        .body(body.to_string())
        .send()
        .map_err(|_| "云端识别连接失败或超时；请手动重试")?;
    let status = response.status();
    let mut bytes = Vec::new();
    response
        .take(4 * 1024 * 1024 + 1)
        .read_to_end(&mut bytes)
        .map_err(|_| "读取识别回复失败")?;
    if bytes.len() > 4 * 1024 * 1024 {
        return Err("云端回复过大".into());
    }
    let parsed = serde_json::from_slice::<Value>(&bytes);
    if !status.is_success() {
        return Err(match status.as_u16() {
            401 | 403 => "API Key、地域或模型权限无效".into(),
            429 => "百炼限流或额度不足，请稍后手动重试".into(),
            _ => {
                let code = parsed
                    .as_ref()
                    .ok()
                    .and_then(|v| v.get("code"))
                    .and_then(Value::as_str)
                    .filter(|c| {
                        c.len() < 100
                            && c.chars()
                                .all(|x| x.is_ascii_alphanumeric() || "._-".contains(x))
                    })
                    .unwrap_or("ServiceError");
                if code == "Arrearage" {
                    "百炼账户欠费或余额不足（Arrearage），请在阿里云控制台检查账户后手动重试".into()
                } else {
                    format!(
                        "百炼返回 {}（{}），请检查模型名、地域及接口参数",
                        status.as_u16(),
                        code
                    )
                }
            }
        });
    }
    let value: Value = parsed.map_err(|_| "云端回复不是有效 JSON")?;
    if value.get("code").is_some() {
        return Err("百炼拒绝了识别请求，请检查模型权限和设置".into());
    }
    Ok(value)
}

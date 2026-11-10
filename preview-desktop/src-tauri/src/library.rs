use base64::{engine::general_purpose::STANDARD, Engine};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use sha2::{Digest, Sha256};
use std::{
    collections::{BTreeMap, HashMap, HashSet},
    fs::{self, File, OpenOptions},
    io::Write,
    path::{Component, Path, PathBuf},
    sync::Mutex,
};
use tauri::{Manager, State};

pub type Result<T> = std::result::Result<T, String>;

pub fn error(e: impl std::fmt::Display) -> String {
    e.to_string()
}

pub fn id() -> String {
    uuid::Uuid::new_v4().simple().to_string()
}

pub fn today() -> String {
    chrono::Local::now().date_naive().to_string()
}

#[derive(Clone, Default, Deserialize, Serialize)]
#[serde(default)]
pub struct Question {
    pub id: String,
    pub question_type: String,
    pub difficulty: f64,
    pub source: String,
    pub created_at: String,
    pub image_path: String,
    pub image_paths: Vec<String>,
    pub analysis_image_paths: Vec<String>,
    pub image_widths: BTreeMap<String, u32>,
    pub stem: String,
    pub options: Vec<String>,
    pub answer: String,
    pub analysis: String,
    pub note: String,
    pub knowledge_tags: Vec<String>,
    pub method_tags: Vec<String>,
    pub learning_tags: Vec<String>,
    #[serde(flatten)]
    extra: BTreeMap<String, Value>,
}

impl Question {
    pub fn images(&self) -> impl Iterator<Item = &String> {
        self.image_paths.iter().chain(&self.analysis_image_paths)
    }

    fn normalize(&mut self) {
        if self.id.is_empty() {
            self.id = id();
        }
        if self.question_type.is_empty() {
            self.question_type = "单选题".into();
        }
        self.difficulty = (self.difficulty * 2.0).round_ties_even().clamp(1.0, 10.0) / 2.0;
        if self.created_at.is_empty() {
            self.created_at = today();
        }
        self.options.resize(6, String::new());
        if self.image_paths.is_empty() && !self.image_path.is_empty() {
            self.image_paths.push(self.image_path.clone());
        }
        self.image_path = self.image_paths.first().cloned().unwrap_or_default();
        for width in self.image_widths.values_mut() {
            *width = (*width).clamp(10, 100);
        }
    }
}

#[derive(Clone, Default, Deserialize, Serialize)]
#[serde(default)]
pub struct Basket {
    pub id: String,
    pub name: String,
    pub question_ids: Vec<String>,
    #[serde(flatten)]
    extra: BTreeMap<String, Value>,
}

#[derive(Clone, Default, Deserialize, Serialize)]
#[serde(default)]
pub struct Library {
    pub questions: Vec<Question>,
    pub baskets: Vec<Basket>,
    pub active_basket_id: String,
    pub method_tag_library: Vec<String>,
    pub learning_tag_library: Vec<String>,
    pub vmware_profile: Value,
    #[serde(flatten)]
    pub extra: BTreeMap<String, Value>,
}

impl Library {
    pub fn parse(bytes: &[u8]) -> Result<Self> {
        let mut value: Value = serde_json::from_slice(bytes).map_err(error)?;
        if value.is_array() {
            value = serde_json::json!({"questions":value});
        }
        let object = value.as_object_mut().ok_or("题库格式无效")?;
        if !object.contains_key("method_tag_library") {
            let catalog: Value =
                serde_json::from_str(include_str!("../../src/catalog.json")).map_err(error)?;
            object.insert("method_tag_library".into(), catalog["methods"].clone());
        }
        // Older files accepted string difficulties and widths.
        if let Some(rows) = object.get_mut("questions").and_then(Value::as_array_mut) {
            for row in rows {
                let Some(fields) = row.as_object_mut() else {
                    continue;
                };
                let difficulty = fields
                    .get("difficulty")
                    .and_then(|v| {
                        v.as_f64()
                            .or_else(|| v.as_str()?.parse::<f64>().ok())
                            .filter(|n| n.is_finite())
                    })
                    .unwrap_or(3.0);
                fields.insert("difficulty".into(), Value::from(difficulty));
                if fields.get("created_at").is_some_and(Value::is_null) {
                    fields.remove("created_at");
                }
                if fields.get("id").is_some_and(Value::is_null) {
                    fields.remove("id");
                }
                if let Some(widths) = fields
                    .get_mut("image_widths")
                    .and_then(Value::as_object_mut)
                {
                    for width in widths.values_mut() {
                        let n = width
                            .as_i64()
                            .or_else(|| width.as_str()?.parse().ok())
                            .unwrap_or(68);
                        *width = Value::from(n.clamp(10, 100));
                    }
                }
            }
        }
        let mut library: Self = serde_json::from_value(value).map_err(error)?;
        library.normalize();
        Ok(library)
    }

    pub fn normalize(&mut self) {
        for question in &mut self.questions {
            question.normalize();
        }
        unique(&mut self.method_tag_library);
        for tag in &mut self.learning_tag_library {
            *tag = tag.trim().to_string();
        }
        unique(&mut self.learning_tag_library);
        if self.baskets.is_empty() {
            self.baskets.push(Basket {
                id: id(),
                name: "默认试题篮".into(),
                question_ids: self
                    .extra
                    .remove("basket_question_ids")
                    .and_then(|v| serde_json::from_value(v).ok())
                    .unwrap_or_default(),
                ..Default::default()
            });
        }
        let question_ids: HashSet<_> = self.questions.iter().map(|q| &q.id).collect();
        let mut basket_ids = HashSet::new();
        let mut names = HashSet::new();
        for (index, basket) in self.baskets.iter_mut().enumerate() {
            if basket.id.is_empty() || basket_ids.contains(&basket.id) {
                basket.id = id();
            }
            basket_ids.insert(basket.id.clone());
            basket.name = basket.name.trim().to_string();
            if basket.name.is_empty() {
                basket.name = "未命名试题篮".into();
            }
            if names.contains(&basket.name) {
                basket.name = format!("{}（{}）", basket.name, index + 1);
            }
            names.insert(basket.name.clone());
            basket.question_ids.retain(|id| question_ids.contains(id));
            unique(&mut basket.question_ids);
        }
        if !basket_ids.contains(&self.active_basket_id) {
            self.active_basket_id.clone_from(&self.baskets[0].id);
        }
        if self.vmware_profile.is_null() {
            self.vmware_profile = serde_json::json!({});
        }
    }
}

pub fn unique(items: &mut Vec<String>) {
    let mut seen = HashSet::new();
    items.retain(|v| !v.is_empty() && seen.insert(v.clone()));
}

pub fn basename(path: &str) -> &str {
    path.rsplit(['/', '\\']).next().unwrap_or(path)
}

pub fn image_key(path: &str) -> String {
    format!("{:x}", Sha256::digest(basename(path).as_bytes()))[..12].to_string()
}

pub fn marker(path: &str) -> String {
    format!("[[image:{}]]", image_key(path))
}

pub fn local_image(root: &Path, path: &str) -> Option<PathBuf> {
    let relative = Path::new(path);
    if relative
        .components()
        .any(|part| !matches!(part, Component::Normal(_)))
    {
        return None;
    }
    let root = root.canonicalize().ok()?;
    let target = root.join(relative).canonicalize().ok()?;
    (target.starts_with(&root) && target.is_file()).then_some(target)
}

pub fn atomic_write(path: &Path, bytes: &[u8]) -> Result<()> {
    let parent = path.parent().ok_or("无效的保存路径")?;
    fs::create_dir_all(parent).map_err(error)?;
    let mut file = tempfile::NamedTempFile::new_in(parent).map_err(error)?;
    file.write_all(bytes).map_err(error)?;
    file.as_file().sync_all().map_err(error)?;
    file.persist(path).map_err(error)?;
    Ok(())
}

pub struct Store {
    pub root: PathBuf,
    pub inner: Mutex<Snapshot>,
    _lock: File,
}

#[derive(Clone, Serialize)]
pub struct Snapshot {
    pub revision: u64,
    pub library: Library,
    pub image_keys: BTreeMap<String, String>,
    pub location: String,
}

fn image_keys(library: &Library) -> BTreeMap<String, String> {
    library
        .questions
        .iter()
        .flat_map(Question::images)
        .map(|path| (path.clone(), image_key(path)))
        .collect()
}

impl Store {
    pub fn open(app: &tauri::AppHandle) -> Result<Self> {
        let source = std::env::var_os("PM_PREVIEW_SOURCE")
            .map(PathBuf::from)
            .unwrap_or_else(|| Path::new(env!("CARGO_MANIFEST_DIR")).join("../../data"));
        let root = std::env::var_os("PM_PREVIEW_DATA")
            .map(PathBuf::from)
            .map(Ok)
            .unwrap_or_else(|| app.path().app_local_data_dir().map_err(error))?;
        Self::open_paths(root, &source)
    }

    pub fn open_paths(root: PathBuf, source: &Path) -> Result<Self> {
        fs::create_dir_all(&root).map_err(error)?;
        let root = root.canonicalize().map_err(error)?;
        if let Ok(source) = source.canonicalize() {
            if root == source || source.starts_with(&root) {
                return Err("新版题库目录必须与原题库分开".into());
            }
        }
        let lock = OpenOptions::new()
            .write(true)
            .create(true)
            .truncate(false)
            .open(root.join(".lock"))
            .map_err(error)?;
        lock.try_lock()
            .map_err(|_| "题库已经打开，或当前目录无法加锁")?;
        let file = root.join("questions.json");
        if !file.exists() {
            initialize(&root, source, &file)?;
        }
        let mut library = Library::parse(&fs::read(file).map_err(error)?)?;
        let blanks = regex::Regex::new(r"\\+underline\{(?:\\+quad\s*)+\}|\\+blank\b|_{3,}|＿{2,}")
            .map_err(error)?;
        for q in &mut library.questions {
            if q.question_type == "填空题" {
                q.stem = blanks.replace_all(&q.stem, "@").into_owned();
            }
        }
        let keys = image_keys(&library);
        Ok(Self {
            inner: Mutex::new(Snapshot {
                revision: 0,
                library,
                image_keys: keys,
                location: root.display().to_string(),
            }),
            root,
            _lock: lock,
        })
    }

    pub fn commit(
        &self,
        revision: u64,
        mut library: Library,
        files: HashMap<String, String>,
    ) -> Result<Snapshot> {
        let mut snapshot = self.inner.lock().map_err(error)?;
        if snapshot.revision != revision {
            return Err("题库已有更新，请重新载入后再保存".into());
        }
        library.normalize();
        let retained: HashSet<_> = library
            .questions
            .iter()
            .flat_map(Question::images)
            .cloned()
            .collect();
        let previous: HashSet<_> = snapshot
            .library
            .questions
            .iter()
            .flat_map(Question::images)
            .cloned()
            .collect();
        let mut created = Vec::new();
        let result = (|| {
            for (name, data) in files {
                if !retained.contains(&name) {
                    continue;
                }
                let filename = name.strip_prefix("images/").ok_or("配图路径无效")?;
                if !filename.ends_with(".png")
                    || !filename
                        .bytes()
                        .all(|b| b.is_ascii_alphanumeric() || b"-_.".contains(&b))
                {
                    return Err("配图文件名无效".into());
                }
                let payload = data
                    .strip_prefix("data:image/png;base64,")
                    .ok_or("配图格式无效")?;
                if payload.len() > 24 * 1024 * 1024 {
                    return Err("配图过大".into());
                }
                let bytes = STANDARD.decode(payload).map_err(error)?;
                if !bytes.starts_with(b"\x89PNG\r\n\x1a\n") {
                    return Err("配图格式无效".into());
                }
                let target = self.root.join(name);
                if target.exists() {
                    return Err("配图名称冲突".into());
                }
                atomic_write(&target, &bytes)?;
                created.push(target);
            }
            atomic_write(
                &self.root.join("questions.json"),
                &serde_json::to_vec_pretty(&library).map_err(error)?,
            )
        })();
        if let Err(e) = result {
            for path in created {
                let _ = fs::remove_file(path);
            }
            return Err(e);
        }
        snapshot.revision += 1;
        snapshot.image_keys = image_keys(&library);
        snapshot.library = library;
        for path in previous
            .difference(&retained)
            .filter(|p| p.starts_with("images/"))
        {
            if let Some(target) = local_image(&self.root, path) {
                if target.parent() == Some(self.root.join("images").as_path()) {
                    let _ = fs::remove_file(target);
                }
            }
        }
        Ok(snapshot.clone())
    }
}

fn initialize(root: &Path, source: &Path, file: &Path) -> Result<()> {
    if !source.join("questions.json").is_file() {
        let library = Library::parse(include_bytes!("../../src/sample.json"))?;
        return atomic_write(file, &serde_json::to_vec_pretty(&library).map_err(error)?);
    }
    let source = source.canonicalize().map_err(error)?;
    let original = fs::read(source.join("questions.json")).map_err(error)?;
    let mut library = Library::parse(&original)?;
    let mut remapped = false;
    for q in &mut library.questions {
        for path in q.image_paths.iter_mut().chain(&mut q.analysis_image_paths) {
            let raw_path = Path::new(path);
            if !raw_path.is_absolute() {
                if let Some(input) = local_image(&source, path) {
                    atomic_write(&root.join(&*path), &fs::read(input).map_err(error)?)?;
                }
                continue;
            }
            if !raw_path.is_file() {
                continue;
            }
            let output = format!("images/legacy-{}/{}", image_key(path), basename(path));
            atomic_write(&root.join(&output), &fs::read(raw_path).map_err(error)?)?;
            *path = output;
            remapped = true;
        }
        q.image_path = q.image_paths.first().cloned().unwrap_or_default();
    }
    let bytes = if remapped {
        serde_json::to_vec_pretty(&library).map_err(error)?
    } else {
        original
    };
    atomic_write(file, &bytes)
}

#[tauri::command]
pub fn load_library(store: State<'_, Store>) -> Result<Snapshot> {
    Ok(store.inner.lock().map_err(error)?.clone())
}

#[tauri::command]
pub fn save_library(
    store: State<'_, Store>,
    revision: u64,
    library: Library,
    images: HashMap<String, String>,
) -> Result<Snapshot> {
    store.commit(revision, library, images)
}

pub fn image_data(path: &Path) -> Result<String> {
    let bytes = fs::read(path).map_err(error)?;
    let mime = match path
        .extension()
        .and_then(|e| e.to_str())
        .unwrap_or("")
        .to_ascii_lowercase()
        .as_str()
    {
        "jpg" | "jpeg" => "image/jpeg",
        "gif" => "image/gif",
        "webp" => "image/webp",
        "bmp" => "image/bmp",
        "tif" | "tiff" => "image/tiff",
        _ => "image/png",
    };
    Ok(format!("data:{mime};base64,{}", STANDARD.encode(bytes)))
}

#[tauri::command]
pub fn read_image(store: State<'_, Store>, path: String) -> Result<String> {
    if !store
        .inner
        .lock()
        .map_err(error)?
        .image_keys
        .contains_key(&path)
    {
        return Err("配图不在当前题库".into());
    }
    image_data(&local_image(&store.root, &path).ok_or("找不到配图")?)
}

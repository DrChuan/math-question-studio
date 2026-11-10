use crate::{
    images::replace_code,
    library::{
        basename, error, id, local_image, marker, today, unique, Library, Result, Snapshot, Store,
    },
};
use serde_json::{json, Value};
use std::{
    collections::HashSet,
    fs,
    io::{Read, Write},
    path::{Component, Path},
};
use tauri::{AppHandle, Manager};
use tauri_plugin_dialog::DialogExt;
use zip::{write::SimpleFileOptions, CompressionMethod, ZipArchive, ZipWriter};

const STATE: &str = "question_bank.json";
const MANIFEST: &str = "manifest.json";
const FORMAT: &str = "Math Question Studio Backup";

fn write_backup(root: &Path, library: Library, destination: &Path) -> Result<()> {
    let output = tempfile::NamedTempFile::new_in(destination.parent().ok_or("保存路径无效")?)
        .map_err(error)?;
    let mut writer = ZipWriter::new(output.reopen().map_err(error)?);
    let options = SimpleFileOptions::default().compression_method(CompressionMethod::Deflated);
    let mut payload = library;
    let mut entries = Vec::new();
    for q in &mut payload.questions {
        let original_widths = q.image_widths.clone();
        for (field, paths) in [
            ("image_paths", &mut q.image_paths),
            ("analysis_image_paths", &mut q.analysis_image_paths),
        ] {
            let mut archived = Vec::new();
            for (i, path) in paths.iter().enumerate() {
                let Some(source) = local_image(root, path) else {
                    continue;
                };
                let ext = source.extension().and_then(|s| s.to_str()).unwrap_or("png");
                let safe_id =
                    q.id.chars()
                        .filter(char::is_ascii_alphanumeric)
                        .collect::<String>();
                let name = format!("images/{safe_id}-{field}-{i}.{}", ext.to_ascii_lowercase());
                if field == "analysis_image_paths" {
                    q.analysis = replace_code(&q.analysis, path, &name);
                }
                if let Some(width) = original_widths.get(&marker(path)) {
                    q.image_widths.remove(&marker(path));
                    q.image_widths.insert(marker(&name), *width);
                }
                entries.push((name.clone(), source));
                archived.push(name);
            }
            *paths = archived;
        }
        q.image_path = q.image_paths.first().cloned().unwrap_or_default();
    }
    let mut payload = serde_json::to_value(payload).map_err(error)?;
    payload.as_object_mut().unwrap().remove("vmware_profile");
    for (name, value) in [
        (
            MANIFEST,
            json!({"format":FORMAT,"version":1,"state_file":STATE}),
        ),
        (STATE, payload),
    ] {
        writer.start_file(name, options).map_err(error)?;
        serde_json::to_writer_pretty(&mut writer, &value).map_err(error)?;
    }
    for (name, path) in entries {
        writer.start_file(name, options).map_err(error)?;
        std::io::copy(&mut fs::File::open(path).map_err(error)?, &mut writer).map_err(error)?;
    }
    writer.finish().map_err(error)?.sync_all().map_err(error)?;
    output.persist(destination).map_err(error)?;
    Ok(())
}

fn entry(archive: &mut ZipArchive<fs::File>, name: &str, max: u64) -> Result<Vec<u8>> {
    let mut bytes = Vec::new();
    archive
        .by_name(name)
        .map_err(error)?
        .take(max + 1)
        .read_to_end(&mut bytes)
        .map_err(error)?;
    if bytes.len() as u64 > max {
        return Err("备份内容超过大小限制".into());
    }
    Ok(bytes)
}

fn restore(store: &Store, path: &Path, merge: bool) -> Result<Snapshot> {
    let current = store.inner.lock().map_err(error)?.clone();
    let mut archive = ZipArchive::new(fs::File::open(path).map_err(error)?).map_err(error)?;
    let manifest: Value =
        serde_json::from_slice(&entry(&mut archive, MANIFEST, 1024 * 1024)?).map_err(error)?;
    if manifest["format"] != FORMAT || manifest["version"] != 1 {
        return Err("这不是受支持的题库备份文件".into());
    }
    let mut incoming = Library::parse(&entry(&mut archive, STATE, 64 * 1024 * 1024)?)?;
    let folder = format!("images/restore-{}", id());
    let target = store.root.join(&folder);
    let result = (|| {
        let mut seen = HashSet::new();
        for q in &mut incoming.questions {
            for path in q.image_paths.iter_mut().chain(&mut q.analysis_image_paths) {
                let parts = Path::new(path).components().collect::<Vec<_>>();
                let name = basename(path);
                if parts.len() != 2
                    || !path.starts_with("images/")
                    || name.contains('\\')
                    || parts.iter().any(|p| !matches!(p, Component::Normal(_)))
                {
                    return Err("备份中包含不安全的图片路径".into());
                }
                if seen.insert(path.clone()) {
                    let bytes = entry(&mut archive, path, 32 * 1024 * 1024)?;
                    fs::create_dir_all(&target).map_err(error)?;
                    let mut image = fs::File::create(target.join(name)).map_err(error)?;
                    image.write_all(&bytes).map_err(error)?;
                    image.sync_all().map_err(error)?;
                }
                *path = format!("{folder}/{name}");
            }
            q.image_path = q.image_paths.first().cloned().unwrap_or_default();
        }
        let mut library = if merge {
            merge_libraries(current.library.clone(), incoming)
        } else {
            incoming
        };
        library.vmware_profile = current.library.vmware_profile;
        store.commit(current.revision, library, Default::default())
    })();
    if result.is_err() && target.is_dir() {
        let _ = fs::remove_dir_all(target);
    }
    result
}

fn merge_libraries(mut current: Library, incoming: Library) -> Library {
    for question in incoming.questions {
        if let Some(row) = current.questions.iter_mut().find(|q| q.id == question.id) {
            *row = question;
        } else {
            current.questions.push(question);
        }
    }
    for mut basket in incoming.baskets {
        if let Some(existing) = current.baskets.iter_mut().find(|b| b.id == basket.id) {
            existing.question_ids.extend(basket.question_ids);
            unique(&mut existing.question_ids);
        } else {
            let base = basket.name.clone();
            let mut suffix = 2;
            while current.baskets.iter().any(|b| b.name == basket.name) {
                basket.name = format!("{base}（导入 {suffix}）");
                suffix += 1;
            }
            current.baskets.push(basket);
        }
    }
    current
        .method_tag_library
        .extend(incoming.method_tag_library);
    current
        .learning_tag_library
        .extend(incoming.learning_tag_library);
    current
}

#[tauri::command]
pub async fn backup_library(app: AppHandle) -> Result<Option<String>> {
    tauri::async_runtime::spawn_blocking(move || {
        let Some(file) = app
            .dialog()
            .file()
            .set_title("备份题库")
            .add_filter("题库备份", &["zip"])
            .set_file_name(format!("题库备份-{}.zip", today()))
            .blocking_save_file()
        else {
            return Ok(None);
        };
        let store = app.state::<Store>();
        let state = store.inner.lock().map_err(error)?.library.clone();
        let target = file.into_path().map_err(error)?.with_extension("zip");
        write_backup(&store.root, state, &target)?;
        Ok(Some(target.display().to_string()))
    })
    .await
    .map_err(error)?
}

#[tauri::command]
pub async fn restore_library(app: AppHandle, merge: bool) -> Result<Option<Snapshot>> {
    tauri::async_runtime::spawn_blocking(move || {
        let Some(file) = app
            .dialog()
            .file()
            .set_title("恢复题库")
            .add_filter("题库备份", &["zip"])
            .blocking_pick_file()
        else {
            return Ok(None);
        };
        let store = app.state::<Store>();
        restore(&store, &file.into_path().map_err(error)?, merge).map(Some)
    })
    .await
    .map_err(error)?
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::library::Question;

    #[test]
    fn isolated_copy_and_backup_round_trip() {
        let scratch = tempfile::tempdir().unwrap();
        let original = scratch.path().join("original");
        let preview = scratch.path().join("preview");
        fs::create_dir_all(original.join("images")).unwrap();
        let image = "images/source.png";
        fs::write(original.join(image), include_bytes!("../icons/icon.png")).unwrap();
        let original_bytes = serde_json::to_vec_pretty(&json!({
            "questions": [{ "id": "legacy", "stem": "题干", "image_path": image,
                "analysis": "解析 ![图片](image:source.png){width=40%}",
                "analysis_image_paths": [image], "image_widths": {(marker(image)): 40} }],
            "basket_question_ids": ["legacy"],
            "vmware_profile": {"fixture": "kept"},
            "extension_field": true
        }))
        .unwrap();
        fs::write(original.join("questions.json"), &original_bytes).unwrap();
        let store = Store::open_paths(preview, &original).unwrap();
        assert_eq!(
            fs::read(store.root.join("questions.json")).unwrap(),
            original_bytes
        );
        let snapshot = store.inner.lock().unwrap().clone();
        assert_eq!(snapshot.library.baskets[0].question_ids, vec!["legacy"]);
        assert_eq!(snapshot.library.questions[0].image_paths, vec![image]);

        let archive = scratch.path().join("backup.zip");
        write_backup(&store.root, snapshot.library.clone(), &archive).unwrap();
        let mut changed = snapshot.library;
        changed
            .questions
            .push(serde_json::from_value::<Question>(json!({"id":"new","stem":"新增"})).unwrap());
        store
            .commit(0, changed.clone(), Default::default())
            .unwrap();
        assert!(store.commit(0, changed, Default::default()).is_err());
        let merged = restore(&store, &archive, true).unwrap();
        assert_eq!(merged.library.questions.len(), 2);
        let replaced = restore(&store, &archive, false).unwrap();
        let q = &replaced.library.questions[0];
        assert_eq!(replaced.library.questions.len(), 1);
        assert_eq!(replaced.library.vmware_profile["fixture"], "kept");
        assert_eq!(replaced.library.extra["extension_field"], true);
        let analysis_path = &q.analysis_image_paths[0];
        assert!(q
            .analysis
            .contains(&crate::library::image_key(analysis_path)));
        assert_eq!(q.image_widths[&marker(analysis_path)], 40);
        assert!(local_image(&store.root, analysis_path).is_some());
        assert_eq!(
            fs::read(original.join("questions.json")).unwrap(),
            original_bytes
        );
        assert!(original.join(image).is_file());
    }
}

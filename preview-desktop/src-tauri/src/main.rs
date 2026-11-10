#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod backup;
mod export;
mod images;
mod library;
mod recognition;

use tauri::Manager;
use tauri_plugin_dialog::DialogExt;

fn main() {
    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_clipboard_manager::init())
        .manage(export::ExportCache::default())
        .setup(|app| {
            match library::Store::open(app.handle()) {
                Ok(store) => {
                    app.manage(store);
                }
                Err(error) => {
                    let handle = app.handle().clone();
                    app.dialog()
                        .message(error)
                        .title("无法打开题库")
                        .show(move |_| handle.exit(1));
                }
            }
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            library::load_library,
            library::save_library,
            library::read_image,
            backup::backup_library,
            backup::restore_library,
            export::render_export,
            export::save_export,
            export::clear_export,
            recognition::recognition_settings,
            recognition::recognition_save_settings,
            recognition::recognition_choose_image,
            recognition::recognition_paste_image,
            recognition::recognition_call,
        ])
        .run(tauri::generate_context!())
        .expect("无法启动题库");
}

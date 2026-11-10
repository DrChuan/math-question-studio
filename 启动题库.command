#!/bin/zsh
set -e
cd -- "${0:A:h}"
root="$PWD"
export PATH="/opt/homebrew/bin:/usr/local/bin:$HOME/.cargo/bin:/Library/TeX/texbin:$PATH"
export PM_PREVIEW_SOURCE="$root/data"

app="$root/preview-desktop/src-tauri/target/release/bundle/macos/Math Question Studio.app"
if [[ ! -d "$app" && -d "$root/preview-desktop/src-tauri/target/universal-apple-darwin/release/bundle/macos/Math Question Studio.app" ]]; then
  app="$root/preview-desktop/src-tauri/target/universal-apple-darwin/release/bundle/macos/Math Question Studio.app"
fi
if [[ ! -d "$app" ]]; then
  cd "$root/preview-desktop"
  command -v npm >/dev/null || { echo "构建题库需要 Node.js 和 npm。"; exit 1; }
  command -v cargo >/dev/null || { echo "构建题库需要 Rust 和 Cargo。"; exit 1; }
  [[ -x node_modules/.bin/tauri ]] || npm ci
  npm run desktop -- build --bundles app
fi
open "$app" --env "PM_PREVIEW_SOURCE=$PM_PREVIEW_SOURCE"

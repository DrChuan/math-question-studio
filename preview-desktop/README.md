# 高中数学题目管理器桌面版

在项目根目录双击 `启动题库.command`。已经构建时会直接启动 macOS 应用。首次从源码构建需要 Node.js、npm、Rust 和系统编译工具。

桌面版采用 React、TypeScript、Tauri 和 Rust。首次打开会从原项目的 `data/questions.json` 及其引用配图建立副本。macOS 题库保存在 `~/Library/Application Support/lab.chuan.problemmanager.preview`，实际位置也显示在窗口底部。从试用版升级时会直接读取同一份题库和百炼设置，可通过原版兼容的 ZIP 备份进行合并或覆盖恢复。原版仍用 `app.py` 启动。

截图识别在「识别题目图片」中打开。到「百炼设置」填写自己的 API Key 和地域，选择或粘贴截图，按需裁剪，再点击「识别题文」。可以校对 LaTeX 源码、查看排版、复制全文或填入题干。只有点击识别才会提交截图。题库的 Key 和 Mathdraw 分开保存。

排版导出需要 XeLaTeX 和 xdvipdfmx。双向定位的 PDF 预览使用 Ghostscript 或 Poppler，macOS 的 MacTeX 可提供相关工具。没有页面渲染工具时，应用仍可生成 LaTeX 和 PDF，并使用系统 WebView 的 PDF 预览。

## 开发

在本目录运行 `npm ci`，然后用 `npm run desktop -- dev` 启动。macOS 构建命令为 `npm run desktop -- build --target universal-apple-darwin --bundles app`。Windows 构建可在 Windows 上运行 `npm run desktop -- build --bundles nsis`。原程序的 Word/VMware 实验模块仍在原项目中，原版导出入口使用的 XeLaTeX/PDF 流程已迁入桌面版。

`cargo test --manifest-path src-tauri/Cargo.toml` 检查数据隔离和 ZIP 往返。安装 XeLaTeX 后加 `-- --include-ignored` 可一起验证含配图和题目定位的 PDF 编译。

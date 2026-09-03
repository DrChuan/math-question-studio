# Math Question Studio

一个使用 PySide6 开发的高中数学题库桌面程序，适合录入、整理、筛选和导出含 LaTeX 公式的数学题目。

## 功能

- 支持单选题、多选题、填空题、解答题
- 题干、选项、答案、解析、备注均可输入行内 LaTeX
- 离线数学公式预览，不依赖网络
- 高中数学知识点树与可自定义的方法/思想标签
- 题目难度、来源、创建日期与配图
- 多标签检索、全文搜索与可保存试题篮
- 试题篮导出为 XeLaTeX 源文件与 PDF
- 支持选择是否输出答案解析、备注和解答题答题留白

## 启动

建议在 Python 3.9+ 环境中安装依赖：

```bash
pip install -r requirements.txt
python app.py
```

macOS 上如使用 Conda：

```bash
conda run -n base python app.py
```

首次启动会自动写入测试题。个人题库数据保存在 `data/questions.json`，并被 Git 忽略，避免题目内容和本地图片被上传。

## 公式输入

- 行内公式：`$x^2+1$`
- 分式：`$\\dfrac{1}{2}$`
- 填空横线：`$\\blank$`

导出时所有公式会保持行内版式。

## 导出 PDF

PDF 导出依赖 XeLaTeX。macOS 可以安装 MacTeX 或 BasicTeX；安装后，程序会同时生成可编辑的 `.tex` 和排版后的 `.pdf`。

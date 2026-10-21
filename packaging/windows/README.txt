Windows 一键打包说明

这个 export 文件夹目前是打包材料，不是已经生成的 exe。
里面已经带上当前题目、配图和完整程序源码。

使用方法：
1. 把整个 export 文件夹复制到 Windows 10/11 64 位电脑上的可写目录。
2. 联网，双击 build.bat。首次运行会下载 Python（如未安装）、程序依赖和便携 LaTeX。
3. 等待显示 SUCCESS。窗口会保留，不会自动关闭。
4. 打开 dist/MathQuestionManager/MathQuestionManager.exe。
5. 以后换电脑，只需搬走整个 dist/MathQuestionManager 文件夹，无需再打包或安装依赖。

第一次构建会下载数百 MB，并占用较多磁盘空间，请预留约 3 GB 可用空间。
构建时必须能连接 python.org、PyPI、GitHub 和 CTAN。下载失败可以重新双击 build.bat 重试。
打包使用单独的构建环境，不会向已有 conda 环境安装依赖，也不修改系统 PATH。
如需安装 Python，会以当前用户身份安装到 _build/python，无需管理员权限。

题库：data/questions.json；配图：data/images。
图片已经使用相对路径，保留题目、标签、试题篮及图片大小设置。
不包含 Mac 虚拟机配置或密码。

成功前会用生成的 exe 自检：读取题库、检查配图、真实公式渲染、PDF 编译与 Qt 页面加载。
失败时不会假装成功，也不会覆盖已有 dist/MathQuestionManager 中的题库。
再次构建会创建带时间后缀的新目录，旧版与其题库保留不动。
出错时，把 build.log 发回以便排查。

# 启动图片横幅验收

用户已明确允许横幅使用图片，并要求优先还原参考图。本次将先前的字符画替换为参考图中直接裁取的图案和 TermCode 字标，保留原图的轮廓、斜体描边和橙金色光晕。版本号、工作目录与就绪提示仍使用真实运行信息。

## 检查项目

- [x] 图案和字标来自原图，资源中的对应像素没有重绘或改写。证据：[源像素核对](evidence/image-banner/source-match.json)。
- [x] 原生 Sixel 图片组件输出正确，独立解码后可见参考图的图案、字体和光晕。证据：[组件输出预览](evidence/image-banner/native-sixel-preview.png)、[渲染信息](evidence/image-banner/native-renderer.json)。该预览是组件输出解码结果，不是 Windows 桌面截图。
- [x] 图片与历史消息共同滚动；100/180 列下可翻回横幅，缩至 38×20 后输入区可用，横幅高度受限。证据：两平台测试报告中的 `test_native_image_scrolls_with_history_and_resizes`。
- [x] 对话、供应商选择、流式显示、Markdown、异常恢复和退出回归通过。Windows：57 项，31.98 秒；Linux / WSL：57 项，65.17 秒。证据：[Windows 测试](evidence/image-banner-pytest.xml)、[Linux 测试](evidence/image-banner-linux.xml)。
- [x] 真实 tmux 中启动应用，使用已有 DeepSeek 配置完成两轮对话，第二轮正确回忆 `TC-1027`；100/80/38 列输入区可用，`/exit` 退出码为 0，退出前后终端模式一致。证据：[实际结果](evidence/image-banner/live-results.json)、[首屏](evidence/image-banner/live-startup-100.txt)、[第二轮](evidence/image-banner/live-second-reply.txt)、[退出](evidence/image-banner/live-exit.txt)。此 tmux 环境未提供图片能力，实际观察到的是文字兼容提示，不能据此声称真实终端图片显示已验收。
- [x] 用户配置、供应商、历史、输入处理、流式请求与系统提示词保持一致；新增依赖仅用于图片显示。发行包包含与工作区相同的图片和源码，未包含真实配置。证据：[修改范围](evidence/image-banner/scope-check.json)、[质量与打包](evidence/image-banner/quality-checks.json)。

## 运行条件与验证边界

验收日期：2026-10-27。`ruff check`、`ruff format --check`、`mypy`、编译检查、离线锁文件检查和发行包构建通过。

图片由终端的 Sixel / Kitty 图形能力显示；不支持图片时显示文字提示，仍可聊天。Windows 可使用 Windows Terminal 1.22 或更新版本，本机安装版本已核对为 1.24.12741.0。依据：[textual-image 终端支持说明](https://github.com/lnqs/textual-image#supported-terminals)。

原图资源已逐像素核对。实际显示会根据终端单元尺寸缩放，Sixel 会量化颜色，因此不同终端不保证逐屏幕像素一致。尚未通过 Windows Terminal 桌面实屏观察；已完成的图片验证为实际组件编码输出的独立解码与布局测试。

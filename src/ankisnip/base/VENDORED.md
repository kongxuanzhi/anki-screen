# Vendored base: mysphinx-snip 0.0.1

- 来源: PyPI sdist `mysphinx_snip-0.0.1.tar.gz`（`pip download mysphinx-snip --no-binary :all:`）
- 许可证: **MIT**（`pyproject.toml` 中 `license = { text = "MIT" }`，PKG-INFO `License: MIT`）
  上游 sdist 未附带 LICENSE 文件正文，此处按 MIT 条款使用并保留版权声明。
- 上游依赖: PySide6>=6.5、pynput>=1.7
- 仓库地址: 上游 PyPI 页面未公布源码仓库 URL，因此采用 **vendored（内嵌源码）** 而非 git submodule。

## 相对上游的改动（仅两处，均为任务书许可的注入点）

1. `annotations.py`
   - `ToolType` 增加 `MASK = "mask"`
   - 新增 `MaskAnnotation` 类（拖拽创建、半透明渲染、命中检测、移动、`to_norm()/from_norm()`）
   - 注册进 `_TOOL_CLASSES`
   - `AnnotationStack` 增加 `remove()`（删除命中的标注，供挖空删除使用）
2. `toolbar.py`
   - `_TOOLS` 增加 `(ToolType.MASK, "▢", "挖空（不烧录进图片）")`

其余文件（capture / overlay / app / pin / config / permissions）与上游逐字节一致。

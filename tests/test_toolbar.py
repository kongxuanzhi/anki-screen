"""编辑器工具条：7 色调色板 + 两行上下文布局。"""

from __future__ import annotations

from PySide6.QtGui import QColor

from ankisnip.base.annotations import ToolType
from ankisnip.editor_toolbar import EditorToolbar
from ankisnip.mask_colors import DEFAULT_COLOR, MASK_COLORS, normalize


def _toolbar(color: str = DEFAULT_COLOR) -> EditorToolbar:
    return EditorToolbar(QColor(color), 3, 24)


def test_palette_has_exactly_the_template_colors():
    tb = _toolbar()
    assert len(tb._color_buttons) == 7
    assert set(tb._color_buttons) == {value for value, _label in MASK_COLORS}
    # 选中的色块必须是当前颜色
    assert tb._color.name() in tb._color_buttons


def test_clicking_swatch_emits_supported_color():
    tb = _toolbar()
    fired: list = []
    tb.colorChanged.connect(fired.append)

    tb._select_color("#7500ca")

    assert fired and fired[-1].name() == "#7500ca"
    assert tb._color_buttons["#7500ca"].isChecked()
    assert not tb._color_buttons[DEFAULT_COLOR].isChecked()


def test_unsupported_color_is_normalized_to_nearest_template_color():
    tb = _toolbar("#FF3B30")
    assert tb._color.name() == "#ff3e05", "历史默认红应收敛到模板的红色"
    assert tb._color.name() in tb._color_buttons


def test_context_row_follows_editing_and_tool():
    tb = _toolbar()
    tb.show()  # 让 isVisible() 有意义
    try:
        tb.set_editing(False)
        assert not tb._context.isVisible(), "选区态不显示第二行"

        tb.set_editing(True)
        tb.select_tool(ToolType.MASK)
        assert tb._context.isVisible()
        assert tb._ctx_color.isVisible()
        assert not tb._ctx_width.isVisible()
        assert not tb._ctx_font.isVisible()

        tb.select_tool(ToolType.ARROW)
        assert tb._ctx_width.isVisible()
        assert not tb._ctx_font.isVisible()

        tb.select_tool(ToolType.TEXT)
        assert tb._ctx_font.isVisible()
        assert not tb._ctx_width.isVisible()

        tb.select_tool(None)
        assert not tb._context.isVisible(), "选择/移动工具不需要第二行"
    finally:
        tb.hide()


def test_secondary_tools_and_save_image_live_in_overflow_menu():
    tb = _toolbar()
    titles = [a.text() for a in tb._more_menu.actions()]
    # 子菜单 + 保存图片 + 删除选中
    assert "更多标注" in titles
    assert "保存图片…" in titles
    submenu = next(a.menu() for a in tb._more_menu.actions() if a.menu())
    sub_titles = [a.text() for a in submenu.actions()]
    assert sub_titles == ["矩形", "椭圆", "高亮"]


def test_mask_count_badge():
    tb = _toolbar()
    tb.set_mask_count(3)
    assert tb._mask_label.text() == "▢ 3"
    assert not tb._mask_label.isHidden()

    tb.set_mask_count(0)
    assert tb._mask_label.isHidden()


def test_normalize_helper():
    assert normalize("#057fff") == "#057fff"
    assert normalize("#00aa55") == "#40af04", "绿色应映射到模板的绿色"
    assert normalize("not-a-color") == DEFAULT_COLOR

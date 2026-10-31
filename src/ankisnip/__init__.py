"""AnkiSnip —— 截图 → 标注 / 挖空 → 保存为 Anki 卡片（独立常驻托盘软件）。

模块分层
--------
``base/``       vendored 的开源截图基座 mysphinx-snip 0.0.1（MIT），仅注入了
                ``MASK`` 工具类型与 ``AnnotationStack.remove``
``coords.py``   坐标变换唯一出入口（image / widget / norm）
``editor_*``    可缩放平移的编辑器（复用基座标注栈）
``rasterize.py`` 烧录导出（排除挖空）
``kong/``       现有 Anki 插件 ``kong`` 字段的序列化 / 加密（Python 移植）
``anki_*``      AnkiConnect 直连与 genanki 离线导出
``saver.py``    保存链路编排
``app.py``      托盘 / 全局热键 / 会话编排（复用基座 SnipApp）
"""

__version__ = "0.1.0"

# AnkiSnip —— 截图 → 标注 / 挖空 → 保存为 Anki 卡片

一个 Windows / macOS **独立常驻系统托盘**软件：

1. 全局热键（`Ctrl/Cmd + Shift + A`）触发区域截图；
2. 在编辑器里画**箭头**、写**文字**、拖**挖空**；
3. 保存时 **箭头和文字直接烧录进图片**，**只有挖空以坐标数据写入 `kong` 字段**；
4. Anki 开着就通过 **AnkiConnect** 直接 `addNote` 到指定牌组；Anki 没开就生成
   **`.apkg`** 双击导入。

截图 / 选区 / 箭头 / 文字 / 撤销重做 / 托盘 / 全局热键全部复用开源基座
**`mysphinx-snip` 0.0.1（MIT）**；本项目只新增了**挖空工具**与 **Anki 保存链路**
（外加缩放 / 平移画布，因为基座没有）。详见 [`docs/00-基座选型报告.md`](docs/00-基座选型报告.md)。

---

## 快速开始

```bash
python -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements.txt

python -m ankisnip          # 或：python packaging/ankisnip_main.py
```

启动后程序常驻托盘，没有主窗口。

### 第一次运行

| 平台 | 需要授权 | 位置 |
|---|---|---|
| macOS | **屏幕录制**（截图必需，否则只拍到壁纸） | 系统设置 → 隐私与安全性 → 屏幕录制 |
| macOS | 辅助功能（**仅 pynput 兜底路径**需要） | 同上 → 辅助功能 |
| Windows | 无 | — |

全局热键默认走**系统原生注册**（macOS Carbon `RegisterEventHotKey` /
Windows `RegisterHotKey`），**不需要辅助功能权限**；只有原生注册失败时才回落到
pynput，此时 macOS 才要求「辅助功能」。当前用的是哪个后端、为什么失败，
可以在托盘菜单 →「快捷键设置…」里看到。

> macOS 上授权对象必须是**运行程序的那个应用本身**。从终端跑就授权 Terminal / iTerm；
> 打包成 `.app` 后必须授权 `AnkiSnip.app`。**授权后要完全退出再重启**才生效。
> 托盘右键菜单 →「权限设置…」可一键打开对应面板。

**托盘图标：菜单交给系统原生托盘**。macOS 上左键 / 右键都会弹出菜单（系统标准
行为，「截图」在第一项，「快捷键设置… / 打开牌组目录 / 设置牌组目录…」紧随其后；
设置 / 归档 / Anki 连接 / 权限 / 退出也都在里面）；Windows / Linux 上仍是左键 =
直接截图、右键 = 菜单。

热键万一没生效也能用：托盘菜单 →「截图」，或从菜单里打开「快捷键设置…」查原因。

### Anki 侧准备

1. Anki 安装插件 **AnkiConnect**（代码 `2055492159`），保持默认端口 `8765`。
2. 确认存在笔记类型 **`图片挖空模板sle1_0`**（字段 `pic_kong` / `question` / `relation`）。
   名字不一样也没关系：在「设置…」→「模板与保存」里直接**导入 .apkg 模板**，
   会自动提取笔记类型、字段和模板；也可以在「高级」里手工改字段映射。
3. 默认牌组名与笔记类型同名，可在设置里改。

不做以上准备也能用 —— 检测不到 AnkiConnect 时会自动导出 `.apkg`。

---

## 使用

截图和标注在**同一个界面**里完成，不会弹出第二个窗口：

1. 框选区域：拖出矩形。默认状态下在选区内拖动 = 移动选区，拖八个手柄 = 缩放选区。
2. **双击选区**，或者**直接点工具条上的某个工具**，就进入绘制模式，原地开始画。
3. 画完点绿色「保存到 Anki」。

| 操作 | 方式 |
|---|---|
| 触发截图 | 全局热键 / 托盘菜单 →「截图」 |
| 改快捷键 | 托盘菜单 →「快捷键设置…」→「录制…」→ 直接按下组合键（无需重启） |
| 进入绘制模式 | 双击选区，或点工具条 `▢`（挖空）/ `↗`（箭头）/ `T`（文字）（选区同时被锁住） |
| 选择 / 移动标注 | 工具条 `✥` |
| 回到选区 | 工具条最左的 `⛶`（已画的标注保留） |
| 画箭头 / 写文字 | 工具条 `↗` / `T` |
| 拖挖空 | 工具条 `▢`，在选区内拖一个矩形 |
| 选颜色 | 绘制态**第二行**的 7 个色块，或数字键 `1`–`7`（模板仅支持这 7 色，透明度固定） |
| 矩形 / 椭圆 / 高亮 | 工具条「⋯」→ 矩形 / 椭圆 / 高亮 |
| 确认挖空挡住了什么 | **鼠标移到挖空上** —— 遮挡块变半透明露出底下的内容，移开恢复实心 |
| 删除挖空 | 选中按 Delete，或右键点它，或双击它 |
| 缩放 | 滚轮（以鼠标为锚点）／工具条百分比菜单（放大 / 缩小 / 适应窗口 / 100%） |
| 平移 | 按住空格拖，或中键拖 |
| 撤销 / 重做 | `Ctrl/Cmd+Z` / `Ctrl/Cmd+Y` |
| 复制到剪贴板 | 工具条「复制」——**界面不关**，右下角弹「已复制到剪贴板」，可以接着改接着复制 |
| 另存为图片 | 工具条「保存图片…」——**连遮挡块一起画进图片**，保存后关闭界面 |
| 保存到 Anki | 工具条绿色「保存到 Anki」／`Ctrl+S`／回车——保存后关闭界面 |
| 放弃本次截图 | `Esc`，或工具条 `✕` |

> **调色板**：图片挖空模板只支持 7 个颜色，语义分别是
> 铭记于心（红 `#ff3e05`）/ 印象深刻（橙 `#ff8d05`）/ 温故知新（黄 `#ecca05`）/
> 熟能生巧（绿 `#40af04`）/ 了解一二（蓝 `#057fff`）/ 晦涩难懂（紫 `#7500ca`）/
> 不屑一顾（灰 `#808080`）；透明度由模板固定。工具条不再提供任意取色器。

> **选区提示**：选区态默认高亮「⛶ 回到选区」（单击不会取消高亮）；鼠标移到
> 选区的边 / 角会按方向变成不同的双向缩放光标，直接拖动边框即可放大 / 缩小选区。
> 进入绘制态后高亮切给当前工具；挖空的选中框边缘 / 角同样按方向显示缩放光标。
>
> **文字提示**：输入框透明、无边框；`Shift+Enter` 手动换行，`Enter` 确定；
> **不会自动换行**。确定后文字的位置和行距与输入态一致（都用 QTextDocument 布局），
> 字号会记住，下次截图沿用。

> **缩放提示**：挖空坐标是相对原图的归一化值，400% 下画的挖空缩回 100% 后
> 位置和大小**完全不变**（`tests/test_editor.py` 有对应断言）。

### 设置与模板

托盘菜单里和配置相关的入口：

- **快捷键设置…**：只改全局热键，并显示当前注册状态 / 后端；「设置…」里不再放热键。
- **设置…**：没有确定/取消，改动即时保存；分「模板与保存」「高级」两页：
  - *模板与保存*：当前模板、**导入 .apkg 模板…**、从 Anki 选择模板、**挖空图片写入字段**、
    标签、保存到的牌组（「从Anki刷新」）、牌组目录、离线牌包归并方式、导出图片尺寸、
    AnkiConnect 地址；
  - *高级*：常用设置——启用全局热键、开机自启动。
- **挖空图片写入字段**：唯一需要用户选的模板字段；默认自动选 `pic_kong`。笔记类型名和
  其它字段映射都在导入 / 选择模板时自动确定，不再显示在界面上。
- **导入 .apkg 模板…**：自动提取笔记类型、字段、模板和 CSS；同时通过 AnkiConnect
  把整个 `.apkg` 导入 Anki（会带上包内的卡片 / 牌组）。Anki 没开时只保存本地模板，
  之后可以再从 Anki 选择。
- 只带 `collection.anki21b` 的新格式包读不了：先在 Anki 里导入一次，再点「从 Anki 选择模板…」。

### 两条保存路径的区别

| | 保存到 Anki | 复制 / 另存图片 |
|---|---|---|
| 挖空 | 只写进 `kong` 字段（坐标数据），**图片里看不到遮罩** | **画进图片**（不透明遮挡块） |
| 箭头 / 文字 | 烧录进图片 | 烧录进图片 |

这是刻意的：挖空要能被模板在复习时重新渲染出来，就不能先烧死在图片里；
而复制到微信、贴到文档里时没有模板，必须直接看到遮挡效果。

### 导出图片尺寸（Retina 屏必读）

Retina 屏上 `QScreen.grabWindow()` 交回的是**原生像素**：1280×800 的屏抓出来是
2560×1600。原样导出的话，一块看起来 400×300 的选区会变成 **800×600 的文件**——
这就是"保存的图怎么比截图大"的来由。设置里「导出图片尺寸」二选一：

| 选项 | 400×300 的选区导出为 | 特点 |
|---|---|---|
| 原生像素（**默认**） | 800×600 | 保留全部真实采样，最清晰；代价是像素数 4 倍 |
| 与屏幕一致 | 400×300 | 文件小，但等于把真实采样砍掉一半 |

**为什么默认是「原生像素」**：屏幕点口径看着"和截图一样大"，实际上是把 Retina
的真实采样丢掉一半；而这张图之后在 Anki、预览、微信里都是按 Retina **放大回来**
看的，所以中间那次降采样只会表现为"糊"。嫌文件太大再切回「与屏幕一致」。

切换口径只影响导出的一次重采样：**挖空存的是归一化坐标（比例值）**，
所以不管哪个口径，"遮挡盖住哪个字"完全不变。

### 离线导出：多张卡片攒进同一个牌包

Anki 没开的时候走离线路径，导出 `.apkg`。它**不是一张卡片一个文件**，而是累积的：

```
第 1 张 ┐
第 2 张 ├─►  .ankisnip-batch/（media/ + manifest.json）──► 同一个 .apkg（整包重写）
第 N 张 ┘
```

每次保存只往 `media/` 丢图、往 `manifest.json` 记字段，然后**整包重写**同一个目标文件，
磁盘上永远只有一个包。设置里可选三种归并方式（默认「每天一个」）：

| 归并方式 | 目标文件 | 适合 |
|---|---|---|
| 每天一个（默认） | `AnkiSnip-2026-10-28.apkg` | 按天批量导入，一天一包不至于太大 |
| 永远同一个 | `AnkiSnip.apkg` | 长期收集同一个主题的素材 |
| 一张一个 | `AnkiSnip-20261028-221530-123.apkg` | 旧行为，想单独分发某张卡时用 |

要点：

* **累积的是同一个文件**，所以重复导入不会产生重复卡片——每张卡的 `guid` 由媒体名
  哈希得到，是稳定的（genanki 默认按字段算 guid，字段里含随机文件名，必然重复）。
* 托盘右键菜单有「**归档离线牌包**」：把当前包改名留档、清空批次重新开始；
  归档成功会弹窗显示归档路径，并可直接「打开目录」。
* 「打开牌组目录」直接跳到导出目录；「设置牌组目录…」可以直接改位置（和设置里的
  「牌组目录（离线牌包）」是同一个值）。
* 「保存到的牌组」在设置里是**可编辑下拉**，旁边「刷新」按钮会从 AnkiConnect
  读取你现有的牌组列表来选。

---

## 数据模型

运行时（不持久化）：

```jsonc
{
  "image":  { "originalWidth": 1920, "originalHeight": 1080 },
  "view":   { "scale": 1.0, "tx": 0, "ty": 0 },
  "annotations": [
    { "type": "mask",  "nx": 0.32, "ny": 0.18, "nw": 0.10, "nh": 0.06 },
    { "type": "arrow", "x1": 0.10, "y1": 0.20, "x2": 0.30, "y2": 0.40,
      "color": "#ff0000", "width": 3 },
    { "type": "text",  "nx": 0.50, "ny": 0.60, "text": "重点",
      "fontSizePx": 24, "color": "#ff0000" }
  ]
}
```

实际实现中 `annotations` 的坐标以**原图像素**存储（与基座一致），
持久化前统一经 `MaskAnnotation.to_norm()` 转成 0~1，再由 `kong.serialize` 转成
现有模板要求的百分比字符串。`view` 不持久化。

持久化只有两样：

1. **烧录后的图片**（原图分辨率 = 截图选区的物理像素）
   —— 含箭头、文字；**不含挖空**。
2. **`pic_kong` 字段**：`<img src="ankisnip_xxx.png" kong="ID6+base32(AES-ECB(base64(json)));…">`

协议细节与对拍结论见 [`docs/02-kong协议说明.md`](docs/02-kong协议说明.md)。

---

## 目录结构

```
src/ankisnip/
  base/             vendored 基座 mysphinx-snip 0.0.1（MIT），仅注入 MASK 与两处钩子
  coords.py         坐标变换唯一出入口（image / widget / norm）
  editor_canvas.py  可缩放 / 平移画布 + 鼠标交互（作为覆盖层子控件挂进选区）
  editor_toolbar.py 工具条（选区态就已出现，含「回到选区 / 复制 / 保存到 Anki / ✕」）
  overlay_ext.py    选区 + 标注合一的覆盖层：Phase.SELECT / Phase.DRAW
  rasterize.py      分离挖空 + 按原图分辨率烧录
  kong/             现有插件 kong 序列化的 Python 移植（crypto / serialize）
  anki_connect.py   AnkiConnect 客户端（仅标准库）
  apkg_export.py    genanki 离线导出
  saver.py          保存链路编排 + 失败/取消清理
  settings.py       配置（继承基座 Config）
  settings_dialog.py
  hotkey.py         全局热键：统一记法 → 原生注册（Carbon/Win32）→ pynput 兜底
  hotkey_capture.py 热键录制控件（按下即生成，不用手写记法）
  logging_setup.py
  app.py            SnipApp 子类：托盘、热键、保存回调、首次运行引导
tests/              104 个用例（含基座自带的 15 个）
tools/              kong 对拍用的 JS 参考实现 + 热键自检脚本
packaging/          PyInstaller spec、构建脚本、图标、 entitlements
docs/               基座选型报告 / 变更说明 / kong 协议 / 阶段交付记录
```

---

## 开发

```bash
pip install -r requirements.txt -r requirements-dev.txt

QT_QPA_PLATFORM=offscreen pytest -q     # 122 passed
python tools/parity_check.py            # kong 与现有 Anki 插件对拍
python tools/hotkey_selftest.py         # 热键自检（会请你按一次组合键）
python tools/verify_bundle.py           # 校验 dist 里装的是不是最新源码
```

`kong` 对拍用例需要 `node`；找不到 node 时会自动跳过（不会误判为失败）。

---

## 打包

```bash
# macOS 一键：测试 → 打包 → 校验 → 退出旧进程 → 重置屏幕录制授权 → 安装 → 启动
bash packaging/build_install_mac.sh
#   --skip-tests         跳过测试，快速迭代
#   --keep-permissions   保留现有屏幕录制授权（默认会先 tccutil reset 清除）
#   --no-launch          只安装，不自动启动
#   PYTHON=/path/to/python3 ...   指定打包用的 Python

# 只打包 / 只安装（分步执行时用）
bash packaging/build_mac.sh            # → dist/AnkiSnip.app
python tools/verify_bundle.py           # 非零退出码 = dist 是旧的
cp -R dist/AnkiSnip.app /Applications/

# Windows
packaging\build_win.bat                # → dist\AnkiSnip\AnkiSnip.exe
```

签名与公证步骤写在 `packaging/build_mac.sh` 的注释里。

已实跑验证（macOS 15 / arm64 / Python 3.13 / PyInstaller 6.22.3）：

```text
dist/AnkiSnip.app   85 MB，onedir + BUNDLE，构建无告警
Contents/Frameworks: libpyside6.abi3.6.12.dylib、libpython3.13.dylib、Crypto/ …
Analysis-00.toc:    _raw_ecb ✓  pynput(49) ✓  genanki(23) ✓  ankisnip(56) ✓
```

> 首次双击会触发系统「屏幕录制」授权弹窗 —— 这是预期行为，勾选后**完全退出再重启**即可。
> 当前产物是 ad-hoc 签名，分发给他人需要走 `build_mac.sh` 注释里的签名 + 公证流程。

---

## 已知问题

1. **macOS 菜单栏的框选**。覆盖层现在已经把 `NSWindow.level` 抬到菜单栏之上
   （`NSScreenSaverWindowLevel`），正常可以把菜单栏一起框进来；截取的图像本身
   （`QScreen.grabWindow`）一直包含菜单栏。若某台机器上系统仍强制把菜单栏画在
   覆盖层之上，选区最上方一条会显示为暗色，但截图内容仍然是完整的。
2. **撤销栈只记录「新增」**。移动 / 删除标注不进撤销栈 —— 这是基座
   `AnnotationStack` 的 LIFO 语义所致，没有改动它以免破坏基座行为。
   需要完整撤销时，应把 Stack 升级成命令对象栈。
3. **Linux / Wayland 支持有限**（基座自述）。非目标平台。
4. **离线 `.apkg` 依赖「模板快照」**。挖空是靠笔记类型正面模板里内联的脚本渲染的，
   所以离线导出必须带上 Anki 里那个笔记类型的完整定义（模型 id / 字段 / 模板 / CSS）。
   Anki 开着时会自动抓一份快照；也可以在「设置…」→「模板与保存」里点
   「从 Anki 选择模板…」，或直接「导入 .apkg 模板…」；
   **从没连过 Anki 就导出的话**，牌包用的是最小模板，导入后挖空不会显示——程序会在
   保存结果里明确写这句话。
5. **旧格式 `kong` 卡片**（早期版本产出的、无 `;` 的单块密文）本项目只读不写，
   且那一版用的 key 与现在不同，无法解密 —— 不影响既有卡片复习，也不影响新卡片。
6. **macOS 上无法用合成事件自动验证热键**：系统不把 `CGEventPost` 的事件投递给
   无辅助功能权限的进程，`tools/hotkey_selftest.py` 因此提供人工模式——运行后
   按一次快捷键即可在窗口上看到结果。
7. **托盘菜单是系统原生菜单**（macOS）。早期为了"左键直接截图、右键菜单"用
   `QMenu.exec()` 自己弹菜单，AppKit 对状态项点击跟踪的收尾非常脆弱：Esc 关掉后
   图标残留聚焦、系统滞留在 `NSEventTrackingRunLoopMode`、Carbon 全局热键不
   投递，延后 / 激活 / 清高亮的补丁越修越多。

   现在直接 `QSystemTrayIcon.setContextMenu(menu)`（Qt 6.4+ 在 macOS 上映射为
   `NSStatusItem.menu`）：菜单由 AppKit 全权管理，焦点、图标高亮、Esc、菜单项
   派发和关闭后的激活态都走系统路径。行为是 macOS 的标准行为——左键 / 右键
   **都会**弹菜单，「截图」在第一项；快捷键 ⌃⌘S 仍然直接截图。
   「截图」菜单项同时带着这个快捷键作为 key equivalent：菜单开着时按快捷键由
   原生菜单触发（Carbon 全局热键在菜单跟踪期间会被系统挡住），菜单关着时由
   Carbon 触发，两边互补。
   `mac_focus.py` 只负责截图覆盖层 / 对话框收起后的前台交还，不再参与托盘菜单。
   要实测前台交还可跑 `open -a dist/AnkiSnip.app --args --focus-selftest`，
   结论写在 `~/Library/Application Support/ankisnip/logs/focus-selftest.txt`。
8. **`dist/` 很容易比源码旧**（血泪教训）。曾经用户连着报了 4 个 bug，源码里早就
   修好了 —— 他一直在跑一个半小时前的陈旧 bundle。约定：**改完源码必须重打包**，
   然后跑 `python tools/verify_bundle.py` 做字符串级校验（非零退出码 = 包是旧的）。

### Retina / 高 DPI 的坐标陷阱

Qt 的 `QPainter` 在 `devicePixelRatio > 1` 的绘图设备上使用**逻辑坐标**：每一次
draw 都会被再乘一次 dpr。而截图本体就带 dpr（Retina = 2），标注坐标却是原图物理
像素 —— 两者相乘的结果是所有笔画被放大一倍并溢出到画布外，表现为"箭头/矩形完全
没烧进图片"。

处理办法：在**取图出口**（`overlay_ext.selection_image()`）和**烧录入口**
（`rasterize.normalize_image()`）各归一一次 dpr，中间所有环节统一按"1 个 QImage
像素 = 1 个原图像素"思考；dpr 语义只在 `compose()` 返回 `QPixmap` 那一层恢复。
挖空走 `to_norm()` 不经过 painter，所以当时只有箭头/文字受影响 —— 这个差异一度
误导排查方向。

> 早前的测试全部用 `dpr = 1` 的 Pixmap 构造覆盖层，三套坐标恰好重合，把这个缺陷
> 完全掩盖住了。`tests/test_export_geometry.py` 补的就是这块。

---

## 验收清单对照

| # | 验收项 | 状态 | 依据 |
|---|---|---|---|
| 1 | 全局热键在前台任意应用时触发 | ✅ 原生注册为主、pynput 兜底 | `hotkey.py` + `base/app.py`；`tools/hotkey_selftest.py` 人工验证 |
| 2 | 箭头 / 文字 / 拖拽挖空 | ✅ | `overlay_ext` + `editor_canvas`；`tests/test_editor.py`、`tests/test_overlay.py` |
| 3 | 400% 挖空 → 100% 不变 | ✅ | `test_mask_geometry_survives_zoom_change`、`test_mask_geometry_is_stable_across_zoom` |
| 4 | 撤销 / 重做对三类标注有效 | ✅ | `test_undo_redo_for_mask_arrow_and_text` |
| 5 | 图片里有箭头文字、无挖空遮罩 | ✅ **仅「保存到 Anki」** | `test_bake_keeps_original_resolution_and_excludes_mask`；复制/另存则相反，见 `test_compose_includes_masks_for_copy_and_save` |
| 6 | 导出分辨率 = 原图分辨率 | ✅ | 同上（`out.size() == image.size()`） |
| 7 | Anki 运行时写入指定牌组 | ✅ | `test_save_via_ankiconnect`（`addNote` + `storeMediaFile`） |
| 8 | Anki 未运行生成 `.apkg` | ✅ 且带上真实笔记类型 | `test_offline_apkg_fallback`、`test_apkg_uses_real_model_definition`（校验 mid / 模板 / CSS） |
| 9 | `kong` 与现有插件对拍一致 | ✅ | `test_kong_matches_existing_plugin`（逐字节） |
| 10 | 复习时渲染位置与编辑器一致 | ✅ 同协议 | 坐标存的是百分比字符串，与插件渲染口径一致 |
| 11 | 多显示器 / 高 DPI | ✅ Retina 专项回归 | `base/capture.py`、`overlay_ext.selection_image`；`tests/test_export_geometry.py` 用 dpr=2 场景锁定像素位置（见下方第 8 条） |
| 12 | 取消保存不留孤儿媒体 | ✅ | `test_add_note_failure_cleans_media`、`saver.cleanup/discard` |
| 13 | 错误场景有明确提示 | ✅ | `test_missing_model_*`、`test_missing_field_*`、连接失败提示 |

> 第 1、11 项依赖真实硬件环境，本机（macOS）为代码路径复用基座实现 + 单测覆盖，
> 建议在多显示器 / Windows 125%~150% 缩放下做一次人工回归。

---

## 许可证

* 本项目代码：MIT
* `src/ankisnip/base/`：vendored 的 `mysphinx-snip` 0.0.1（MIT），
  来源与改动见 [`src/ankisnip/base/VENDORED.md`](src/ankisnip/base/VENDORED.md)
* `tools/refjs/`：来自现有 Anki 插件仓库的 `crypto-js` 与 `base32` 副本，
  仅用于对拍测试，不参与打包



PYTHON=/Users/faiyer/miniforge3/bin/python3 bash packaging/build_install_mac.sh

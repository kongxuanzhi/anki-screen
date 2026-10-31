# `kong` 字段协议说明（逆向 + 对拍结论）

> 本文记录了从**现有 Anki 插件代码**中定位、确认 `kong` 序列化 / 加密逻辑的全过程与结论。
> 依据任务书 §4「禁止自行修改现有 Anki 模板的 `kong` 字段格式」，
> 本项目只做**移植**，不做任何格式调整。

---

## 1. 定位过程

| 步骤 | 命令 / 方法 | 结果 |
|---|---|---|
| 找加密实现 | 在 `ankiaddon/www/src/js` 搜 `AES|ECB|b32encode` | 命中 `utils/utils.js` |
| 找挖空串拼装 | 搜 `kong=` / `rect2str` | 命中 `lib/pic1.js`（图片挖空模板） |
| 确认 base32 实现 | `webpack.config.js` → `externals: {'base32_min': 'base32'}` | 运行时用的是**全局 `base32`**，即模板内嵌的 `utils/base32.min.js` |
| 确认字母表 | `grep 0123456789abcdefghjkmnpqrtuvwxyz dist/templates/pic1/pic1.lib.min.js` | ✅ 命中；RFC4648 字母表（`A-Z2-7`）**未**出现 |
| 确认 key | `grep 2024169163483647 pic1.bundle.js` | ✅ 命中 |
| 找字段定义 | `templates.json` → `pic1` | `type_name: 图片挖空模板sle1_0`，`fields: [pic_kong, question, relation]`，`spec_filed: pic_kong` |

## 2. 协议定义

### 2.1 加密

```js
// ankiaddon/www/src/js/utils/utils.js
encrypt(message) {
    var aseKey = '2024169163483647';
    var encrypt = CryptoJs.AES.encrypt(message, CryptoJs.enc.Utf8.parse(aseKey), {
        mode: CryptoJs.mode.ECB,
        padding: CryptoJs.pad.Pkcs7
    }).toString();          // → 密文的标准 base64（无 Salted__ 头，因为 key 是 WordArray）
    return base32.encode(encrypt);
}
```

等价 Python：

```python
AES-128-ECB(key=b"2024169163483647", PKCS7) → base64 → base32(自定义字母表, 无填充)
```

### 2.2 base32

* 字母表：`0123456789abcdefghjkmnpqrtuvwxyz`（32 字符，5 bit/字符，MSB-first，**无 `=` 填充**）
* 输入是 base64 **字符串**（ASCII），逐字节 `charCodeAt` 参与编码
* 输出恒为小写 —— 这正好是 `decrypt()` 里 `if (encrypt.toLowerCase() == encrypt)` 判断
  "这是新格式（base32）还是旧格式（裸 base64）"的依据

### 2.3 挖空串（新格式，本项目采用）

```js
// ankiaddon/www/src/js/lib/pic1.js
rect2str(rect) {
    let rectarr = [x, y, color, w, h, rx, ry, opacity, tip];   // 9 项，不含 id
    return id + encrypt(JSON.stringify(rectarr));              // id 是明文 6 位前缀
}
saveContext(rects, 'add') {
    for (let rect of rects) kong += this.rect2str(rect) + ';'; // 每块后面都带 ';'
}
```

完整形态：

```text
kong = (ID6 + base32(AES-ECB(base64(JSON)))) ; (…) ;
        ↑6位小写随机id                               ↑末尾也带一个 ';'
```

* `ID6` 字符集（`utils.randomString`）：
  `ABCDEFGHIJKLMNOPQRSTWXYZabcdefhijkmnprstwxyz2345678` 取 6 位后 **toLowerCase**
* `JSON` 数组 9 项：`[x, y, color, w, h, rx, ry, opacity, tip]`
  - `x / y / w / h` 是**百分比字符串**，形如 `"32.0%"`（`toPercent = (v*100).toFixed(1) + '%'`）
  - 模板渲染时 SVG `<rect>` 用百分比定位，**天然与图片分辨率无关**
  - `rx / ry` 恒为 `"0"`；`opacity` 恒为 `"1.0"`；`tip` 缺省 `""`
* 字段 HTML：`<img src="文件名" kong="上面的串">`

### 2.4 旧格式（本项目不产出，但需能识别）

早期版本 `kong` 是**单个**大块：`encrypt(JSON.stringify([[x,y,color,w,h,rx,ry,opacity,id,tip], ...]))`
（10 项、含 id、无 `;`、无 6 位前缀）。判断依据是 `kongs_str.indexOf(';') === -1`。
`anki图片挖空模板newsle.apkg` 里的历史卡片就是这种（且是更早的一版 key，现已无法解密），
本项目只写新格式、只读新格式；旧卡片不受影响。

---

## 3. 归一化坐标 ↔ 百分比字符串

任务书 §3 要求持久化坐标是"相对原图的归一化坐标"，§4 要求复用现有格式。
二者在本项目里的调和方式：

```
运行时（编辑器）:  image 像素坐标  ──MaskAnnotation.to_norm()──►  归一化 0~1
持久化（kong）  :  归一化 0~1     ──to_percent()─────────────►  "32.0%" 百分比字符串
```

百分比同样是"相对原图"的相对量，**不含任何屏幕像素**，满足 §1 的禁止事项；
同时又能被现有模板直接渲染，满足 §4 的禁止修改格式。

## 4. 对拍测试

* 参考实现：`tools/kong_ref.js`（逐行移植 `utils.js:encrypt` + `pic1.js:rect2str/saveContext`，
  直接 `require` 仓库里 copy 过来的 `tools/refjs/crypto-js.min420.js` 与 `base32.min.js`——
  就是线上模板加载的那两份文件）
* Python 实现：`src/ankisnip/kong/`
* 测试：`tests/test_kong_parity.py::test_kong_matches_existing_plugin`

实测结果（2 组用例，含中文 tip / 边界值 100%）：

```text
JS : ab3kx9ccvp2rtmdmv5ey9h69amjrhbe9q6egb7cwr7jhagad6qgyjqdxrmghaj8cnprubm6dnkexb1e58m2t1hd…;qw7z2man8mphu9dmup2jv8e9q7ehkhdrvk6e35d5834jbp6962pk9mf166acbbedkkgp24arw2yxtkax93cj9m9hk6wphn88u46ube6t8k0yk1an1p8j9ne9pnmn228rvn0uud6ha58wac8xpm2h1f90w6mxkh61h6um2betbq6va669kku;
PY : ab3kx9ccvp2rtmdmv5ey9h69amjrhbe9q6egb7cwr7jhagad6qgyjqdxrmghaj8cnprubm6dnkexb1e58m2t1hd…;qw7z2man8mphu9dmup2jv8e9q7ehkhdrvk6e35d5834jbp6962pk9mf166acbbedkkgp24arw2yxtkax93cj9m9hk6wphn88u46ube6t8k0yk1an1p8j9ne9pnmn228rvn0uud6ha58wac8xpm2h1f90w6mxkh61h6um2betbq6va669kku;
MATCH: True          # 逐字节一致
```

另有往返测试（`build_kong` → `parse_kong`）与 base32 8 种长度边界的编解码往返。

#!/usr/bin/env node
/**
 * kong_ref.js —— 现有 Anki 插件「图片挖空」序列化逻辑的**参考实现**（原样移植）。
 *
 * 来源（逐行对齐，未做任何改动）：
 *   - 加密：ankiaddon/www/src/js/utils/utils.js  -> encrypt()
 *   - 挖空串：ankiaddon/www/src/js/lib/pic1.js  -> rect2str() / saveContext(op='add')
 *
 * 用途：与 Python 侧 kong/ 模块做**对拍**（parity test）。
 *
 * 用法：
 *   node tools/kong_ref.js '<JSON>'
 * 其中 JSON 形如：
 *   { "masks": [ {"id":"ab3kx9", "x":"32.0%","y":"18.0%","color":"#057fff",
 *                 "w":"10.0%","h":"6.0%","rx":"0","ry":"0",
 *                 "opacity":"1.0","tip":""} ] }
 * 输出：{"kong": "..."}   （新格式：每块 = 6位id + base32(AES-ECB(base64(json)))，块间 ';' 分隔，末尾带 ';'）
 */
'use strict';

const path = require('path');
const CryptoJs = require(path.join(__dirname, 'refjs', 'crypto-js.min420.js'));
const base32 = require(path.join(__dirname, 'refjs', 'base32.min.js'));

/** 移植自 www/src/js/utils/utils.js */
function encrypt(message) {
    var aseKey = '2024169163483647';
    var encrypt = CryptoJs.AES.encrypt(message, CryptoJs.enc.Utf8.parse(aseKey), {
        mode: CryptoJs.mode.ECB,
        padding: CryptoJs.pad.Pkcs7
    }).toString();
    return base32.encode(encrypt);
}

/** 移植自 www/src/js/utils/utils.js */
function decrypt(encrypt) {
    var aseKey = '169163483647';
    if (encrypt.toLowerCase() == encrypt) {
        encrypt = base32.decode(encrypt);
        aseKey = '2024169163483647';
    }
    return CryptoJs.AES.decrypt(encrypt, CryptoJs.enc.Utf8.parse(aseKey), {
        mode: CryptoJs.mode.ECB,
        padding: CryptoJs.pad.Pkcs7
    }).toString(CryptoJs.enc.Utf8);
}

/**
 * 移植自 www/src/js/lib/pic1.js -> rect2str(rect)
 * 注意：新格式（saveContext 'add' 分支）数组为 9 项，不含 id（id 以明文 6 位前缀放在密文前）
 */
function maskToStr(m) {
    var rectarr = [m.x, m.y, m.color, m.w, m.h, m.rx, m.ry, m.opacity, m.tip || ''];
    return m.id + encrypt(JSON.stringify(rectarr));
}

/** 移植自 www/src/js/lib/pic1.js -> saveContext(rects, 'add') */
function buildKong(masks) {
    var kong = '';
    for (const m of masks) {
        kong += maskToStr(m) + ';';
    }
    return kong;
}

/** 移植自 www/src/js/lib/pic1.js -> init_convert() 的新格式解析分支（反向校验用） */
function parseKong(kong) {
    const out = [];
    for (const part of kong.split(';')) {
        if (part.length < 10) continue;
        const id = part.slice(0, 6);
        const attr = part.slice(6);
        out.push({ id: id, rect: JSON.parse(decrypt(attr)) });
    }
    return out;
}

function main() {
    const arg = process.argv[2];
    if (!arg) {
        console.error('usage: node kong_ref.js <json>');
        process.exit(2);
    }
    const payload = JSON.parse(arg);
    const kong = buildKong(payload.masks);
    if (process.env.KONG_REF_ECHO_PARSE === '1') {
        console.log(JSON.stringify({ kong: kong, parsed: parseKong(kong) }));
    } else {
        console.log(JSON.stringify({ kong: kong }));
    }
}

if (require.main === module) {
    main();
}

module.exports = { encrypt, decrypt, maskToStr, buildKong, parseKong };

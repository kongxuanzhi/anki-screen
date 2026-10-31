"""坐标变换测试：正/逆变换必须自洽，缩放锚点必须不动，缩放后归一化坐标不变。"""

from __future__ import annotations

import pytest
from PySide6.QtCore import QPointF

from ankisnip.coords import ViewTransform


def test_roundtrip_image_widget():
    tf = ViewTransform(1920, 1080, fit_scale=0.4, scale=2.5, tx=-130.0, ty=47.0)
    for x, y in [(0, 0), (1920, 1080), (13.5, 777.25), (960, 540)]:
        p = tf.image_to_widget(x, y)
        q = tf.widget_to_image(p.x(), p.y())
        assert q.x() == pytest.approx(x, abs=1e-6)
        assert q.y() == pytest.approx(y, abs=1e-6)


def test_norm_and_image_agree():
    """norm 与 image 两条路径必须给出同一个屏幕点（不允许出现两套真理）。"""
    tf = ViewTransform(1920, 1080, fit_scale=0.4, scale=2.5, tx=-130.0, ty=47.0)
    a = tf.norm_to_widget(0.32, 0.18)
    b = tf.image_to_widget(0.32 * 1920, 0.18 * 1080)
    assert a.x() == pytest.approx(b.x(), abs=1e-6)
    assert a.y() == pytest.approx(b.y(), abs=1e-6)

    nx, ny = tf.widget_to_norm(a.x(), a.y())
    assert nx == pytest.approx(0.32, abs=1e-6)
    assert ny == pytest.approx(0.18, abs=1e-6)


def test_zoom_anchor_is_stable():
    """滚轮缩放时鼠标下的那个图像像素必须保持不动。"""
    tf = ViewTransform(1000, 800, fit_scale=0.5, scale=1.0, tx=10.0, ty=20.0)
    anchor = QPointF(321.0, 222.0)
    before = tf.widget_to_image(anchor.x(), anchor.y())
    for _ in range(6):
        tf.zoom_at(anchor.x(), anchor.y(), 1.25)
        after = tf.widget_to_image(anchor.x(), anchor.y())
        assert after.x() == pytest.approx(before.x(), abs=1e-6)
        assert after.y() == pytest.approx(before.y(), abs=1e-6)
    assert tf.scale == pytest.approx(1.25 ** 6, rel=1e-9)


def test_zoom_400_then_back_to_100_keeps_geometry():
    """验收清单第 3 条：400% 下画的挖空，缩回 100% 后位置和大小不变。"""
    tf = ViewTransform(800, 600, fit_scale=0.5)
    tf.fit(1200, 900)

    # 在 400% 下用鼠标拖出一个矩形
    tf.set_zoom(4.0, 1200, 900)
    p0 = tf.widget_to_image(500.0, 400.0)
    p1 = tf.widget_to_image(560.0, 440.0)
    # 归一化持久化
    nx, ny = p0.x() / tf.img_w, p0.y() / tf.img_h
    nw, nh = (p1.x() - p0.x()) / tf.img_w, (p1.y() - p0.y()) / tf.img_h

    # 缩回 100%
    tf.set_zoom(1.0, 1200, 900)
    q0 = tf.norm_to_widget(nx, ny)
    q1 = tf.norm_to_widget(nx + nw, ny + nh)
    # 关键断言：由归一化坐标还原出的"图像像素矩形"必须仍是原来那个
    r0 = tf.widget_to_image(q0.x(), q0.y())
    r1 = tf.widget_to_image(q1.x(), q1.y())
    assert r0.x() == pytest.approx(p0.x(), abs=1e-6)
    assert r0.y() == pytest.approx(p0.y(), abs=1e-6)
    assert r1.x() == pytest.approx(p1.x(), abs=1e-6)
    assert r1.y() == pytest.approx(p1.y(), abs=1e-6)


def test_fit_centers_image():
    tf = ViewTransform(4000, 3000)
    tf.fit(800, 600)
    assert tf.display_w <= 800 - 16 + 1e-6
    assert tf.display_h <= 600 - 16 + 1e-6
    assert tf.tx == pytest.approx((800 - tf.display_w) / 2.0, abs=1e-6)


def test_scale_clamped():
    tf = ViewTransform(100, 100, fit_scale=1.0)
    for _ in range(200):
        tf.zoom_at(0, 0, 2.0)
    assert tf.scale <= 40.0 + 1e-9
    for _ in range(400):
        tf.zoom_at(0, 0, 0.5)
    assert tf.scale >= 0.05 - 1e-9

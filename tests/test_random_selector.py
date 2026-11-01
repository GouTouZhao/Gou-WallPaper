"""不放回随机选择与批次边界测试 (`tests/test_random_selector.py`)."""

from __future__ import annotations

from app.random_selector import ImageSelector


def test_random_selector_without_replacement_cycle():
    candidates = [f"image_{i}.png" for i in range(10)]
    selector = ImageSelector(candidates=candidates, mode="random", seed=42)

    # 连续抽取 10 张，构成一个完整周期
    first_batch = [selector.next() for _ in range(10)]
    assert len(first_batch) == 10
    # 一个周期内不得有重复图片
    assert len(set(first_batch)) == 10
    assert set(first_batch) == set(candidates)


def test_batch_boundary_non_repeat_guarantee():
    """验证核心规则 4.3：新批次开头不得与上一批次末尾重复（有效图片 >= 2 张时）."""
    candidates = [f"img_{i}.png" for i in range(4)]
    selector = ImageSelector(candidates=candidates, mode="random", seed=123)

    # 连续跨越 50 个周期进行严格边界比对
    last_item = None
    for cycle in range(50):
        batch = [selector.next() for _ in range(len(candidates))]
        assert len(set(batch)) == len(candidates), f"周期 {cycle} 内部出现重复"
        if last_item is not None:
            # 跨周期边界检查：新周期的首张不得与上一周期的最后一张相同
            assert batch[0] != last_item, f"周期 {cycle} 首张与上一周期末张重复: {batch[0]}"
        last_item = batch[-1]


def test_sequential_mode():
    candidates = ["a.png", "b.png", "c.png"]
    selector = ImageSelector(candidates=candidates, mode="sequential")

    results = [selector.next() for _ in range(7)]
    assert results == ["a.png", "b.png", "c.png", "a.png", "b.png", "c.png", "a.png"]


def test_previous_and_next_navigation():
    candidates = ["img1.png", "img2.png", "img3.png", "img4.png"]
    selector = ImageSelector(candidates=candidates, mode="sequential")

    assert selector.next() == "img1.png"
    assert selector.next() == "img2.png"
    assert selector.next() == "img3.png"

    # 回退到上一张
    assert selector.previous() == "img2.png"
    assert selector.previous() == "img1.png"

    # 沿历史前进
    assert selector.next() == "img2.png"
    assert selector.next() == "img3.png"


def test_remove_candidate_self_healing():
    candidates = ["a.png", "b.png", "c.png"]
    selector = ImageSelector(candidates=candidates, mode="sequential")
    assert selector.next() == "a.png"

    # 运行期模拟 b.png 被删除
    selector.remove_candidate("b.png")
    assert selector.candidate_count == 2
    # 下一张应平滑跳到 c.png
    assert selector.next() == "c.png"


def test_serialization_and_restore():
    candidates = [f"p_{i}.jpg" for i in range(8)]
    selector = ImageSelector(candidates=candidates, mode="random", seed=99)
    for _ in range(5):
        selector.next()
    state = selector.to_dict()

    new_sel = ImageSelector(candidates=candidates)
    new_sel.restore_from_dict(state)
    assert new_sel.mode == selector.mode
    assert new_sel.current() == selector.current()


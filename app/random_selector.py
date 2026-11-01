"""不放回随机洗牌与顺序图片选择器模块 (`app/random_selector.py`).

核心保证（需求 4.3）：
1. 默认采用不放回的随机播放策略（将有效图片建立为候选集合 -> 随机打乱 -> 逐张取出 -> 耗尽后重新洗牌）。
2. 新批次开头不得与上一批次末尾重复，除非有效图片不足两张。
3. 同时支持顺序播放模式 (`sequential`)。
4. 支持：下一张、上一张、随机切换、暂停/恢复、当前图片查询，以及候选集变化/文件删除/程序重启后的状态自愈恢复。
"""

from __future__ import annotations

import random
import threading
from typing import Any


class ImageSelector:
    """线程安全的不放回随机与顺序选择器."""

    def __init__(
        self,
        candidates: list[str] | None = None,
        mode: str = "random",
        seed: int | None = None,
        max_history: int = 200,
    ) -> None:
        self._lock = threading.RLock()
        self._rng = random.Random(seed)
        self._mode: str = mode if mode in ("random", "sequential") else "random"
        self._max_history: int = max(10, int(max_history))

        self._candidates: list[str] = []
        self._batch_queue: list[str] = []
        self._seq_index: int = 0
        self._last_emitted: str | None = None

        # 浏览历史栈与游标，用于支持“上一张”与从历史向“下一张”前进
        self._history: list[str] = []
        self._history_cursor: int = -1
        self._paused: bool = False

        if candidates:
            self.set_candidates(candidates)

    @property
    def mode(self) -> str:
        with self._lock:
            return self._mode

    def set_mode(self, mode: str) -> None:
        with self._lock:
            normalized = mode if mode in ("random", "sequential") else "random"
            if normalized != self._mode:
                self._mode = normalized
                if normalized == "random" and not self._batch_queue:
                    self._refill_random_batch()
                elif normalized == "sequential" and self._last_emitted in self._candidates:
                    self._seq_index = (self._candidates.index(self._last_emitted) + 1) % max(1, len(self._candidates))

    @property
    def paused(self) -> bool:
        with self._lock:
            return self._paused

    def pause(self) -> None:
        with self._lock:
            self._paused = True

    def resume(self) -> None:
        with self._lock:
            self._paused = False

    @property
    def candidate_count(self) -> int:
        with self._lock:
            return len(self._candidates)

    @property
    def remaining_in_batch(self) -> int:
        with self._lock:
            return len(self._batch_queue)

    def current(self) -> str | None:
        """返回当前选中的图片路径."""
        with self._lock:
            if 0 <= self._history_cursor < len(self._history):
                cur = self._history[self._history_cursor]
                if cur in self._candidates:
                    return cur
            if self._last_emitted and self._last_emitted in self._candidates:
                return self._last_emitted
            return None

    def set_candidates(self, candidates: list[str]) -> None:
        """更新有效图片候选集，并保持已有批次/历史状态平滑恢复."""
        with self._lock:
            seen: set[str] = set()
            clean: list[str] = []
            for p in candidates:
                if p and p not in seen:
                    seen.add(p)
                    clean.append(p)

            self._candidates = clean
            valid_set = seen

            # 过滤剩余随机队列中已被删除的图片
            self._batch_queue = [p for p in self._batch_queue if p in valid_set]

            # 过滤历史记录中已被删除的图片
            old_current = (
                self._history[self._history_cursor]
                if 0 <= self._history_cursor < len(self._history)
                else self._last_emitted
            )
            self._history = [p for p in self._history if p in valid_set]
            if not self._history:
                self._history_cursor = -1
            elif old_current in self._history:
                # 定位到最近一次出现的位置
                for idx in range(len(self._history) - 1, -1, -1):
                    if self._history[idx] == old_current:
                        self._history_cursor = idx
                        break
            else:
                self._history_cursor = len(self._history) - 1

            if self._last_emitted not in valid_set:
                self._last_emitted = self._history[self._history_cursor] if self._history_cursor >= 0 else None

            if not self._candidates:
                self._seq_index = 0
                self._batch_queue.clear()
                return

            self._seq_index = self._seq_index % len(self._candidates)

    def remove_candidate(self, bad_path: str) -> None:
        """当某张图片加载失败或被删除时，立即将其移出候选集并修正状态."""
        with self._lock:
            if bad_path in self._candidates:
                self.set_candidates([p for p in self._candidates if p != bad_path])

    def next(self) -> str | None:
        """获取下一张图片（若当前处于历史回退位置，优先沿历史栈前进）."""
        with self._lock:
            if not self._candidates:
                return None

            # 如果之前执行过 previous()，先沿着历史记录向后前进
            if 0 <= self._history_cursor < len(self._history) - 1:
                self._history_cursor += 1
                chosen = self._history[self._history_cursor]
                self._last_emitted = chosen
                return chosen

            chosen = self._draw_from_active_mode()
            if chosen is not None:
                self._push_history(chosen)
            return chosen

    def previous(self) -> str | None:
        """回到上一张图片."""
        with self._lock:
            if not self._candidates:
                return None

            if self._history_cursor > 0:
                self._history_cursor -= 1
                chosen = self._history[self._history_cursor]
                self._last_emitted = chosen
                return chosen

            if self._mode == "sequential":
                if self._last_emitted in self._candidates:
                    cur_idx = self._candidates.index(self._last_emitted)
                    prev_idx = (cur_idx - 1) % len(self._candidates)
                else:
                    prev_idx = (self._seq_index - 1) % len(self._candidates)
                chosen = self._candidates[prev_idx]
                self._seq_index = (prev_idx + 1) % len(self._candidates)
                self._push_history(chosen)
                return chosen

            # 随机模式下若已在历史最顶端，返回当前首张或抽取一张
            if self._history_cursor == 0 and self._history:
                return self._history[0]
            return self.next()

    def random_switch(self) -> str | None:
        """无论当前是顺序模式还是随机模式，立即执行一次不放回随机切换."""
        with self._lock:
            if not self._candidates:
                return None
            chosen = self._draw_random_without_replacement()
            if chosen is not None:
                self._push_history(chosen)
            return chosen

    def select_multiple(self, count: int) -> list[str]:
        """为多显示器或拼贴模式一次抽取 `count` 张图片."""
        with self._lock:
            if not self._candidates or count <= 0:
                return []
            results: list[str] = []
            for _ in range(count):
                item = self._draw_from_active_mode()
                if item is not None:
                    results.append(item)
            if results:
                self._push_history(results[-1])
            return results

    def _draw_from_active_mode(self) -> str | None:
        if not self._candidates:
            return None
        if self._mode == "sequential":
            if self._seq_index >= len(self._candidates):
                self._seq_index = 0
            chosen = self._candidates[self._seq_index]
            self._seq_index = (self._seq_index + 1) % len(self._candidates)
            self._last_emitted = chosen
            return chosen
        return self._draw_random_without_replacement()

    def _draw_random_without_replacement(self) -> str | None:
        if not self._candidates:
            return None
        if not self._batch_queue:
            self._refill_random_batch()
        if not self._batch_queue:
            return None
        chosen = self._batch_queue.pop(0)
        self._last_emitted = chosen
        return chosen

    def _refill_random_batch(self) -> None:
        """重新洗牌生成新批次，并确保新批次首张图片不等于上一批次最后一张图片."""
        n = len(self._candidates)
        if n == 0:
            self._batch_queue = []
            return
        if n == 1:
            self._batch_queue = [self._candidates[0]]
            return

        new_batch = list(self._candidates)
        self._rng.shuffle(new_batch)

        # 关键约束：新批次开头不得与上一批次末尾重复
        if self._last_emitted is not None and new_batch[0] == self._last_emitted:
            swap_idx = self._rng.randint(1, n - 1)
            new_batch[0], new_batch[swap_idx] = new_batch[swap_idx], new_batch[0]

        self._batch_queue = new_batch

    def _push_history(self, path: str) -> None:
        # 若游标在历史中间，截断游标之后的分叉记录
        if 0 <= self._history_cursor < len(self._history) - 1:
            self._history = self._history[: self._history_cursor + 1]

        if not self._history or self._history[-1] != path:
            self._history.append(path)
            if len(self._history) > self._max_history:
                overflow = len(self._history) - self._max_history
                self._history = self._history[overflow:]
        self._history_cursor = len(self._history) - 1
        self._last_emitted = path

    def to_dict(self) -> dict[str, Any]:
        """序列化选择器状态以便持久化到配置文件."""
        with self._lock:
            return {
                "mode": self._mode,
                "paused": self._paused,
                "batch_queue": list(self._batch_queue),
                "seq_index": int(self._seq_index),
                "last_emitted": self._last_emitted or "",
                "history": list(self._history[-50:]),
                "history_cursor": min(self._history_cursor, 49) if self._history else -1,
            }

    def restore_from_dict(self, state: dict[str, Any] | None) -> None:
        """从持久化字典恢复选择器状态，并结合当前有效候选集自动校准."""
        if not isinstance(state, dict):
            return
        with self._lock:
            mode = str(state.get("mode", self._mode))
            if mode in ("random", "sequential"):
                self._mode = mode
            self._paused = bool(state.get("paused", False))

            valid_set = set(self._candidates)
            raw_queue = state.get("batch_queue", [])
            if isinstance(raw_queue, list):
                self._batch_queue = [str(p) for p in raw_queue if isinstance(p, str) and p in valid_set]

            self._seq_index = int(state.get("seq_index", 0))
            if self._candidates:
                self._seq_index = max(0, self._seq_index) % len(self._candidates)
            else:
                self._seq_index = 0

            last_emitted = str(state.get("last_emitted", ""))
            self._last_emitted = last_emitted if last_emitted in valid_set else None

            raw_history = state.get("history", [])
            if isinstance(raw_history, list):
                self._history = [str(p) for p in raw_history if isinstance(p, str) and p in valid_set]
            if self._history:
                raw_cursor = int(state.get("history_cursor", len(self._history) - 1))
                self._history_cursor = max(0, min(len(self._history) - 1, raw_cursor))
                if self._last_emitted is None:
                    self._last_emitted = self._history[self._history_cursor]
            else:
                self._history_cursor = -1


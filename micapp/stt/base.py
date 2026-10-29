"""STT 引擎抽象与注册表。新引擎：继承 STTEngine 并用 register 注册。"""
import logging


class STTEngine:
    name = "base"

    def __init__(self, config: dict, on_result=None):
        """on_result(text, utterance_index) 在引擎线程被调用。

        on_drop(utterance_index)：这句话没有结果（转写失败 / 队列满 / 空文本）。
        调用方靠「每句话要么有结果、要么被丢弃」对齐自己的记账；漏报会让
        下一句挂上上一句的时长，越错越远。
        """
        self.config = config
        self.on_result = on_result
        self.on_drop = None

    def start(self):
        pass

    def stop(self):
        pass

    def feed_utterance(self, pcm: bytes):
        raise NotImplementedError

    def _drop(self, idx: int, why: str):
        """报告一句话被丢弃；子类在失败/队列满/空结果时调用。"""
        logging.info("%s: utterance %s dropped (%s)", self.name, idx, why)
        if self.on_drop:
            try:
                self.on_drop(idx)
            except Exception:
                logging.exception("on_drop 回调失败")


_REGISTRY: dict = {}


def register(cls):
    _REGISTRY[cls.name] = cls
    return cls


def create(name: str, config: dict, on_result) -> STTEngine:
    if name not in _REGISTRY:
        raise KeyError(f"未知 STT 引擎: {name}（可用: {list(_REGISTRY)}）")
    return _REGISTRY[name](config, on_result)

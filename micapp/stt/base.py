"""STT 引擎抽象与注册表。新引擎：继承 STTEngine 并用 register 注册。"""


class STTEngine:
    name = "base"

    def __init__(self, config: dict, on_result=None):
        """on_result(text: str, utterance_index: int) 在引擎线程被调用。"""
        self.config = config
        self.on_result = on_result

    def start(self):
        pass

    def stop(self):
        pass

    def feed_utterance(self, pcm: bytes):
        raise NotImplementedError


_REGISTRY: dict = {}


def register(cls):
    _REGISTRY[cls.name] = cls
    return cls


def create(name: str, config: dict, on_result) -> STTEngine:
    if name not in _REGISTRY:
        raise KeyError(f"未知 STT 引擎: {name}（可用: {list(_REGISTRY)}）")
    return _REGISTRY[name](config, on_result)

"""首启向导的纯逻辑测试：needs_setup 分支、引擎探测、局域网探测函数。

跑法：python test_setup.py（转写服务在 8390 时全过退出码 0）。
界面流程（扫描/试说一句）是实机验收，不在本文件。
"""
import sys

sys.path.insert(0, r"F:\projects\mic-desktop")

from micapp.setup_wizard import needs_setup, looks_configured, local_ip, probe_whisper

checks = []


def check(name, cond, detail=""):
    checks.append((name, bool(cond), detail))


BASE = {"stt_engine": "xfyun", "xfyun": {"app_id": "", "api_key": "", "api_secret": ""}}

# needs_setup：新机器（空模板）要弹
check("空模板 → 要向导", needs_setup(dict(BASE)) is True)
# 已完成过向导
check("setup_done=true → 不弹", needs_setup({**BASE, "setup_done": True}) is False)
# 讯飞凭据齐全（老机器迁移）
full = {**BASE, "xfyun": {"app_id": "a", "api_key": "k", "api_secret": "s"}}
check("讯飞凭据齐 → 不弹", needs_setup(full) is False)
check("凭据缺一个 → 弹", needs_setup({**BASE, "xfyun": {"app_id": "a", "api_key": "k", "api_secret": ""}}) is True)
# whisper_http 有地址（老机器迁移）
check("whisper 有 url → 不弹",
      needs_setup({**BASE, "stt_engine": "whisper_http", "whisper_http": {"url": "http://x:1/stt"}}) is False)
check("whisper 空 url → 弹",
      needs_setup({**BASE, "stt_engine": "whisper_http", "whisper_http": {"url": ""}}) is True)
# looks_configured 与 needs_setup 互补
check("looks_configured = not needs_setup",
      looks_configured(dict(BASE)) is False and looks_configured(full) is True)

# local_ip
ip = local_ip()
check("local_ip 拿得到", bool(ip) and ip != "")

# 引擎探测：真服务（8390 在跑）与必挂端口
ok, why = probe_whisper("http://127.0.0.1:8390/stt")
check("探测真服务 ok", ok is True, why)
ok2, why2 = probe_whisper("http://127.0.0.1:1/stt", timeout=2)
check("探测死端口失败", ok2 is False and why2 != "")

failed = [c for c in checks if not c[1]]
for name, okc, detail in checks:
    print(("  ok   " if okc else "  FAIL ") + name + (("  <- " + detail) if detail and not okc else ""))
print("通过 %d/%d" % (len(checks) - len(failed), len(checks)))
sys.exit(1 if failed else 0)

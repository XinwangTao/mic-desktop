# PCCrashGuard (second PC)

阻止「某个程序把整台 Windows 拖到卡死，只能断电」的保护程序。每 10 秒巡检：

- **按名称规则**：进程 RSS 超过上限、或同类进程数超过上限 → 直接杀掉（RelayDesk / MicDesktop / flix / node / python）
- **内存压力**：物理内存占用 ≥ 90% 时，杀掉最大的非白名单进程（≥800MB）
- **绝不杀**：系统进程与白名单（explorer、svchost、MsMpEng、powershell…）
- **日志**：`%LOCALAPPDATA%\PCCrashGuard\guard.log`（2MB 轮转），每分钟一条 BEAT 心跳（内存%、C 盘剩余、Top5 内存进程），杀进程记 KILL 行

## 安装（本机执行，无需管理员）

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File F:\projects\mic-desktop\second_pc\crash-guard\install_guard.ps1
```

安装器会复制脚本到 `%LOCALAPPDATA%\PCCrashGuard\`，注册登录自启的计划任务 `PCCrashGuard`（隐藏窗口、失败每分钟重启），并打印任务状态与日志尾部。

## 诊断卡死原因

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File F:\projects\mic-desktop\second_pc\crash-guard\diagnose.ps1
```

输出 `%LOCALAPPDATA%\PCCrashGuard\diagnose-<时间>.txt`：系统/内存/磁盘、近 14 天关键事件（Kernel-Power 41、BugCheck 1001、磁盘 129/153/157、显卡 TDR 4101、WHEA）、minidump 列表、计划任务、自启项、相关进程、显卡驱动、守卫日志尾部。

## 调整阈值

编辑 `guard.json`（安装后位于 `%LOCALAPPDATA%\PCCrashGuard\guard.json`），重启任务生效：

```powershell
Stop-ScheduledTask PCCrashGuard; Start-ScheduledTask PCCrashGuard
```

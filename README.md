# 浏览器工具箱 · browser-toolkit

Windows 上的一体化小工具：**清浏览器缓存 / 切极速·兼容内核 / 修网络 / 测延迟**，
一个单文件 exe，纯 Python 标准库 + tkinter，不需要安装 Python 或任何运行库。

> 顺手解决的问题：Chrome 缓存占几十上百 MB、搜狗「兼容模式」藏在设置深处、
> 网络一坏就得挨个敲 `ipconfig` / `netsh`、想知道某个接口地址到底通不通。

## 功能

| 页签 | 能干什么 |
| --- | --- |
| **一键清理** | 自动识别 Chrome / Edge / 搜狗高速浏览器 / 115 浏览器，一键清缓存；可选 Cookie、历史、表单（删前自动备份）；清理前自动关闭浏览器 |
| **极速 / 兼容模式** | 搜狗：真正改内核（`multicorestrategy` 0/1/2），改前备份、改后回读校验；Chrome / Edge / 115：用启动参数模拟兼容模式（关 GPU 加速） |
| **网络修复** | 刷 DNS、清 ARP、重置 WinHTTP 代理、关闭手动代理、重获 IP、DNS 改自动、重置 Winsock / TCP-IP；顶部实时显示 IPv4 / 网关 / DNS / 代理；缺权限会提示提权 |
| **延迟测试** | 每行一个域名 / IP / 完整接口地址（`https://api.xxx.com/v1` 也行），批量测丢包率、最快 / 平均 / 最慢；URL 会额外发一次真实 HTTP 请求显示状态码；附带自制悬浮监控窗（延迟曲线 + CPU + 内存 + 网速） |
| **LiteMonitor** | 对接第三方开源硬件监控 [Diorser/LiteMonitor](https://github.com/Diorser/LiteMonitor) 的启动/内嵌（**公开版不随包分发其二进制**，见下） |

## 截图

> 截图待补（作者本地环境含个人信息，先略）。跑起来就五个页签，很直白。

## 直接使用

到 [Releases](../../releases) 下载 `browser-toolkit-v1.1.0-win64.exe`，双击即可（第一次启动会解压到临时目录，等 2~3 秒）。

- 需要 Windows 10/11 x64
- 涉及 `netsh` 网络重置的项目需要管理员权限；工具会弹窗问你要不要提权重启
- 杀毒软件可能对「结束浏览器进程（taskkill）」敏感，属正常行为

## 从源码运行 / 构建

```powershell
# 运行（只需要 Python 3.10+，标准库即可）
python src/main.py

# 打包成单文件 exe
python -m pip install -r requirements-dev.txt
python make_icon.py
.\build.ps1
```

## 目录结构

```
src/
  main.py        入口，高 DPI 适配
  ui.py          tkinter 界面（5 个页签）
  browsers.py    浏览器识别、用户数据目录、清理目标路径表
  cleaner.py     清理引擎：统计 / 备份 / 健壮删除 / 报告
  kernel.py      搜狗内核切换 + Chromium 兼容模式启动参数
  netfix.py      网络修复 + ICMP/HTTP 探测
  monitor.py     自研悬浮监控窗
  litemon.py     第三方 LiteMonitor 的启动/内嵌与 .NET 运行时检测
  winproc.py     纯 ctypes 的进程枚举/结束、exe 版本号读取（免 psutil）
make_icon.py     生成 app.ico
build.ps1        PyInstaller 打包脚本
```

## 已知限制（实话实说）

1. **Chrome / Edge / 115 没有 IE 兼容内核**，不存在真正意义上的「兼容模式」。
   工具给的是「以兼容方式启动」：`--disable-gpu --disable-gpu-*`，能缓解老网页花屏白屏，
   代价是滚动/视频略慢。任何号称能给它换 IE 内核的工具都不靠谱。
2. **搜狗的内核开关是站点白名单优先**：全局设成兼容后，它自家导航页这类站点仍走 Chromium。
3. **LiteMonitor 内嵌是实验功能**：窗口能 `SetParent` 挂进来，但它是无边框 WPF 悬浮窗，
   内嵌后半透明区域会渲染成黑块，日常建议用它的独立悬浮窗，或本项目的自研监控。

## 第三方组件

- [Diorser/LiteMonitor](https://github.com/Diorser/LiteMonitor) — 轻量硬件监控。
  **本仓库不包含它的二进制文件**（上游未声明许可证）。工具里提供「去官方下载」入口，
  下载后放到 `%LOCALAPPDATA%\BrowserCleaner\lite\` 即可使用。

## License

MIT，见 [LICENSE](LICENSE)。

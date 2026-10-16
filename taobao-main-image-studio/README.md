# 淘宝主图工作台原型

Windows 优先的本地交互 Demo。提供“输入链接 → 来源审核 → 本地抠图审核 → 构图审核 → 主图与文案导出”的人工审批流程。

## 首次安装

前置条件：安装 **Python 3.10 或更高版本（64 位）**。当前 `rembg` 与 ONNX 推理环境不再支持 Python 3.7；脚本会优先识别官方 Python 安装目录，避免被旧版 Anaconda Python 覆盖。GPU 模式需要 NVIDIA 驱动和 GPU 版 ONNX Runtime。

在 PowerShell 中进入此目录，依次运行：

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\setup.ps1
.\Start-Demo.ps1
```

打开 http://127.0.0.1:8765 。默认先用轻量 `u2netp` 自动抠图；如果审核不满意，点击“框选修正”，框住完整商品后使用 `isnet-general-use` 重新处理，并按框选范围修正包装主体。两种模型都会在首次使用后缓存，之后可离线运行。

服务只监听本机 `127.0.0.1`，每次只处理一张图，超过 2048px 的长边会自动缩小，上传上限为 15MB。运行产生的图片仅保存在 `runtime/`，不上传云端。

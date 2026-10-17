# 淘宝主图工作台项目记录

更新时间：2026-10-16（依据当前工作区代码、README 和本地运行日志整理）

## 项目定位

这是一个 Windows 优先、仅监听本机的淘宝主图工作台原型。目标流程是“输入链接/本地参考图 → 来源审核 → 本地抠图审核 → 构图审核 → 主图与文案审核 → 导出任务清单”。产品和文案在生成前后都保留人工审核节点；运行产物写入 `runtime/`，不上传云端。

截至本记录建立时，仓库没有任何 Git commit；以下内容是从当前文件和日志可确认的基线，不代表曾经存在但已丢失的历史。

## 当前可确认的功能

- `index.html` 提供 6 个阶段的单页工作流，目标画布为 800×800；状态和任务数据保存在浏览器 `localStorage`。
- 来源阶段支持粘贴多行链接和选择本地图片。链接目前只生成候选占位记录，不抓取淘宝页面；真实离线抠图要求第一步导入并批准本地图片。
- `backend/app.py` 是 FastAPI 本地服务，默认由 Uvicorn 监听 `127.0.0.1:8765`。
- 图片处理会校正 EXIF 方向、转 RGBA、将长边限制为 2048px，上传上限为 15MB；结果保存在 `runtime/uploads`、`runtime/cutouts`、`runtime/backgrounds`。
- 抠图支持 CPU 和检测到的 CUDA/ROCm/DirectML provider；模型目录包含 `u2netp`、`u2net`、`isnet-general-use`、BiRefNet、BRIA RMBG 和仅框选使用的 SAM。默认自动模型为 `u2netp`，框选默认模型为 `isnet-general-use`，可在界面下载、删除和设置默认模型。
- 框选修正通过 `/api/refine-with-box` 按选区修正主体遮罩；SAM 当前仅允许 CPU 框选模式。
- 背景模型接口支持 Tiny-SD、DreamShaper 8、Realistic Vision 5.1、LCM DreamShaper v7 的本地可用性检查和 `/api/generate-background`；实际生成依赖 `diffusers` 与本地模型文件。
- 文案接口 `/api/generate-copy` 使用本地 Qwen 2.5 3B GGUF（若存在）生成 JSON 草稿，并在提示词中限制虚构功效、认证、销量和绝对化承诺。
- 成图阶段可叠加真实透明商品、背景、标题/卖点、位置/尺寸/颜色和水印；最终当前稳定导出的是 `manifest.json` 任务清单。

## 运行方式与依赖

1. Windows PowerShell 中进入项目目录，运行 `Set-ExecutionPolicy -Scope Process Bypass`。
2. 运行 `./setup.ps1`：脚本要求 64 位 Python 3.10+，创建 `.venv` 并安装 `requirements.txt`。
3. 运行 `./Start-Demo.ps1`，浏览器打开 `http://127.0.0.1:8765`。
4. 可选运行 `download_models.py` 下载 `u2net` 与 `birefnet-general-lite`；`download_background_models.py` 下载背景模型到 `runtime/background-models`。

主要依赖：FastAPI/Uvicorn、Pillow、rembg、ONNX Runtime（CPU/GPU 变体）、NumPy、PyTorch CUDA 11.8、Diffusers、Transformers、Accelerate、Safetensors、Hugging Face Hub、llama-cpp-python。具体版本约束以 `requirements.txt` 为准。

## 当前日志可确认的结果

- 2026-10-14：依赖安装日志显示 FastAPI 0.143.0、rembg 2.0.69、ONNX Runtime 1.23.2 等安装成功。
- 2026-10-14：`download_models.py` 日志显示 `u2net` 和 `birefnet-general-lite` 下载完成。
- 2026-10-14：Demo 服务曾成功启动并收到 `/api/models` 请求。
- 2026-10-14：GPU runtime 安装日志出现 Windows `WinError 32`（文件被占用），不能据此确认 GPU provider 可用。
- 2026-10-15：背景模型下载因远端连接中断而失败；模型是否完整必须通过 `/api/background-models` 或文件检查重新确认。

## 已知问题与边界

- 淘宝链接解析、图片下载、授权核验和正式桌面壳仍是占位/待实现能力；不能把竞品候选直接当作可发布素材。
- 浏览器刷新会丢失本地 `File` 对象，需要重新选择图片；localStorage 只保存元数据和任务状态。
- 背景模型体积大、依赖网络和显存；下载中断可能留下不完整目录。生成能力必须在模型文件完整且依赖可导入时验证。
- `requirements.txt` 同时声明 CPU/GPU 相关包和 CUDA 11.8 PyTorch，实际 provider、驱动和版本兼容性需在目标机器单独验证。
- 目前没有自动化测试、锁定依赖文件或 CI；服务测试应至少覆盖健康检查、模型列表、图片上传限制和错误路径。

## 后续路线（按风险优先）

1. 先补最小 API/浏览器冒烟测试与环境诊断页，明确 CPU/GPU、模型缓存和版本状态。
2. 将任务目录、原图/抠图/主图和审核记录落盘，解决刷新丢失 File 对象的问题。
3. 实现受控的公开页面解析和授权字段，保留人工批准门禁。
4. 完善背景生成的可恢复下载、校验和清理，并为各模型记录版本、来源、许可证和显存要求。
5. 再考虑桌面壳、批量任务、更多版式和发布前合规检查。

## 记录约定

每次功能优化后的变更摘要、验证结果、模型/依赖变动和 Git 提交号，按 `CONTRIBUTING_WORKFLOW.md` 的模板追加到本文件的“变更记录”区（必要时新增小节）。

## 变更记录

### 2026-10-16 · 建立项目基线

- 新增本项目记录和协作/Git 备份流程文档。
- 收紧忽略规则，避免把虚拟环境、运行日志和 PID 文件误提交。
- 代码与依赖未因本次记录工作修改。
- 验证：静态检查文件清单、读取 README/核心路由和现有运行日志；未运行完整模型下载或生成流程。

### 2026-10-16 · 真实主图 PNG 导出与目录保存

- 变更：`index.html` 新增浏览器端 800×800 Canvas 合成导出，按当前背景、透明商品、Logo 水印、标题和卖点生成 PNG；导出时根据商品名和时间创建 `产品名_YYMMDD_HHMM` 子目录，并写入 `<产品名>_800x800.png` 与 `manifest.json`。
- 兼容性：支持 `showDirectoryPicker` 的浏览器会让用户选择保存目录并写入子目录；不支持时降级为分别下载 PNG 与 manifest 文件，不会修改服务器端 `runtime/`。
- 模型与依赖：无变化；继续使用现有本地背景/抠图/文案模型和浏览器 Canvas API。
- 验证：检查导出函数、文件名清理、800×800 Canvas 尺寸、目录降级分支及 `git diff --check`；未在本轮实际选择目录并完成浏览器下载冒烟测试。
- Git：功能提交 `9f1b7ce19f87ac7bb5aa1617083fc100009eef73`（本条记录随后以文档提交固化）。

### 2026-10-16 · 默认导出目录持久化

- 变更：第一步输入界面新增“选择默认目录”按钮；目录句柄通过 IndexedDB `taobao-main-image-studio/settings` 持久化，并在页面加载和后续导出时尝试恢复及重新申请写权限。
- 导出行为：已有授权目录时直接复用；没有目录时首次导出才弹出目录选择；用户取消目录选择显示“已取消”而非失败，并注明未写入文件。
- 兼容性：没有目录选择 API 时继续分别下载 PNG 和 `manifest.json`；目录选择/权限异常会显示明确状态，不会伪装成成功。
- 模型与依赖：无变化；仅使用浏览器 IndexedDB、File System Access API 和现有 Canvas 导出逻辑。
- 验证：检查按钮入口、IndexedDB 读写键、权限查询/申请、AbortError 分支和无 API 降级分支，并通过 `git diff --check`；未在本轮实际授权目录、刷新页面后复用或取消对话框进行浏览器冒烟测试。
- Git：功能提交 `857137745f40115f835be476d8203375322752d1`（本条记录随后以文档提交固化）。

### 2026-10-16 · 导出目录提示与严格复用

- 变更：输入链接页现在显示已保存的目录名称，并说明浏览器安全限制不会向网页暴露 Windows 完整路径；成图页主操作按钮改名为“输出结果图”。
- 导出行为：导出只复用已保存的默认目录；若没有目录句柄，不再在成图页重新弹出选择框，而是提示回到“输入链接”界面设置默认目录。用户取消导出时显示“已取消导出，未写入文件”；目录权限错误继续以明确状态反馈。
- 兼容性：不支持目录 API 时仍降级下载 PNG 和 `manifest.json`；模型与依赖无变化。
- 验证：检查默认目录标签、路径隐藏说明、按钮文本、`NotFoundError` 回退提示和 `AbortError` 分支，并通过 `git diff --check`；未在本轮实际切换页面、撤销权限或点击取消对话框进行浏览器冒烟测试。
- Git：功能提交 `617ade247e6302255e30626c06d38eba486e5904`（本条记录随后以文档提交固化）。

### 2026-10-17 · Windows 原生结果目录与服务端导出

- 变更：后端新增 Windows 本地默认导出目录配置（`runtime/export-settings.json`）、原生目录选择、导出 PNG 与 `manifest.json`、以及用 Explorer 打开默认目录的 API。服务端验证导出图片为 800×800 PNG、限制上传大小，并清理产品名/目录名以避免非法路径。
- 前端：默认目录显示后端返回的完整本地路径；“输出结果图”在成图页直接合成并提交结果，不再跳转步骤，成功后显示图片路径；新增“打开图片目录”按钮。
- 编辑控制：商品主体横纵坐标滑块扩展为 0–100；Logo 旋转范围改为 -180–180，并把旧的 0–360 保存值转换为兼容范围。
- 兼容性与边界：当前导出依赖本地 FastAPI/Windows 目录选择和 Explorer；未配置目录、目录选择取消、权限/API 错误均由界面显示失败原因。服务端设置文件和结果文件均位于被忽略的 `runtime/`，不进入 Git。
- 模型与依赖：无变化；新增能力使用 Python 标准库 `tkinter`、`os`、`subprocess` 和已有 Pillow/FastAPI。
- 验证：检查新增 API 路由、800×800/30MB/manifest JSON 校验、路径约束、前端请求及滑块/旋转兼容逻辑，并通过 `git diff --check`；未实际执行 Windows 目录选择、写入结果文件或 Explorer 打开操作。
- Git：功能提交 `8a09c253d281ffe7342ce69e3d8e57a20008371d`（本条记录随后以文档提交固化）。

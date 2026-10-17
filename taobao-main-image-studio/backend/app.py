"""Local-only background-removal service for the Main Image Studio demo."""

from __future__ import annotations

import json
import os
import subprocess
from io import BytesIO
from pathlib import Path
from uuid import uuid4

import cv2
import numpy as np
import onnxruntime as ort
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from PIL import Image, ImageOps, UnidentifiedImageError

APP_ROOT = Path(__file__).resolve().parent.parent
RUNTIME = APP_ROOT / "runtime"
EXPORT_SETTINGS = RUNTIME / "export-settings.json"
UPLOADS = RUNTIME / "uploads"
CUTOUTS = RUNTIME / "cutouts"
BACKGROUNDS = RUNTIME / "backgrounds"
MAX_EDGE = 2048
MAX_UPLOAD_BYTES = 15 * 1024 * 1024
AUTO_MODEL_NAME = "u2netp"
BOX_MODEL_NAME = "isnet-general-use"
Image.MAX_IMAGE_PIXELS = 36_000_000

for folder in (UPLOADS, CUTOUTS, BACKGROUNDS):
    folder.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="主图工坊本地抠图服务")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["null", "http://127.0.0.1:8765", "http://localhost:8765"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

_sessions = {}
_copy_llm = None
_background_pipelines = {}
MODEL_SETTINGS = RUNTIME / "model-settings.json"
COPY_MODEL = RUNTIME / "text-models" / "qwen2.5-3b-instruct" / "qwen2.5-3b-instruct-q4_k_m.gguf"
BACKGROUND_MODELS = {
    "tiny-sd": {"label": "Tiny-SD", "purpose": "快速草稿", "path": RUNTIME / "background-models" / "tiny-sd", "steps": 6},
    "dreamshaper-8": {"label": "DreamShaper 8", "purpose": "标准场景质感", "path": RUNTIME / "background-models" / "dreamshaper-8", "steps": 12, "fp16": True},
    "realistic-vision-v5.1": {"label": "Realistic Vision 5.1", "purpose": "写实摄影场景", "path": RUNTIME / "background-models" / "realistic-vision-v5.1" / "Realistic_Vision_V5.1_fp16-no-ema.safetensors", "steps": 12, "single_file": True},
    "lcm-dreamshaper-v7": {"label": "LCM DreamShaper v7", "purpose": "快速高质量草稿", "path": RUNTIME / "background-models" / "lcm-dreamshaper-v7" / "LCM_Dreamshaper_v7_4k.safetensors", "steps": 6, "single_file": True, "fp16": True},
}


def background_model_ready(model_id: str) -> bool:
    item = BACKGROUND_MODELS[model_id]
    path = item["path"]
    if model_id in {"realistic-vision-v5.1", "lcm-dreamshaper-v7"}:
        return path.is_file() and path.stat().st_size > 1_000_000_000
    required = ["model_index.json", "unet", "text_encoder", "vae"]
    return path.is_dir() and all((path / name).exists() for name in required)
MODEL_CATALOG = [
    {"id": "u2netp", "label": "u2netp", "purpose": "轻量自动抠图", "modes": ["auto", "box"]},
    {"id": "u2net", "label": "u2net", "purpose": "更完整的通用抠图", "modes": ["auto", "box"]},
    {"id": "isnet-general-use", "label": "isnet-general-use", "purpose": "标准商品抠图", "modes": ["auto", "box"]},
    {"id": "birefnet-general-lite", "label": "BiRefNet General Lite", "purpose": "较高质量商品抠图", "modes": ["auto", "box"]},
    {"id": "birefnet-general", "label": "BiRefNet General", "purpose": "高质量通用抠图（更占资源）", "modes": ["auto", "box"]},
    {"id": "bria-rmbg", "label": "BRIA RMBG", "purpose": "高质量通用抠图", "modes": ["auto", "box"]},
    {"id": "sam", "label": "SAM ViT-B", "purpose": "框选分割", "modes": ["box"]},
]


def model_settings() -> dict[str, str]:
    if MODEL_SETTINGS.exists():
        try:
            return json.loads(MODEL_SETTINGS.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
    return {"auto": AUTO_MODEL_NAME, "box": BOX_MODEL_NAME}


def save_model_settings(settings: dict[str, str]) -> None:
    MODEL_SETTINGS.write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")


def export_directory() -> Path | None:
    try:
        raw = json.loads(EXPORT_SETTINGS.read_text(encoding="utf-8"))
        path = Path(raw.get("directory", ""))
        return path if path.is_dir() else None
    except (OSError, ValueError, TypeError):
        return None


def save_export_directory(path: Path) -> None:
    EXPORT_SETTINGS.write_text(json.dumps({"directory": str(path)}, ensure_ascii=False, indent=2), encoding="utf-8")


def safe_export_name(value: str, fallback: str) -> str:
    cleaned = "".join("_" if char in '\\/:*?\"<>|' else char for char in value).strip().replace(" ", "_")
    return (cleaned[:80] or fallback).strip("._") or fallback


def cached_models() -> list[dict[str, object]]:
    cache = Path.home() / ".u2net"
    files = {path.name for path in cache.glob("*.onnx")} if cache.exists() else set()
    models = []
    for item in MODEL_CATALOG:
        if item["id"] == "sam":
            ready = {"sam_vit_b_01ec64.encoder.quant.onnx", "sam_vit_b_01ec64.decoder.quant.onnx"}.issubset(files)
        elif item["id"] == "bria-rmbg":
            ready = any(name.startswith("bria-rmbg") for name in files)
        else:
            ready = f"{item['id']}.onnx" in files
        size_bytes = 0
        if ready:
            if item["id"] == "sam":
                size_bytes = sum((cache / name).stat().st_size for name in files if name.startswith("sam_vit_b_01ec64.") and (cache / name).exists())
            else:
                model_file = cache / f"{item['id']}.onnx"
                if model_file.exists():
                    size_bytes = model_file.stat().st_size
        models.append({**item, "downloaded": ready, "size_bytes": size_bytes, "size_mb": round(size_bytes / 1024 / 1024, 1) if size_bytes else 0})
    return models


def providers_for(device: str) -> list[str]:
    available = ort.get_available_providers()
    if device == "gpu":
        for provider in ("CUDAExecutionProvider", "ROCMExecutionProvider", "DmlExecutionProvider"):
            if provider in available:
                return [provider, "CPUExecutionProvider"]
        raise HTTPException(status_code=400, detail="当前未检测到可用 GPU 运行时；请安装 onnxruntime-gpu 及匹配的显卡驱动后再试。")
    return ["CPUExecutionProvider"]


def model_session(name: str, device: str):
    """Load a model only when the matching mode is actually requested."""
    if name == "sam" and device != "cpu":
        raise HTTPException(status_code=400, detail="当前 SAM 框选实现仅支持 CPU。")
    key = (name, device)
    if key not in _sessions:
        from rembg import new_session

        if name == "sam":
            _sessions[key] = new_session("sam", sam_model="sam_vit_b_01ec64", sam_quant=True)
        else:
            _sessions[key] = new_session(name, providers=providers_for(device))
    return _sessions[key]


def validate_model(name: str, mode: str) -> None:
    models = {item["id"]: item for item in cached_models()}
    item = models.get(name)
    if item is None:
        raise HTTPException(status_code=400, detail="所选模型未在本地缓存中找到。")
    if mode not in item["modes"]:
        raise HTTPException(status_code=400, detail="SAM 模型只能在框选模式中使用。")


def normalized_image(raw: bytes, max_edge: int = MAX_EDGE) -> tuple[bytes, int, int]:
    try:
        with Image.open(BytesIO(raw)) as opened:
            image = ImageOps.exif_transpose(opened).convert("RGBA")
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise HTTPException(status_code=400, detail="无法读取图片，请上传 JPG、PNG 或 WebP 文件。") from exc

    if max(image.size) > max_edge:
        image.thumbnail((max_edge, max_edge), Image.Resampling.LANCZOS)
    stream = BytesIO()
    image.save(stream, format="PNG", optimize=True)
    return stream.getvalue(), image.width, image.height


@app.get("/api/health")
def health() -> dict[str, object]:
    return {
        "ok": True,
        "auto_model": AUTO_MODEL_NAME,
        "box_model": BOX_MODEL_NAME,
        "max_edge": MAX_EDGE,
        "cached_models": cached_models(),
        "available_providers": ort.get_available_providers(),
        "loaded_models": [f"{name}:{device}" for name, device in _sessions],
    }


@app.get("/api/export-directory")
def get_export_directory() -> dict[str, object]:
    directory = export_directory()
    return {"configured": directory is not None, "path": str(directory) if directory else None}


@app.post("/api/export-directory/select")
def select_export_directory() -> dict[str, object]:
    try:
        import tkinter as tk
        from tkinter import filedialog

        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        selected = filedialog.askdirectory(title="选择主图结果保存目录", mustexist=True)
        root.destroy()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"无法打开 Windows 目录选择窗口：{exc}") from exc
    if not selected:
        return {"configured": False, "cancelled": True}
    directory = Path(selected).resolve()
    save_export_directory(directory)
    return {"configured": True, "path": str(directory)}


@app.post("/api/export-result")
async def export_result(
    image: UploadFile = File(...),
    manifest: str = Form(...),
    product_name: str = Form("product"),
    folder_name: str = Form(""),
) -> dict[str, object]:
    directory = export_directory()
    if directory is None:
        raise HTTPException(status_code=400, detail="请先在输入链接页面设置默认导出目录。")
    raw = await image.read()
    if not raw or len(raw) > 30 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="导出图片为空或超过 30MB 限制。")
    try:
        with Image.open(BytesIO(raw)) as opened:
            if opened.size != (800, 800):
                raise ValueError("不是 800×800 图片")
            output_image = BytesIO()
            opened.convert("RGBA").save(output_image, format="PNG", optimize=True)
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=f"无法保存导出图片：{exc}") from exc
    try:
        manifest_data = json.loads(manifest)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="导出任务记录格式无效。") from exc
    product = safe_export_name(product_name, "product")
    folder = safe_export_name(folder_name, "export")
    target = (directory / folder).resolve()
    if directory not in target.parents:
        raise HTTPException(status_code=400, detail="导出目录名称无效。")
    target.mkdir(parents=True, exist_ok=True)
    image_path = target / f"{product}_800x800.png"
    image_path.write_bytes(output_image.getvalue())
    (target / "manifest.json").write_text(json.dumps(manifest_data, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"ok": True, "folder_path": str(target), "image_path": str(image_path)}


@app.post("/api/open-export-directory")
def open_export_directory() -> dict[str, object]:
    directory = export_directory()
    if directory is None:
        raise HTTPException(status_code=400, detail="请先在输入链接页面设置默认导出目录。")
    try:
        os.startfile(str(directory))
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"无法打开文件夹：{exc}") from exc
    return {"ok": True, "path": str(directory)}


@app.get("/api/models")
def models() -> dict[str, object]:
    providers = ort.get_available_providers()
    gpu_ready = any(provider in providers for provider in ("CUDAExecutionProvider", "ROCMExecutionProvider", "DmlExecutionProvider"))
    return {
        "models": cached_models(),
        "defaults": model_settings(),
        "devices": [
            {"id": "cpu", "label": "CPU", "available": True},
            {"id": "gpu", "label": "GPU", "available": gpu_ready},
        ],
    }


@app.get("/api/background-models")
def background_models() -> dict[str, object]:
    return {"models": [
        {"id": model_id, "label": item["label"], "purpose": item["purpose"], "downloaded": background_model_ready(model_id)}
        for model_id, item in BACKGROUND_MODELS.items()
    ]}


@app.post("/api/models/{model_id}/download")
def download_model(model_id: str) -> dict[str, object]:
    item = next((x for x in MODEL_CATALOG if x["id"] == model_id), None)
    if item is None:
        raise HTTPException(status_code=404, detail="不支持的模型。")
    try:
        from rembg.sessions import sessions
        session_class = sessions[model_id]
        session_class.download_models()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"模型下载失败：{exc}") from exc
    return {"ok": True, "model": model_id, "models": cached_models()}


@app.delete("/api/models/{model_id}")
def delete_model(model_id: str) -> dict[str, object]:
    item = next((x for x in MODEL_CATALOG if x["id"] == model_id), None)
    if item is None:
        raise HTTPException(status_code=404, detail="不支持的模型。")
    cache = Path.home() / ".u2net"
    patterns = ["sam_vit_b_01ec64.*"] if model_id == "sam" else [f"{model_id}.*"]
    removed = []
    for pattern in patterns:
        for path in cache.glob(pattern):
            path.unlink(missing_ok=True)
            removed.append(path.name)
    _sessions.pop((model_id, "cpu"), None)
    _sessions.pop((model_id, "gpu"), None)
    return {"ok": True, "removed": removed, "models": cached_models()}


@app.post("/api/models/defaults")
def set_defaults(payload: dict[str, str]) -> dict[str, object]:
    settings = model_settings()
    for mode in ("auto", "box"):
        if mode in payload:
            validate_model(payload[mode], mode)
            settings[mode] = payload[mode]
    save_model_settings(settings)
    return {"ok": True, "defaults": settings}


@app.post("/api/generate-copy")
def generate_copy(payload: dict[str, str]) -> dict[str, object]:
    product = (payload.get("product") or "").strip()
    if len(product) < 2:
        raise HTTPException(status_code=400, detail="请先填写商品名称、特点或使用场景。")
    prompt = f"你是淘宝电商文案助手。根据以下商品资料，生成3组主图短文案。每组包含title和selling_point，中文，每项不超过16个字。只能使用资料中明确存在的信息，不得虚构材质、功效、销量、认证或绝对化承诺。只输出JSON数组，不要解释。商品资料：{product}"
    llm = copy_model()
    try:
        result = llm.create_chat_completion(
            messages=[
                {"role": "system", "content": "你负责生成简洁、合规的中文电商主图文案。"},
                {"role": "user", "content": prompt},
            ],
            temperature=0.7,
            max_tokens=240,
        )
        text = result["choices"][0]["message"]["content"].strip()
        start, end = text.find("["), text.rfind("]")
        if start >= 0 and end > start:
            text = text[start : end + 1]
        items = json.loads(text)
        if not isinstance(items, list):
            raise ValueError("模型返回格式不是数组")
        normalized_items = []
        for item in items[:5]:
            if isinstance(item, dict):
                normalized_items.append({"title": str(item.get("title", "")), "selling_point": str(item.get("selling_point", ""))})
            elif isinstance(item, list) and len(item) >= 2:
                normalized_items.append({"title": str(item[0]), "selling_point": str(item[1])})
        items = normalized_items
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"本地文案生成失败：{exc}") from exc
    return {"ok": True, "model": "Qwen2.5-3B-Instruct (本地)", "items": items}


def save_result(source: bytes, result: bytes, width: int, height: int, model: str) -> dict[str, object]:
    identifier = uuid4().hex
    source_path = UPLOADS / f"{identifier}.png"
    output_path = CUTOUTS / f"{identifier}.png"
    source_path.write_bytes(source)
    output_path.write_bytes(result)
    return {
        "ok": True,
        "model": model,
        "width": width,
        "height": height,
        "output_url": f"/runtime/cutouts/{output_path.name}",
        "output_path": str(output_path),
    }


def repair_package_mask(result: bytes, left: float, top: float, right: float, bottom: float) -> bytes:
    """Make an automatic mask usable for printed bags and boxes after user boxing.

    Automatic models can classify artwork printed on a package as separate objects,
    leaving holes in the alpha mask. For a deliberately user-boxed package, retain the
    biggest outline inside that box and fill its interior. This mode is intentionally
    used only by the box endpoint, never by automatic removal.
    """
    image = Image.open(BytesIO(result)).convert("RGBA")
    alpha = np.asarray(image.getchannel("A"))
    binary = np.where(alpha > 24, 255, 0).astype(np.uint8)
    close_size = max(7, int(min(image.size) * 0.012) | 1)
    closed = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, np.ones((close_size, close_size), np.uint8))
    contours, _ = cv2.findContours(closed, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return result

    def score(contour: np.ndarray) -> float:
        x, y, w, h = cv2.boundingRect(contour)
        cx, cy = x + w / 2, y + h / 2
        overlaps = not (x + w < left or x > right or y + h < top or y > bottom)
        centered = left <= cx <= right and top <= cy <= bottom
        return cv2.contourArea(contour) if overlaps and centered else -1

    package = max(contours, key=score)
    if score(package) <= 0:
        return result
    repaired = np.zeros_like(binary)
    cv2.drawContours(repaired, [package], -1, 255, thickness=cv2.FILLED)
    padding = max(6, int(min(image.size) * 0.02))
    clip = np.zeros_like(binary)
    cv2.rectangle(
        clip,
        (max(0, int(left) - padding), max(0, int(top) - padding)),
        (min(image.width - 1, int(right) + padding), min(image.height - 1, int(bottom) + padding)),
        255,
        thickness=cv2.FILLED,
    )
    repaired = cv2.bitwise_and(repaired, clip)
    image.putalpha(Image.fromarray(repaired))
    stream = BytesIO()
    image.save(stream, format="PNG", optimize=True)
    return stream.getvalue()


async def read_image(image: UploadFile, max_edge: int = MAX_EDGE) -> tuple[bytes, int, int]:
    if not (image.content_type or "").startswith("image/"):
        raise HTTPException(status_code=400, detail="请上传图片文件。")
    raw = await image.read()
    if not raw:
        raise HTTPException(status_code=400, detail="图片为空。")
    if len(raw) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="图片超过 15 MB；请先压缩或缩小后再试。")
    return normalized_image(raw, max_edge=max_edge)


def model_image_edge(model: str) -> int:
    # BRIA RMBG has a large decoder and commonly exceeds 4 GB VRAM at 2K input.
    # Keep the local Windows demo usable on 4 GB cards; the final main image is 800 px.
    return 1024 if model == "bria-rmbg" else MAX_EDGE


def friendly_model_error(exc: Exception) -> str:
    message = str(exc)
    lowered = message.lower()
    if "failed to allocate memory" in lowered or "out of memory" in lowered or "outofmemory" in lowered:
        return "模型推理显存/内存不足。请切换 CPU，或先将图片缩小到 1024 像素以内后重试。"
    return message


def copy_model():
    global _copy_llm
    if _copy_llm is None:
        if not COPY_MODEL.exists():
            raise HTTPException(status_code=503, detail="本地文案模型尚未下载完成。")
        try:
            from llama_cpp import Llama
            _copy_llm = Llama(model_path=str(COPY_MODEL), n_ctx=2048, n_threads=4, verbose=False)
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"本地文案模型加载失败：{exc}") from exc
    return _copy_llm


def background_pipeline(model_id: str):
    """Load Tiny-SD lazily so normal cutout work does not occupy GPU memory."""
    if model_id not in BACKGROUND_MODELS:
        raise HTTPException(status_code=400, detail="不支持的背景模型。")
    if model_id not in _background_pipelines:
        config = BACKGROUND_MODELS[model_id]
        if not background_model_ready(model_id):
            raise HTTPException(status_code=503, detail=f"{config['label']} 正在下载或尚未下载完成。")
        try:
            import torch
            from diffusers import StableDiffusionPipeline

            device = "cuda" if torch.cuda.is_available() else "cpu"
            # Tiny-SD's VAE produces NaN/black frames in FP16 on GTX 1650.
            # Larger SD 1.5 models use their dedicated FP16 weights to fit 4 GB VRAM.
            dtype = torch.float16 if config.get("fp16") and device == "cuda" else torch.float32
            if config.get("single_file"):
                pipe = StableDiffusionPipeline.from_single_file(str(config["path"]), torch_dtype=dtype, safety_checker=None)
            else:
                pipe = StableDiffusionPipeline.from_pretrained(
                    str(config["path"]), local_files_only=True, torch_dtype=dtype, safety_checker=None, feature_extractor=None,
                    **({"variant": "fp16"} if config.get("fp16") else {}),
                )
            pipe.enable_attention_slicing()
            if dtype == torch.float16 and device == "cuda":
                pipe.enable_model_cpu_offload()
            else:
                pipe.to(device)
            _background_pipelines[model_id] = pipe
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"Tiny-SD 背景模型加载失败：{exc}") from exc
    return _background_pipelines[model_id]


@app.post("/api/generate-background")
def generate_background(payload: dict[str, object]) -> dict[str, object]:
    prompt = str(payload.get("prompt") or "").strip()
    if len(prompt) < 3:
        raise HTTPException(status_code=400, detail="请至少填写一段背景描述。")
    try:
        import torch

        seed = int(payload.get("seed") or 0)
        generator = torch.Generator(device="cuda" if torch.cuda.is_available() else "cpu")
        if seed:
            generator.manual_seed(seed)
        else:
            seed = generator.seed()
        model_id = str(payload.get("model") or "tiny-sd")
        config = BACKGROUND_MODELS.get(model_id)
        if not config:
            raise HTTPException(status_code=400, detail="不支持的背景模型。")
        image = background_pipeline(model_id)(
            prompt=prompt,
            negative_prompt="text, words, logo, watermark, product, bottle, cup, package, person, blurry, low quality",
            width=512,
            height=512,
            num_inference_steps=config["steps"],
            guidance_scale=6.5,
            generator=generator,
        ).images[0].resize((800, 800), Image.Resampling.LANCZOS)
        identifier = uuid4().hex
        path = BACKGROUNDS / f"{identifier}.png"
        image.save(path, format="PNG", optimize=True)
        return {
            "ok": True,
            "model": f"{config['label']} (local)",
            "prompt": prompt,
            "seed": seed,
            "width": 800,
            "height": 800,
            "output_url": f"/runtime/backgrounds/{path.name}",
            "output_path": str(path),
        }
    except HTTPException:
        raise
    except Exception as exc:
        message = str(exc)
        if "out of memory" in message.lower():
            message = "显存不足。请先关闭其他占用显卡的软件后重试。"
        raise HTTPException(status_code=500, detail=f"本地背景生成失败：{message}") from exc


@app.post("/api/remove-background")
async def remove_background(
    image: UploadFile = File(...),
    model: str = Form(AUTO_MODEL_NAME),
    device: str = Form("cpu"),
) -> dict[str, object]:
    validate_model(model, "auto")
    normalized, width, height = await read_image(image, max_edge=model_image_edge(model))
    try:
        from rembg import remove

        result = remove(normalized, session=model_session(model, device))
    except Exception as exc:  # Surface a compact actionable message to the UI.
        raise HTTPException(status_code=500, detail=f"本地模型运行失败：{friendly_model_error(exc)}") from exc
    return save_result(normalized, result, width, height, f"{model} ({device.upper()})")


@app.post("/api/refine-with-box")
async def refine_with_box(
    image: UploadFile = File(...),
    left: float = Form(...),
    top: float = Form(...),
    right: float = Form(...),
    bottom: float = Form(...),
    model: str = Form(BOX_MODEL_NAME),
    device: str = Form("cpu"),
) -> dict[str, object]:
    validate_model(model, "box")
    normalized, width, height = await read_image(image, max_edge=model_image_edge(model))
    left, right = sorted((max(0, min(left, width)), max(0, min(right, width))))
    top, bottom = sorted((max(0, min(top, height)), max(0, min(bottom, height))))
    if right - left < 12 or bottom - top < 12:
        raise HTTPException(status_code=400, detail="框选区域过小，请完整框住商品主体。")
    try:
        from rembg import remove

        extra = {"sam_prompt": [{"type": "rectangle", "data": [left, top, right, bottom]}]} if model == "sam" else {}
        result = remove(normalized, session=model_session(model, device), **extra)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"框选分割失败：{friendly_model_error(exc)}") from exc
    repaired = repair_package_mask(result, left, top, right, bottom)
    return save_result(normalized, repaired, width, height, f"{model} ({device.upper()}, box + package repair)")


@app.get("/")
def root() -> RedirectResponse:
    return RedirectResponse("/index.html")


app.mount("/runtime", StaticFiles(directory=RUNTIME), name="runtime")
app.mount("/", StaticFiles(directory=APP_ROOT, html=True), name="web")

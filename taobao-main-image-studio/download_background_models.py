"""Download public background models without Git LFS checkout memory spikes."""

from pathlib import Path

from huggingface_hub import snapshot_download

ROOT = Path(__file__).resolve().parent / "runtime" / "background-models"
MODELS = {
    "dreamshaper-8": ("Lykon/dreamshaper-8", [
        "model_index.json", "scheduler/*", "tokenizer/*", "text_encoder/config.json",
        "text_encoder/model.fp16.safetensors", "unet/config.json",
        "unet/diffusion_pytorch_model.fp16.safetensors", "vae/config.json",
        "vae/diffusion_pytorch_model.fp16.safetensors",
    ]),
    "realistic-vision-v5.1": ("SG161222/Realistic_Vision_V5.1_noVAE", [
        "Realistic_Vision_V5.1_fp16-no-ema.safetensors",
    ]),
    "lcm-dreamshaper-v7": ("SimianLuo/LCM_Dreamshaper_v7", [
        "model_index.json", "scheduler/*", "tokenizer/*", "text_encoder/config.json",
        "text_encoder/model.safetensors", "unet/config.json", "unet/diffusion_pytorch_model.safetensors",
        "vae/config.json", "vae/diffusion_pytorch_model.safetensors",
    ]),
}

for name, (repo, allow_patterns) in MODELS.items():
    print(f"Downloading {name} from {repo}", flush=True)
    snapshot_download(repo_id=repo, local_dir=ROOT / name, allow_patterns=allow_patterns)
    print(f"Finished {name}", flush=True)

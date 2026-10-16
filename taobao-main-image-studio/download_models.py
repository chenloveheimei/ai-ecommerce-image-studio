from rembg.sessions import sessions

for name in ("u2net", "birefnet-general-lite"):
    print(f"Downloading {name}...", flush=True)
    sessions[name].download_models()
    print(f"Finished {name}", flush=True)

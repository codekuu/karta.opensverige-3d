"""Redraw one claimed district with a MultiDiffusion-style overlap.

Windows are 1024 pixels and the stride is 512. At every denoising step the
noise prediction of each window is added onto the shared latent, then divided
by how many windows covered that token, then the scheduler steps once.
That is the MultiDiffusion average (Bar-Tal et al.), applied to one tile.

Model weights download into the contributor's own Hugging Face cache:
  addlabsviral/Qwen-Image-2.1-Turbo-4bit
  handful/mndf-pixel-qwen-image-2.1

The checkpoint is a bitsandbytes 4-bit quant and runs on NVIDIA CUDA.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from repo import load_districts, repo_root

MODEL_ID = "addlabsviral/Qwen-Image-2.1-Turbo-4bit"
LORA_ID = "handful/mndf-pixel-qwen-image-2.1"
WINDOW = 1024
STEP = 512
VAE_ALIGN = 32
MAX_SIDE = 4096
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def pad_size(n: int, window: int = WINDOW, step: int = STEP, multiple: int = VAE_ALIGN) -> int:
    if n <= window:
        target = window
    else:
        k = -(-(n - window) // step)  # ceil division
        target = window + k * step
    if target % multiple != 0:
        raise ValueError(f"Padded size {target} is not a multiple of {multiple}")
    return target


def get_views(width: int, height: int, window: int = WINDOW, step: int = STEP) -> list[tuple[int, int]]:
    """Top-left corners of each window. The tile must already be padded to the grid."""
    if width < window or height < window:
        raise ValueError("Tile is smaller than one window; pad it first")
    if (width - window) % step != 0 or (height - window) % step != 0:
        raise ValueError("Stride does not land on the far edge; pad the tile first")
    views = []
    for top in range(0, height - window + 1, step):
        for left in range(0, width - window + 1, step):
            views.append((top, left))
    if not views:
        raise ValueError("No windows")
    return views


def window_token_indices(
    full_h: int,
    full_w: int,
    top: int,
    left: int,
    window: int = WINDOW,
    scale: int = 16,
) -> list[int]:
    """Row-major latent tokens for one window. Matches QwenImage21Pipeline._pack_latents."""
    if full_h % scale or full_w % scale or window % scale or top % scale or left % scale:
        raise ValueError("Pixel coordinates must be divisible by the VAE scale")
    latent_w = full_w // scale
    y0 = top // scale
    x0 = left // scale
    side = window // scale
    indices = []
    for y in range(y0, y0 + side):
        row = y * latent_w
        for x in range(x0, x0 + side):
            indices.append(row + x)
    return indices


def prompt_for(name: str) -> str:
    return (
        "Repaint this 3D map render as mndfstyle isometric pixel-art map of "
        f"{name}, Sweden, keeping every road, building and tree in place"
    )


def edge_pad(image, width: int, height: int):
    from PIL import Image

    nearest = getattr(getattr(Image, "Resampling", Image), "NEAREST")
    src_w, src_h = image.size
    if image.mode != "RGB":
        image = image.convert("RGB")
    if (src_w, src_h) == (width, height):
        return image
    canvas = Image.new("RGB", (width, height))
    canvas.paste(image, (0, 0))
    if width > src_w:
        strip = image.crop((src_w - 1, 0, src_w, src_h)).resize((width - src_w, src_h), nearest)
        canvas.paste(strip, (src_w, 0))
    if height > src_h:
        strip = canvas.crop((0, src_h - 1, width, src_h)).resize((width, height - src_h), nearest)
        canvas.paste(strip, (0, src_h))
    return canvas


def preflight_message() -> str | None:
    try:
        import torch
    except ImportError:
        return "PyTorch is not installed. Follow the README install for your OS, then run this preflight again."
    if not torch.cuda.is_available():
        if sys.platform == "darwin":
            return (
                "No NVIDIA CUDA GPU is visible. macOS can clone, install, check the SE3 price, and list districts. "
                "The 4-bit checkpoint is a bitsandbytes quant and the redraw runs on CUDA. "
                "Do not claim a district on this Mac."
            )
        return (
            "No NVIDIA CUDA GPU is visible. Install a CUDA build of PyTorch and check the driver. "
            "Do not claim a district until preflight passes."
        )
    try:
        import bitsandbytes  # noqa: F401
    except ImportError:
        return "bitsandbytes is not installed. Run pip install -r requirements.txt on this CUDA machine."
    return None


def _load_pipe():
    import torch
    from diffusers import QwenImage21Pipeline

    kwargs = {"low_cpu_mem_usage": True}
    try:
        pipe = QwenImage21Pipeline.from_pretrained(MODEL_ID, dtype=torch.bfloat16, **kwargs)
    except TypeError:
        pipe = QwenImage21Pipeline.from_pretrained(MODEL_ID, torch_dtype=torch.bfloat16, **kwargs)
    try:
        pipe.load_lora_weights(LORA_ID)
    except Exception as exc:
        raise SystemExit(
            f"Could not load LoRA {LORA_ID} on {MODEL_ID}. No PNG was written. "
            f"Keep this district and retry after fixing the environment. ({exc.__class__.__name__}: {exc})"
        ) from exc
    pipe.to("cuda")
    if hasattr(pipe.vae, "enable_tiling"):
        pipe.vae.enable_tiling()
    pipe.set_progress_bar_config(disable=True)
    return pipe


def _prepare_window(pipe, crop, prompt: str, device, dtype):
    import torch

    crop = crop.convert("RGBA")
    prompt_embeds, prompt_mask, image_pad_mask = pipe.encode_prompt(
        image=[crop],
        prompt=prompt,
        device=device,
    )
    scale = pipe.vae_scale_factor
    target_tokens = (WINDOW // scale) * (WINDOW // scale)
    if target_tokens % 4 != 0:
        raise RuntimeError("Window token count is not divisible by 4")
    # Same target-slot extension as QwenImage21Pipeline.__call__.
    image_pad_mask = torch.cat(
        [image_pad_mask, image_pad_mask.new_ones(image_pad_mask.shape[0], target_tokens // 4)],
        dim=1,
    )
    channels = pipe.transformer.config.in_channels
    tensor = pipe.image_processor.preprocess(crop, width=WINDOW, height=WINDOW).unsqueeze(2)
    dummy = torch.zeros(1, target_tokens, channels, device=device, dtype=dtype)
    _ignored, image_latents = pipe.prepare_latents(
        [tensor],
        1,
        channels,
        WINDOW,
        WINDOW,
        dtype,
        device,
        None,
        dummy,
    )
    side = WINDOW // scale
    return {
        "prompt_embeds": prompt_embeds.detach().to("cpu"),
        "prompt_mask": prompt_mask.detach().to("cpu"),
        "image_pad_mask": image_pad_mask.detach().to("cpu"),
        "image_latents": image_latents.detach().to("cpu"),
        "img_shapes": [[(1, side, side), (1, side, side)]],
        "tokens": target_tokens,
    }


def _denoise(pipe, full, views, contexts, full_h: int, full_w: int, device, dtype):
    import torch
    from diffusers.pipelines.qwenimage21.pipeline_qwenimage21 import calculate_shift, retrieve_timesteps

    scale = pipe.vae_scale_factor
    if WINDOW % scale or STEP % scale:
        raise RuntimeError(f"Window {WINDOW} and step {STEP} must be divisible by VAE scale {scale}")
    sigmas = pipe.config.sample_sigmas
    if not sigmas:
        raise SystemExit("The Turbo checkpoint has no sample_sigmas. Refusing to invent a schedule.")
    seq = (WINDOW // scale) * (WINDOW // scale)
    scheduler_config = pipe.scheduler.config
    mu = calculate_shift(
        seq,
        scheduler_config.get("base_image_seq_len", 256),
        scheduler_config.get("max_image_seq_len", 4096),
        scheduler_config.get("base_shift", 0.5),
        scheduler_config.get("max_shift", 1.15),
    )
    timesteps, count = retrieve_timesteps(
        pipe.scheduler,
        len(list(sigmas)),
        device,
        sigmas=list(sigmas),
        mu=mu,
    )
    pipe.scheduler.set_begin_index(0)
    index_cache = [window_token_indices(full_h, full_w, top, left, WINDOW, scale) for top, left in views]

    with torch.inference_mode():
        for step_index, timestep in enumerate(timesteps):
            acc = torch.zeros_like(full)
            weight = torch.zeros(full.shape[0], full.shape[1], 1, device=full.device, dtype=full.dtype)
            for window_index, context in enumerate(contexts):
                print(
                    f"step {step_index + 1}/{count} window {window_index + 1}/{len(views)}",
                    flush=True,
                )
                indices = torch.tensor(index_cache[window_index], device=full.device, dtype=torch.long)
                crop = full.index_select(1, indices).clone()
                image_latents = context["image_latents"].to(device=device, dtype=dtype)
                hidden = torch.cat([image_latents, crop], dim=1)
                mark = timestep.expand(crop.shape[0]).to(dtype=dtype)
                noise_pred = pipe.transformer(
                    hidden_states=hidden,
                    timestep=mark / 1000,
                    encoder_hidden_states=context["prompt_embeds"].to(device=device, dtype=dtype),
                    encoder_hidden_states_mask=context["prompt_mask"].to(device=device),
                    img_shapes=context["img_shapes"],
                    img_mask=context["image_pad_mask"].to(device=device),
                    attention_kwargs={},
                    kv_cache=None,
                    kv_cache_mode=None,
                    return_dict=False,
                )[0]
                noise_pred = noise_pred[:, -crop.shape[1] :].to(dtype=full.dtype)
                acc.index_add_(1, indices, noise_pred)
                weight.index_add_(
                    1,
                    indices,
                    torch.ones(crop.shape[0], crop.shape[1], 1, device=full.device, dtype=full.dtype),
                )
                del hidden, noise_pred, image_latents, crop
            if int((weight < 1).sum().item()) != 0:
                raise RuntimeError("The window grid missed part of the tile. No PNG was written.")
            full = pipe.scheduler.step(acc / weight, timestep, full, return_dict=False)[0]
    return full


def _decode(pipe, latents, height: int, width: int):
    import torch

    latents = pipe._unpack_latents(latents, height, width, pipe.vae_scale_factor)
    latents = latents.to(pipe.vae.dtype)
    z_dim = pipe.vae.config.z_dim
    latents_mean = (
        torch.tensor(pipe.vae.config.latents_mean).view(1, z_dim, 1, 1, 1).to(latents.device, latents.dtype)
    )
    latents_std = torch.tensor(pipe.vae.config.latents_std).view(1, z_dim, 1, 1, 1).to(latents.device, latents.dtype)
    latents = latents * latents_std + latents_mean
    decoded = pipe.vae.decode(latents, return_dict=False)[0][:, :, 0]
    return pipe.image_processor.postprocess(decoded, output_type="pil")[0]


def redraw(root: Path, slug: str, input_path: Path | None, seed: int | None, run_anyway: bool) -> int:
    import se3_price

    districts = load_districts(root)
    if slug not in districts:
        raise SystemExit(f"Unknown district {slug!r}. Run: python scripts/claim_district.py --list")
    if not (root / "claims" / f"{slug}.md").exists():
        raise SystemExit(f"No claim for {slug}. Claim it, push the claim, then redraw. Do not redraw an unclaimed district.")
    dest = root / "results" / f"{slug}.png"
    if dest.exists():
        raise SystemExit(f"results/{slug}.png already exists. Do not overwrite a finished tile.")
    source = input_path or (root / "inputs" / f"{slug}.png")
    if not source.is_file():
        raise SystemExit(
            f"Missing {source}. Put your own source render there. "
            "This repository does not ship source imagery. Do not commit the render."
        )
    if not run_anyway:
        code = se3_price.main()
        if code != 0:
            raise SystemExit(code)
    message = preflight_message()
    if message:
        raise SystemExit(message)

    from PIL import Image

    image = Image.open(source).convert("RGB")
    src_w, src_h = image.size
    if max(src_w, src_h) > MAX_SIDE:
        raise SystemExit(f"Input is {src_w}x{src_h}. One district tile must be at most {MAX_SIDE}px on the long side.")
    pad_w, pad_h = pad_size(src_w), pad_size(src_h)
    padded = edge_pad(image, pad_w, pad_h)
    views = get_views(pad_w, pad_h)
    name = districts[slug]["name"]
    prompt = prompt_for(name)
    print(f"district: {slug}", flush=True)
    print(f"windows: {len(views)}", flush=True)
    print(f"window: {WINDOW}", flush=True)
    print(f"step: {STEP}", flush=True)
    print(f"model: {MODEL_ID}", flush=True)
    print(f"lora: {LORA_ID}", flush=True)
    print("cache: the default Hugging Face cache on this machine", flush=True)

    import torch

    pipe = _load_pipe()
    device = torch.device("cuda")
    dtype = torch.bfloat16
    generator = torch.Generator(device="cpu")
    if seed is not None:
        generator.manual_seed(seed)
    channels = pipe.transformer.config.in_channels
    full, _unused = pipe.prepare_latents(
        None,
        1,
        channels,
        pad_h,
        pad_w,
        dtype,
        device,
        generator,
        None,
    )
    contexts = []
    for index, (top, left) in enumerate(views):
        print(f"encode window {index + 1}/{len(views)}", flush=True)
        crop = padded.crop((left, top, left + WINDOW, top + WINDOW))
        contexts.append(_prepare_window(pipe, crop, prompt, device, dtype))
    full = _denoise(pipe, full, views, contexts, pad_h, pad_w, device, dtype)
    finished = _decode(pipe, full, pad_h, pad_w).convert("RGB").crop((0, 0, src_w, src_h))
    dest.parent.mkdir(parents=True, exist_ok=True)
    partial = dest.with_name(dest.name + ".partial")
    finished.save(partial, format="PNG")
    data = partial.read_bytes()
    if not data.startswith(PNG_MAGIC):
        partial.unlink(missing_ok=True)
        raise SystemExit("The redraw did not produce a PNG. Nothing was published.")
    partial.replace(dest)
    print(f"wrote: results/{slug}.png", flush=True)
    print(f"Next: python scripts/publish_png.py --district {slug}", flush=True)
    print("That commit contains only the PNG.", flush=True)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Redraw one claimed district tile.")
    parser.add_argument("--district", help="District slug you have already claimed")
    parser.add_argument("--input", type=Path, help="Local source render. Default: inputs/<district>.png")
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--preflight", action="store_true", help="Check CUDA and bitsandbytes. Does not download weights.")
    parser.add_argument(
        "--run-anyway",
        action="store_true",
        help="Redraw even when the SE3 price is high. An agent told to WAIT must not pass this.",
    )
    args = parser.parse_args(argv)
    if args.preflight:
        message = preflight_message()
        if message:
            raise SystemExit(message)
        import torch

        print(f"preflight: ok ({torch.cuda.get_device_name(0)})")
        return 0
    if not args.district:
        raise SystemExit("Pass --district <slug>, or --preflight.")
    return redraw(repo_root(), args.district, args.input, args.seed, args.run_anyway)


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass
    sys.exit(main())

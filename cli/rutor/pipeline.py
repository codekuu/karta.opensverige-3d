"""The pinned redraw pipeline. An empty pin is reported and does not block."""

from __future__ import annotations

PIPELINE = "2026.11-a"
MODEL_ID = "addlabsviral/Qwen-Image-2.1-Turbo-4bit"
LORA_ID = "handful/mndf-pixel-qwen-image-2.1"
WINDOW = 1024
STEP = 512

PINS: dict[str, str] = {
    "model_revision": "",
    "lora_revision": "",
    "diffusers_version": "",
}


def prompt_for(city_name: str) -> str:
    return (
        "Repaint this 3D map render as mndfstyle isometric pixel-art map of "
        f"{city_name}, Sweden, keeping every road, building and tree in place"
    )


def check_pins(found: dict[str, str]) -> list[str]:
    mismatched: list[str] = []
    for name, pin in PINS.items():
        # A pin is only checked against a value that was actually found.
        if pin and name in found and found[name] != pin:
            mismatched.append(name)
    return mismatched

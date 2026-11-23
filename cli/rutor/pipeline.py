"""The pinned redraw pipeline. An empty pin is reported and does not block.

The model and the LoRA are named by commit, never by branch: a volunteer's machine loads these
files, and a moving branch would let whoever owns the Hugging Face repository replace them. To
move a pin, read the new commit's files first, then change the hash in a pull request.
"""

from __future__ import annotations

PIPELINE = "2026.11-a"
MODEL_ID = "addlabsviral/Qwen-Image-2.1-Turbo-4bit"
LORA_ID = "handful/mndf-pixel-qwen-image-2.1"
WINDOW = 1024
STEP = 512

PINS: dict[str, str] = {
    "model_revision": "a5ba55677958edac3444ee8633dbfe9b9ffc4b50",
    "lora_revision": "876d03a974b3f3c1ff8af9b5d86490e87964f116",
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

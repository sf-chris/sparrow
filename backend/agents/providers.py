"""Which provider a model runs on.

Sparrow keeps one message format for every session and translates it at the
call boundary: Assistant models go to Provider, ``model-``/``o`` models to the
AltProvider API (altprovider_loop) and ``glm-`` models to Z.ai's Chat
Completions API (glm_loop).
"""

from __future__ import annotations

PROVIDER, ALTPROVIDER, GLM = "provider", "altprovider", "glm"
NAMES = {PROVIDER: "Provider", ALTPROVIDER: "AltProvider", GLM: "Z.ai"}

ALTPROVIDER_PREFIXES = ("model-", "o1", "o3", "o4")
GLM_PREFIXES = ("glm-",)
# GLM models that read images (Z.ai's vision request lists only the flash family).
GLM_VISION = ("glm-5.3-flash", "glm-5.3-flashx")


def provider_for(model: str) -> str:
    name = (model or "").lower()
    if name.startswith(ALTPROVIDER_PREFIXES):
        return ALTPROVIDER
    if name.startswith(GLM_PREFIXES):
        return GLM
    return PROVIDER


def is_glm_model(model: str) -> bool:
    return provider_for(model) == GLM


def reads_images(model: str) -> bool:
    """Whether Sparrow can send this model a picture (Blu-ray/DVD subtitles)."""
    name = (model or "").lower()
    return provider_for(name) == ALTPROVIDER or name in GLM_VISION


"""Provider adapters and bridges for translation backends."""

from translator.providers.antigravity_bridge import AntigravityBridge, extract_json_object
from translator.providers.opencode import OpenCodeError, model_for as opencode_model_for, run_prompt as run_opencode_prompt
from translator.providers.translator import ProviderTranslator

__all__ = [
    "AntigravityBridge",
    "OpenCodeError",
    "ProviderTranslator",
    "extract_json_object",
    "opencode_model_for",
    "run_opencode_prompt",
]

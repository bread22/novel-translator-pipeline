"""Provider adapters for translation and review backends."""

from translator.providers.antigravity import AntigravityProvider
from translator.providers.base import BaseProvider, extract_json_object
from translator.providers.codex import CodexProvider
from translator.providers.opencode import OpenCodeError, OpenCodeProvider, model_for as opencode_model_for, run_prompt as run_opencode_prompt
from translator.providers.openai_provider import OpenAIProvider
from translator.providers.registry import get_provider
from translator.providers.translator import ProviderTranslator

__all__ = [
    "AntigravityProvider",
    "BaseProvider",
    "CodexProvider",
    "OpenAIProvider",
    "OpenCodeError",
    "OpenCodeProvider",
    "ProviderTranslator",
    "extract_json_object",
    "get_provider",
    "opencode_model_for",
    "run_opencode_prompt",
]

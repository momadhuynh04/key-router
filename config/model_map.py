import json
import os
import re
from typing import Dict, Tuple

CLAUDE_FAMILY_KEY = "claude"

# Legacy per-tier keys, in priority order used when consolidating them into the
# single 'claude' route. 'sonnet' first because it is Claude Code's default.
LEGACY_CLAUDE_TIER_KEYS = ("sonnet", "opus", "haiku", "fable")


class ModelMapper:
    def __init__(self, config_path: str = None):
        if config_path is None:
            base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            config_path = os.path.join(base_dir, "config.json")
        self.config_path = config_path
        self.mappings: Dict[str, str] = {}
        self.load_mappings()
        
    def load_mappings(self):
        if os.path.exists(self.config_path):
            with open(self.config_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                self.mappings = data.get("model_mappings", {})
        self._consolidate_claude_routes()

    def _consolidate_claude_routes(self):
        """Collapse legacy per-tier keys (opus/sonnet/haiku) into one 'claude' key."""
        if CLAUDE_FAMILY_KEY in self.mappings:
            return
        legacy = [key for key in LEGACY_CLAUDE_TIER_KEYS if key in self.mappings]
        if not legacy:
            return
        source = legacy[0]
        target = self.mappings[source]
        self.mappings[CLAUDE_FAMILY_KEY] = target
        for key in legacy:
            del self.mappings[key]
        print(f"[⚡] Consolidated Claude routes {legacy} → "
              f"'{CLAUDE_FAMILY_KEY}' = '{target}'")
        self.save_mappings()

    def save_mappings(self):
        data = {}
        if os.path.exists(self.config_path):
            with open(self.config_path, "r", encoding="utf-8") as f:
                try:
                    data = json.load(f)
                except json.JSONDecodeError:
                    pass
        
        data["model_mappings"] = self.mappings
        with open(self.config_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

    def set_mapping(self, source_model: str, target: str):
        self.mappings[source_model] = target
        self.save_mappings()

    def get_all(self) -> Dict[str, str]:
        return self.mappings
                
    # Keyword tiers to match against — legacy per-tier keys still win when
    # present, so existing configs keep working. OpenAI-family models
    # (gpt-*/codex/o3/o4) are NOT tier-matched on purpose: they resolve via
    # FAMILY_FALLBACKS so one 'codex' key covers the whole family, and only
    # an exact per-model key (e.g. 'gpt-5.5') overrides a single model.
    KEYWORD_TIERS = ["opus", "sonnet", "haiku", "fable"]

    # Family fallback: ANY model from these families routes through a single
    # canonical key, so clients switching models (Claude Code: opus/sonnet/
    # haiku/claude-*, Codex: gpt-5.6-sol/gpt-5.5/o4-mini…) never break routing
    # when upstream renames models — one 'claude' and one 'codex' key cover
    # each whole family.
    FAMILY_FALLBACKS = [
        (r"claude|anthropic|\b(opus|sonnet|haiku|fable)\b", CLAUDE_FAMILY_KEY),
        (r"\b(gpt|codex|o[134])\b", "codex"),
    ]

    def resolve(self, requested_model: str) -> Tuple[str, str]:
        """
        Resolves the requested model string to a provider and target model.
        Strategy:
          1. Exact match against mapping keys.
          2. Keyword match: if requested model contains 'opus'/'sonnet'/'haiku',
             scan existing mapping keys for the same keyword and use that value.
          3. Family fallback: Claude models (claude-*, opus/sonnet/haiku/fable)
             route through the single 'claude' key, and OpenAI-family models
             (gpt-*/codex/o3/o4) through the single 'codex' key.
          4. Raise ValueError with a helpful message.
        """
        # 1. Exact match
        target = self.mappings.get(requested_model)

        # 2. Keyword fallback — scan existing keys for matching keyword
        if not target:
            model_lower = requested_model.lower()
            for keyword in self.KEYWORD_TIERS:
                if keyword in model_lower:
                    # Find first existing key that also contains this keyword
                    for key, val in self.mappings.items():
                        if keyword in key.lower():
                            target = val
                            print(f"[⚡] No exact match for '{requested_model}', "
                                  f"keyword '{keyword}' matched key '{key}' → '{val}'")
                            break
                if target:
                    break

        # 3. Family fallback — one key covers an entire model family
        if not target:
            model_lower = requested_model.lower()
            for pattern, fallback_key in self.FAMILY_FALLBACKS:
                if re.search(pattern, model_lower) and fallback_key in self.mappings:
                    target = self.mappings[fallback_key]
                    print(f"[⚡] No exact match for '{requested_model}', "
                          f"family fallback → key '{fallback_key}' → '{target}'")
                    break

        if not target:
            raise ValueError(
                f"No mapping found for model '{requested_model}'. "
                f"Please add it via WebUI (http://127.0.0.1:8082) or config.json."
            )

        parts = target.split("/", 1)
        if len(parts) != 2:
            raise ValueError(
                f"Invalid mapping format '{target}' for '{requested_model}'. "
                f"Expected 'provider/model_name'."
            )

        return parts[0], parts[1]

model_mapper = ModelMapper()

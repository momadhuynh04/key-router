import pytest
import json
import os
from config.model_map import ModelMapper

@pytest.fixture
def temp_config(tmp_path):
    # Use a temporary file for config.json testing
    config_file = tmp_path / "config.json"
    return str(config_file)

def test_model_mapper_load_save(temp_config):
    mapper = ModelMapper(config_path=temp_config)
    
    # Initially empty
    assert mapper.get_all() == {}
    
    # Add a mapping
    mapper.set_mapping("claude-3-opus", "openrouter/anthropic/claude-3-opus")
    
    # Should save to file
    assert os.path.exists(temp_config)
    with open(temp_config, "r") as f:
        data = json.load(f)
        assert data["model_mappings"]["claude-3-opus"] == "openrouter/anthropic/claude-3-opus"
        
    # Reload in a new instance
    mapper2 = ModelMapper(config_path=temp_config)
    assert mapper2.get_all() == {"claude-3-opus": "openrouter/anthropic/claude-3-opus"}

def test_model_mapper_resolve_exact(temp_config):
    mapper = ModelMapper(config_path=temp_config)
    mapper.set_mapping("claude-3-5-sonnet", "deepseekplatform/deepseek-chat")
    
    provider, target = mapper.resolve("claude-3-5-sonnet")
    assert provider == "deepseekplatform"
    assert target == "deepseek-chat"

def test_model_mapper_resolve_keyword_fallback(temp_config):
    mapper = ModelMapper(config_path=temp_config)
    mapper.set_mapping("opus", "openrouter/qwen/qwen-max")
    
    # Requesting 'claude-3-opus-20240229' should fallback to the 'opus' mapping
    provider, target = mapper.resolve("claude-3-opus-20240229")
    assert provider == "openrouter"
    assert target == "qwen/qwen-max"

def test_model_mapper_resolve_not_found(temp_config):
    mapper = ModelMapper(config_path=temp_config)
    
    with pytest.raises(ValueError, match="No mapping found"):
        mapper.resolve("unknown-model")

def test_model_mapper_resolve_invalid_format(temp_config):
    mapper = ModelMapper(config_path=temp_config)
    mapper.set_mapping("bad-model", "openrouter_no_slash")
    
    with pytest.raises(ValueError, match="Invalid mapping format"):
        mapper.resolve("bad-model")

def test_model_mapper_family_fallback_gpt_models(temp_config):
    """Any gpt-*/o3/o4 model routes through the single 'codex' key."""
    mapper = ModelMapper(config_path=temp_config)
    mapper.set_mapping("codex", "deepseekplatform/deepseek-v4-flash")

    for model in ["gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.5", "gpt-5.2", "o3", "o4-mini", "codex"]:
        provider, target = mapper.resolve(model)
        assert (provider, target) == ("deepseekplatform", "deepseek-v4-flash"), model

def test_model_mapper_family_fallback_exact_match_wins(temp_config):
    """An explicit per-model key takes priority over the family fallback."""
    mapper = ModelMapper(config_path=temp_config)
    mapper.set_mapping("codex", "deepseekplatform/default")
    mapper.set_mapping("gpt-5.5", "openrouter/special-model")

    provider, target = mapper.resolve("gpt-5.5")
    assert (provider, target) == ("openrouter", "special-model")

    # Other family members still fall back
    provider, target = mapper.resolve("gpt-5.6-sol")
    assert (provider, target) == ("deepseekplatform", "default")

def test_model_mapper_family_fallback_requires_codex_key(temp_config):
    """Without a 'codex' key, unknown gpt models still raise a helpful error."""
    mapper = ModelMapper(config_path=temp_config)
    mapper.set_mapping("opus", "openrouter/qwen/qwen-max")

    with pytest.raises(ValueError, match="No mapping found"):
        mapper.resolve("gpt-5.6-sol")

def test_model_mapper_family_fallback_claude_models(temp_config):
    """Every Claude Code model routes through the single 'claude' key."""
    mapper = ModelMapper(config_path=temp_config)
    mapper.set_mapping("claude", "openrouter/anthropic/claude-sonnet-4-5")

    for model in [
        "claude-sonnet-4-5-20250929",
        "claude-opus-4-1-20250805",
        "claude-3-5-haiku-20241022",
        "claude-5-fable-preview",
        "sonnet", "opus", "haiku", "fable",
    ]:
        provider, target = mapper.resolve(model)
        assert (provider, target) == ("openrouter", "anthropic/claude-sonnet-4-5"), model

def test_model_mapper_claude_family_exact_match_wins(temp_config):
    """An explicit per-model key still overrides the family route."""
    mapper = ModelMapper(config_path=temp_config)
    mapper.set_mapping("claude", "openrouter/anthropic/claude-sonnet-4-5")
    mapper.set_mapping("claude-opus-4-1-20250805", "openrouter/anthropic/claude-opus-4-1")

    provider, target = mapper.resolve("claude-opus-4-1-20250805")
    assert (provider, target) == ("openrouter", "anthropic/claude-opus-4-1")

    provider, target = mapper.resolve("claude-4-5-haiku")
    assert (provider, target) == ("openrouter", "anthropic/claude-sonnet-4-5")

def test_model_mapper_legacy_tier_keys_still_win(temp_config):
    """Existing opus/sonnet/haiku keys keep working alongside 'claude'."""
    mapper = ModelMapper(config_path=temp_config)
    mapper.set_mapping("claude", "openrouter/anthropic/claude-sonnet-4-5")
    mapper.set_mapping("opus", "openrouter/anthropic/claude-opus-4-1")

    provider, target = mapper.resolve("claude-opus-4-1")
    assert (provider, target) == ("openrouter", "anthropic/claude-opus-4-1")

def test_model_mapper_consolidates_legacy_claude_tiers(temp_config):
    """On load, per-tier keys collapse into one 'claude' key (sonnet preferred)."""
    with open(temp_config, "w", encoding="utf-8") as f:
        json.dump({"model_mappings": {
            "opus": "openrouter/anthropic/claude-opus-4-1",
            "sonnet": "openrouter/anthropic/claude-sonnet-4-5",
            "haiku": "openrouter/anthropic/claude-haiku-4-5",
            "codex": "deepseekplatform/deepseek-v4-flash",
        }}, f)

    mapper = ModelMapper(config_path=temp_config)
    assert mapper.get_all() == {
        "claude": "openrouter/anthropic/claude-sonnet-4-5",
        "codex": "deepseekplatform/deepseek-v4-flash",
    }

    with open(temp_config, "r", encoding="utf-8") as f:
        saved = json.load(f)["model_mappings"]
    assert saved == mapper.get_all()

def test_model_mapper_consolidation_keeps_existing_claude_route(temp_config):
    """An already-configured 'claude' key is never overwritten by legacy tiers."""
    with open(temp_config, "w", encoding="utf-8") as f:
        json.dump({"model_mappings": {
            "claude": "openrouter/anthropic/claude-opus-4-1",
            "sonnet": "openrouter/anthropic/claude-sonnet-4-5",
        }}, f)

    mapper = ModelMapper(config_path=temp_config)
    assert mapper.get_all() == {
        "claude": "openrouter/anthropic/claude-opus-4-1",
        "sonnet": "openrouter/anthropic/claude-sonnet-4-5",
    }
    provider, target = mapper.resolve("claude-sonnet-4-5")
    assert (provider, target) == ("openrouter", "anthropic/claude-sonnet-4-5")

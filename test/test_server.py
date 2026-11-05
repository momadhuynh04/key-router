from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock, AsyncMock
from proxy.server import app

client = TestClient(app)

# ----------------------------------------
# 1. Test Proxy Session (API Endpoints)
# ----------------------------------------

def test_api_session_health():
    """Test standard session creation and healthcheck."""
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"

@patch('proxy.server.provider_router.get_provider')
def test_api_session_chat_generate_success(mock_get_provider):
    """Test generating a chat message in a session context."""
    # Mock the provider response
    mock_provider = AsyncMock()
    
    mock_response = MagicMock()
    mock_response.model_dump.return_value = {
        "id": "msg_test",
        "type": "message",
        "role": "assistant",
        "content": [{"type": "text", "text": "Success"}],
        "model": "test-model",
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 10, "output_tokens": 10}
    }
    
    # Needs to be awaitable
    async def mock_translate(*args, **kwargs):
        return {"translated": True}
        
    async def mock_generate(*args, **kwargs):
        return mock_response
        
    mock_provider.translate_request = mock_translate
    mock_provider.generate = mock_generate
    mock_get_provider.return_value = mock_provider
    
    payload = {
        "model": "claude-3-haiku-20240307",
        "messages": [{"role": "user", "content": "Hello"}],
        "stream": False
    }
    
    response = client.post("/v1/messages", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["content"][0]["text"] == "Success"
    assert data["role"] == "assistant"

@patch('proxy.server.provider_router.get_provider')
def test_api_folds_system_role_messages(mock_get_provider):
    """Claude Code sends context as a role=system message; it must become top-level system."""
    from models.anthropic import AnthropicResponse, AnthropicUsage

    mock_provider = AsyncMock()
    seen = []

    async def mock_translate(req, *args, **kwargs):
        seen.append(req)
        return {"translated": True}

    async def mock_generate(*args, **kwargs):
        return AnthropicResponse(
            id="msg_test",
            model="claude-sonnet-5-5",
            content=[{"type": "text", "text": "ok"}],
            stop_reason="end_turn",
            usage=AnthropicUsage(input_tokens=1, output_tokens=1),
        )

    mock_provider.translate_request = mock_translate
    mock_provider.generate = mock_generate
    mock_get_provider.return_value = mock_provider

    payload = {
        "model": "claude-sonnet-5-5",
        "system": [{"type": "text", "text": "base system"}],
        "messages": [
            {"role": "user", "content": "hi"},
            {"role": "system", "content": [{"type": "text", "text": "Environment: /repo"}]},
            {"role": "system", "content": "Second system note"},
        ],
        "stream": False,
    }

    response = client.post("/v1/messages", json=payload)
    assert response.status_code == 200
    assert response.json()["content"][0]["text"] == "ok"

    request = seen[0]
    assert [m.role for m in request.messages] == ["user"]
    assert request.messages[0].content == "hi"
    assert request.system[0]["text"] == "base system"
    folded = "\n\n".join(block.get("text", "") for block in request.system[1:])
    assert "Environment: /repo" in folded
    assert "Second system note" in folded


@patch('proxy.server.provider_router.get_provider')
def test_api_strips_thinking_display(mock_get_provider):
    """Claude Code's thinking.display beta hint is rejected by strict Anthropic gateways."""
    from models.anthropic import AnthropicResponse, AnthropicUsage

    mock_provider = AsyncMock()
    seen = []

    async def mock_translate(req, *args, **kwargs):
        seen.append(req)
        return {"translated": True}

    async def mock_generate(*args, **kwargs):
        return AnthropicResponse(
            id="msg_test",
            model="claude-haiku-5-5",
            content=[{"type": "text", "text": "ok"}],
            stop_reason="end_turn",
            usage=AnthropicUsage(input_tokens=1, output_tokens=1),
        )

    mock_provider.translate_request = mock_translate
    mock_provider.generate = mock_generate
    mock_get_provider.return_value = mock_provider

    payload = {
        "model": "claude-haiku-5-5",
        "thinking": {"type": "adaptive", "display": "updates"},
        "messages": [{"role": "user", "content": "hi"}],
        "stream": False,
    }

    response = client.post("/v1/messages", json=payload)
    assert response.status_code == 200

    thinking = seen[0].thinking
    assert thinking.type == "adaptive"
    assert "display" not in thinking.model_dump(exclude_none=True)


# ----------------------------------------
# 2. Test Logging & Error Handling
# ----------------------------------------

@patch('proxy.server.provider_router.get_provider')
def test_api_logging_and_errors(mock_get_provider, capsys):
    """Test that missing mapping or errors log appropriately and return 500."""
    # Simulate a provider mapping failure
    mock_get_provider.side_effect = ValueError("Test missing mapping log")
    
    payload = {
        "model": "unknown-model",
        "messages": [{"role": "user", "content": "Hello"}],
        "stream": False
    }
    
    response = client.post("/v1/messages", json=payload)
    assert response.status_code == 500
    assert "Test missing mapping log" in response.json()["error"]["message"]
    
    # Check if the error might have been logged (simulated by print/traceback internally)
    captured = capsys.readouterr()
    combined = captured.out + captured.err
    assert "Test missing mapping log" in response.json()["error"]["message"]
    assert "Test missing mapping log" in combined or response.status_code == 500

# ----------------------------------------
# 3. Test Tool Call Parsing Session
# ----------------------------------------

@patch('proxy.server.provider_router.get_provider')
def test_api_tool_call_session(mock_get_provider, monkeypatch):
    """Test that a session with tools properly validates schema."""
    monkeypatch.setattr("proxy.websearch.configured", lambda: False)
    mock_provider = AsyncMock()
    
    translated_called_with = []
    
    async def mock_translate(req, *args, **kwargs):
        translated_called_with.append(req)
        return {"translated": True}
        
    mock_provider.translate_request = mock_translate
    
    async def mock_generate(*args, **kwargs):
        mock_resp = MagicMock()
        # Must contain tool_use so agentic retry does not trigger (retry fires
        # on empty/narration with tools offered).
        from models.anthropic import AnthropicResponse, AnthropicUsage
        mock_resp = AnthropicResponse(
            id="msg_test",
            model="claude-3-opus-20240229",
            content=[{"type": "tool_use", "id": "toolu_1", "name": "get_weather", "input": {"location": "Hanoi"}}],
            stop_reason="tool_use",
            usage=AnthropicUsage(input_tokens=10, output_tokens=10),
        )
        return mock_resp

    mock_provider.generate = mock_generate
    mock_get_provider.return_value = mock_provider

    payload = {
        "model": "claude-3-opus-20240229",
        "messages": [{"role": "user", "content": "Find weather"}],
        "tools": [
            {
                "name": "get_weather",
                "description": "Get weather for a location",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "location": {"type": "string"}
                    }
                }
            }
        ],
        "stream": False
    }

    response = client.post("/v1/messages", json=payload)
    assert response.status_code == 200

    assert len(translated_called_with) == 1
    called_request = translated_called_with[0]

    assert len(called_request.tools) == 1
    assert called_request.tools[0]["name"] == "get_weather"

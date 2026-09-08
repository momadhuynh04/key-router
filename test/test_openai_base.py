import pytest
import json
from unittest.mock import patch, MagicMock
from provider.openai_base import (
    _anthropic_content_to_openai,
    _openai_tool_calls_to_anthropic,
    OpenAIBaseProvider
)
from models.anthropic import AnthropicRequest, Message, Thinking

# ----------------------------------------
# 1. Test Content Conversion Methods
# ----------------------------------------

def test_anthropic_content_to_openai_text():
    content = [{"type": "text", "text": "Hello world"}]
    result = _anthropic_content_to_openai("user", content)
    assert result == [{"role": "user", "content": "Hello world"}]

def test_anthropic_content_to_openai_tool_use():
    content = [{
        "type": "tool_use",
        "id": "toolu_abc123",
        "name": "get_weather",
        "input": {"location": "London"}
    }]
    result = _anthropic_content_to_openai("assistant", content)
    # The output should have stripped "toolu_" prefix
    assert len(result) == 1
    assert result[0]["role"] == "assistant"
    assert len(result[0]["tool_calls"]) == 1
    
    tc = result[0]["tool_calls"][0]
    assert tc["id"] == "abc123"
    assert tc["function"]["name"] == "get_weather"
    assert json.loads(tc["function"]["arguments"]) == {"location": "London"}

def test_anthropic_content_to_openai_tool_result():
    content = [{
        "type": "tool_result",
        "tool_use_id": "toolu_xyz789",
        "content": [{"type": "text", "text": "Sunny, 20C"}]
    }]
    result = _anthropic_content_to_openai("user", content)
    assert len(result) == 1
    assert result[0]["role"] == "tool"
    # The output should have stripped "toolu_" prefix
    assert result[0]["tool_call_id"] == "xyz789"
    assert result[0]["content"] == "Sunny, 20C"

def test_openai_tool_calls_to_anthropic():
    tool_calls = [{
        "id": "call_def456",
        "function": {
            "name": "search",
            "arguments": '{"query": "OpenAI"}'
        }
    }]
    result = _openai_tool_calls_to_anthropic(tool_calls)
    assert len(result) == 1
    assert result[0]["type"] == "tool_use"
    # Ensure it prefixed with toolu_
    assert result[0]["id"] == "toolu_call_def456"
    assert result[0]["name"] == "search"
    assert result[0]["input"] == {"query": "OpenAI"}

def test_openai_tool_calls_to_anthropic_already_prefixed():
    tool_calls = [{
        "id": "toolu_1234",
        "function": {
            "name": "ping",
            "arguments": '{}'
        }
    }]
    result = _openai_tool_calls_to_anthropic(tool_calls)
    assert len(result) == 1
    # Ensure it didn't prefix twice
    assert result[0]["id"] == "toolu_1234"

# ----------------------------------------
# 2. Test Provider Translation
# ----------------------------------------

@pytest.mark.asyncio
async def test_translate_request_happy_path():
    provider = OpenAIBaseProvider(base_url="http://test", api_key="test", target_model="test-model")
    
    # Complex system prompt (array format)
    req = AnthropicRequest(
        model="claude-3-opus",
        messages=[Message(role="user", content="Hi")],
        system=[{"type": "text", "text": "You are helpful", "cache_control": {"type": "ephemeral"}}],
        temperature=0.5,
        max_tokens=1000,
        stop_sequences=["STOP"],
        tools=[{
            "name": "say_hello",
            "description": "Says hello",
            "input_schema": {"type": "object", "properties": {}}
        }]
    )
    
    body = await provider.translate_request(req)
    
    assert body["model"] == "test-model"
    assert body["temperature"] == 0.5
    assert body["max_tokens"] == 1000
    
    # System message translation
    assert len(body["messages"]) == 2
    assert body["messages"][0]["role"] == "system"
    assert body["messages"][0]["content"] == "You are helpful"
    assert body["messages"][1]["role"] == "user"
    assert body["messages"][1]["content"] == "Hi"
    
    # Tool translation
    assert len(body["tools"]) == 1
    assert body["tools"][0]["function"]["name"] == "say_hello"

# ----------------------------------------
# 3. Test Generate (Non-Streaming)
# ----------------------------------------

@pytest.mark.asyncio
@patch('httpx.AsyncClient.post')
async def test_generate_happy_path(mock_post):
    # Mock normal response
    mock_response = MagicMock()
    mock_response.raise_for_status.return_value = None
    mock_response.json.return_value = {
        "id": "chatcmpl-123",
        "choices": [{
            "message": {
                "role": "assistant",
                "content": "I am here"
            },
            "finish_reason": "stop"
        }],
        "usage": {
            "prompt_tokens": 10,
            "completion_tokens": 5
        }
    }
    mock_post.return_value = mock_response

    provider = OpenAIBaseProvider(base_url="http://test", api_key="test", target_model="test")
    resp = await provider.generate({"messages": []})
    
    assert resp.type == "message"
    assert resp.role == "assistant"
    assert resp.stop_reason == "end_turn"
    assert len(resp.content) == 1
    assert resp.content[0]["type"] == "text"
    assert resp.content[0]["text"] == "I am here"
    assert resp.usage.input_tokens == 10
    assert resp.usage.output_tokens == 5

@pytest.mark.asyncio
@patch('httpx.AsyncClient.post')
async def test_generate_tool_call(mock_post):
    # Mock response with tool calls
    mock_response = MagicMock()
    mock_response.raise_for_status.return_value = None
    mock_response.json.return_value = {
        "choices": [{
            "message": {
                "role": "assistant",
                "content": None,
                "tool_calls": [{
                    "id": "call_mocked",
                    "function": {"name": "test_tool", "arguments": '{"k":"v"}'}
                }]
            },
            "finish_reason": "tool_calls"
        }],
        "usage": {}
    }
    mock_post.return_value = mock_response

    provider = OpenAIBaseProvider(base_url="http://test", api_key="test", target_model="test")
    resp = await provider.generate({"messages": []})
    
    assert resp.stop_reason == "tool_use"
    assert len(resp.content) == 1
    assert resp.content[0]["type"] == "tool_use"
    assert resp.content[0]["id"] == "toolu_call_mocked"
    assert resp.content[0]["name"] == "test_tool"
    assert resp.content[0]["input"] == {"k": "v"}

@pytest.mark.asyncio
@patch('httpx.AsyncClient.post')
async def test_generate_api_error(mock_post):
    import httpx
    # Mock HTTP error
    mock_response = MagicMock()
    mock_response.status_code = 400
    mock_response.text = '{"error": "bad request"}'
    
    error = httpx.HTTPStatusError("Bad Request", request=MagicMock(), response=mock_response)
    mock_response.raise_for_status.side_effect = error
    mock_post.return_value = mock_response

    provider = OpenAIBaseProvider(base_url="http://test", api_key="test", target_model="test")
    
    with pytest.raises(httpx.HTTPStatusError):
        await provider.generate({"messages": []})

# ----------------------------------------
# 9. Reasoning Effort Mapping (thinking → reasoning_effort)
# ----------------------------------------

@pytest.mark.asyncio
async def test_thinking_to_reasoning_effort_xhigh():
    """budget_tokens > 16000 → xhigh"""
    provider = OpenAIBaseProvider(base_url="http://test", api_key="test", target_model="test")
    req = AnthropicRequest(
        model="claude-sonnet-4-5",
        messages=[Message(role="user", content="test")],
        thinking=Thinking(type="enabled", budget_tokens=20000)
    )
    body = await provider.translate_request(req)
    assert "reasoning_effort" in body
    assert body["reasoning_effort"] == "xhigh"


@pytest.mark.asyncio
async def test_thinking_to_reasoning_effort_high():
    """budget_tokens 8001–16000 → high"""
    provider = OpenAIBaseProvider(base_url="http://test", api_key="test", target_model="test")
    req = AnthropicRequest(
        model="claude-sonnet-4-5",
        messages=[Message(role="user", content="test")],
        thinking=Thinking(type="enabled", budget_tokens=10000)
    )
    body = await provider.translate_request(req)
    assert body["reasoning_effort"] == "high"


@pytest.mark.asyncio
async def test_thinking_to_reasoning_effort_medium():
    """budget_tokens 2001–8000 → medium"""
    provider = OpenAIBaseProvider(base_url="http://test", api_key="test", target_model="test")
    req = AnthropicRequest(
        model="claude-sonnet-4-5",
        messages=[Message(role="user", content="test")],
        thinking=Thinking(type="enabled", budget_tokens=4000)
    )
    body = await provider.translate_request(req)
    assert body["reasoning_effort"] == "medium"


@pytest.mark.asyncio
async def test_thinking_to_reasoning_effort_low():
    """budget_tokens ≤ 2000 → low"""
    provider = OpenAIBaseProvider(base_url="http://test", api_key="test", target_model="test")
    req = AnthropicRequest(
        model="claude-sonnet-4-5",
        messages=[Message(role="user", content="test")],
        thinking=Thinking(type="enabled", budget_tokens=1000)
    )
    body = await provider.translate_request(req)
    assert body["reasoning_effort"] == "low"


@pytest.mark.asyncio
async def test_no_thinking_no_reasoning_effort():
    """Without thinking, reasoning_effort should NOT be in body."""
    provider = OpenAIBaseProvider(base_url="http://test", api_key="test", target_model="test")
    req = AnthropicRequest(
        model="claude-sonnet-4-5",
        messages=[Message(role="user", content="test")]
    )
    body = await provider.translate_request(req)
    assert "reasoning_effort" not in body


@pytest.mark.asyncio
async def test_thinking_disabled_no_reasoning_effort():
    """thinking type='disabled' should NOT add reasoning_effort."""
    provider = OpenAIBaseProvider(base_url="http://test", api_key="test", target_model="test")
    req = AnthropicRequest(
        model="claude-sonnet-4-5",
        messages=[Message(role="user", content="test")],
        thinking=Thinking(type="disabled", budget_tokens=4000)
    )
    body = await provider.translate_request(req)
    assert "reasoning_effort" not in body


# ----------------------------------------
# 10. AnthropicRequest with thinking field
# ----------------------------------------

def test_anthropic_request_with_thinking():
    """AnthropicRequest correctly parses thinking field."""
    payload = {
        "model": "claude-sonnet-4-5",
        "messages": [{"role": "user", "content": "hello"}],
        "thinking": {"type": "enabled", "budget_tokens": 4000}
    }
    req = AnthropicRequest(**payload)
    assert req.thinking is not None
    assert req.thinking.type == "enabled"
    assert req.thinking.budget_tokens == 4000


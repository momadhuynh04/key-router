import json

from bridge import sse


def _feed(events, state=None):
    state = state or sse.TurnState()
    out = []
    for event in events:
        out.extend(sse.feed(event, state))
    return "".join(out), state


def _init(session_id="sess-1"):
    return {"type": "system", "subtype": "init", "session_id": session_id, "cwd": "/repo"}


def _assistant(blocks):
    return {"type": "assistant", "session_id": "sess-1", "message": {"content": blocks}}


def _result(**overrides):
    payload = {
        "type": "result",
        "subtype": "success",
        "session_id": "sess-1",
        "is_error": False,
        "result": "",
    }
    payload.update(overrides)
    return payload


def test_text_block_is_emitted():
    text, state = _feed([
        _init(),
        _assistant([{"type": "text", "text": "Xin chào"}]),
    ])
    assert text == "Xin chào"
    assert state.emitted is True
    assert state.session_id == "sess-1"


def test_tool_use_is_rendered_inline():
    text, _ = _feed([
        _assistant([{"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "ls -la"}}]),
    ])
    assert "Bash" in text
    assert "ls -la" in text


def test_tool_result_preview_is_rendered():
    text, _ = _feed([
        {
            "type": "user",
            "session_id": "sess-1",
            "message": {"content": [{"type": "tool_result", "tool_use_id": "t1", "content": "total 2"}]},
        },
    ])
    assert "total 2" in text


def test_tool_result_array_content_is_flattened():
    text, _ = _feed([
        {
            "type": "user",
            "message": {"content": [{
                "type": "tool_result",
                "tool_use_id": "t1",
                "content": [{"type": "text", "text": "phan mot"}, {"type": "text", "text": "phan hai"}],
            }]},
        },
    ])
    assert "phan mot" in text and "phan hai" in text


def test_result_is_not_duplicated_when_assistant_text_already_emitted():
    text, _ = _feed([
        _assistant([{"type": "text", "text": "Xong."}]),
        _result(result="Xong."),
    ])
    assert text.count("Xong.") == 1


def test_result_is_used_as_fallback_when_nothing_streamed():
    text, state = _feed([_result(result="Kết quả cuối")])
    assert "Kết quả cuối" in text
    assert state.emitted is True


def test_permission_denials_are_surfaced():
    text, state = _feed([
        _assistant([{"type": "text", "text": "Đã thử."}]),
        _result(permission_denials=[{"tool_name": "Bash", "tool_input": {"command": "git fetch"}}]),
    ])
    assert "Blocked by permissions" in text
    assert "Bash" in text
    assert "git fetch" in text
    assert len(state.denials) == 1


def test_error_result_is_surfaced():
    text, state = _feed([
        _result(is_error=True, error="Permission denied", subtype="error_during_execution"),
    ])
    assert "Error" in text
    assert "Permission denied" in text
    assert state.is_error is True


def test_cost_duration_and_turns_footer():
    text, state = _feed([
        _assistant([{"type": "text", "text": "ok"}]),
        _result(total_cost_usd=0.0123, duration_ms=178000, num_turns=7),
    ])
    assert "$0.0123" in text
    assert "178.0s" in text
    assert "7 turn" in text
    assert state.duration_ms == 178000


def test_unknown_event_types_are_ignored():
    text, _ = _feed([
        {"type": "stream_event", "event": {"type": "content_block_delta"}},
        {"type": "something_else"},
    ])
    assert text == ""


def test_session_id_taken_from_later_events_too():
    _, state = _feed([
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "a"}]}},
    ])
    assert state.session_id is None

    _, state = _feed([
        {"type": "assistant", "session_id": "sess-9", "message": {"content": [{"type": "text", "text": "b"}]}},
    ], state)
    assert state.session_id == "sess-9"


# ------------------------------------------------------------- SSE formatting

def test_chunk_shape():
    line = sse.text_chunk("chatcmpl-1", "key-router-agents", "hi")
    assert line.startswith("data: ")
    assert line.endswith("\n\n")
    payload = json.loads(line[len("data: "):].strip())
    assert payload["object"] == "chat.completion.chunk"
    assert payload["model"] == "key-router-agents"
    assert payload["choices"][0]["delta"]["content"] == "hi"
    assert payload["choices"][0]["finish_reason"] is None


def test_finish_chunk_has_stop_reason():
    payload = json.loads(sse.finish_chunk("c", "m")[len("data: "):].strip())
    assert payload["choices"][0]["finish_reason"] == "stop"


def test_completion_object_shape():
    payload = sse.completion("chatcmpl-1", "key-router-agents", "noi dung")
    assert payload["object"] == "chat.completion"
    assert payload["choices"][0]["message"] == {"role": "assistant", "content": "noi dung"}
    assert payload["choices"][0]["finish_reason"] == "stop"
    assert payload["usage"]["total_tokens"] == 0


def test_done_line():
    assert sse.DONE_LINE == "data: [DONE]\n\n"


def test_long_tool_input_is_clipped():
    text, _ = _feed([
        _assistant([{"type": "tool_use", "name": "Bash", "input": {"command": "x" * 500}}]),
    ])
    assert "…" in text
    assert len(text) < 500

from bridge import directives


def test_plain_prompt():
    p = directives.parse("sửa giúp tao middleware auth")
    assert p.kind == "prompt"
    assert p.agent_id is None
    assert p.text == "sửa giúp tao middleware auth"


def test_empty_message_is_prompt():
    p = directives.parse("")
    assert p.kind == "prompt"
    assert p.text == ""


def test_start_with_agent_and_prompt():
    p = directives.parse("/start opus-probrowser làm giúp tao X")
    assert p.kind == "start"
    assert p.agent_id == "opus-probrowser"
    assert p.text == "làm giúp tao X"


def test_start_with_agent_only():
    p = directives.parse("/start opus-probrowser")
    assert p.kind == "start"
    assert p.agent_id == "opus-probrowser"
    assert p.text == ""


def test_bare_start_uses_default_agent():
    p = directives.parse("/start")
    assert p.kind == "start"
    assert p.agent_id is None
    assert p.text == ""


def test_agents_directive():
    assert directives.parse("/agents").kind == "agents"
    assert directives.parse("/list").kind == "agents"
    assert directives.parse("/help").kind == "agents"


def test_stop_directive():
    p = directives.parse("/stop")
    assert p.kind == "stop"
    assert p.agent_id is None

    p = directives.parse("/stop opus-probrowser")
    assert p.kind == "stop"
    assert p.agent_id == "opus-probrowser"


def test_directive_is_case_insensitive_and_trims():
    p = directives.parse("  /START opus-probrowser  ")
    assert p.kind == "start"
    assert p.agent_id == "opus-probrowser"


def test_similar_prefix_is_not_a_directive():
    assert directives.parse("/startle me").kind == "prompt"
    assert directives.parse("/stopwatch").kind == "prompt"


def test_multi_line_prompt_after_directive():
    p = directives.parse("/start a1\nline one\nline two")
    assert p.kind == "start"
    assert p.agent_id == "a1"
    assert p.text == "line one\nline two"

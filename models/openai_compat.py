from pydantic import BaseModel
from typing import List, Optional, Union, Dict, Any

class OpenAIMessage(BaseModel):
    role: str
    # Assistant messages with only tool_calls may omit content entirely
    content: Optional[Union[str, List[Dict[str, Any]], None]] = None
    model_config = {"extra": "allow"}

class OpenAIRequest(BaseModel):
    model: str
    messages: List[OpenAIMessage]
    temperature: Optional[float] = None
    top_p: Optional[float] = None
    max_tokens: Optional[int] = None
    stream: Optional[bool] = False
    stop: Optional[Union[str, List[str]]] = None
    tools: Optional[List[Dict[str, Any]]] = None
    tool_choice: Optional[Union[str, Dict[str, Any]]] = None
    response_format: Optional[Dict[str, Any]] = None
    model_config = {"extra": "allow"}

class ResponsesRequest(BaseModel):
    """OpenAI Responses API request — native protocol of Codex CLI."""
    model: str
    input: Union[str, List[Dict[str, Any]]]
    instructions: Optional[str] = None
    stream: Optional[bool] = False
    max_output_tokens: Optional[int] = None
    tools: Optional[List[Dict[str, Any]]] = None
    additional_tools: Optional[List[Dict[str, Any]]] = None
    tool_choice: Optional[Any] = None
    reasoning: Optional[Dict[str, Any]] = None
    model_config = {"extra": "allow"}

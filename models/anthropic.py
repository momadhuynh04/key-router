from pydantic import BaseModel
from typing import List, Optional, Union, Dict, Any

class MessageContent(BaseModel):
    type: str
    text: Optional[str] = None
    # For image or tool uses, extra fields can be accepted via extra="allow" if needed
    model_config = {"extra": "allow"}

class Message(BaseModel):
    role: str
    content: Union[str, List[Dict[str, Any]]]

class Thinking(BaseModel):
    type: str = "enabled"
    budget_tokens: Optional[int] = None
    model_config = {"extra": "allow"}

class AnthropicRequest(BaseModel):
    model: str
    messages: List[Message]
    system: Optional[Union[str, List[Dict[str, Any]]]] = None
    max_tokens: Optional[int] = None
    metadata: Optional[Dict[str, Any]] = None
    stop_sequences: Optional[List[str]] = None
    stream: Optional[bool] = False
    temperature: Optional[float] = None
    top_p: Optional[float] = None
    top_k: Optional[int] = None
    tools: Optional[List[Dict[str, Any]]] = None
    tool_choice: Optional[Dict[str, Any]] = None
    thinking: Optional[Thinking] = None
    
    model_config = {"extra": "allow"}

class AnthropicUsage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    model_config = {"extra": "allow"}

class AnthropicResponse(BaseModel):
    id: str
    type: str = "message"
    role: str = "assistant"
    content: List[Dict[str, Any]]
    model: str
    stop_reason: Optional[str] = None
    stop_sequence: Optional[str] = None
    usage: AnthropicUsage
    model_config = {"extra": "allow"}

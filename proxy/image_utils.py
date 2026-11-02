"""Image content helpers shared by the ingress and provider adapters.

Clients speak different image shapes (OpenAI `image_url`, Responses
`input_image`, Anthropic `image` blocks, Gemini `inlineData`). The proxy's
internal format is the Anthropic image block:

    {"type": "image",
     "source": {"type": "base64", "media_type": "image/png", "data": "..."}}

or, for remote images:

    {"type": "image", "source": {"type": "url", "url": "https://..."}}
"""

from typing import Any, Dict, Optional, Tuple

DEFAULT_MEDIA_TYPE = "image/png"


def parse_data_url(url: Any) -> Optional[Tuple[str, str]]:
    """Return (media_type, base64_data) for a base64 data URL, else None."""
    if not isinstance(url, str) or not url.startswith("data:"):
        return None
    header, sep, data = url.partition(",")
    if not sep or ";base64" not in header:
        return None
    media_type = header[len("data:"):].split(";", 1)[0] or DEFAULT_MEDIA_TYPE
    if not data:
        return None
    return media_type, data


def image_url_to_anthropic_block(image_url: Any) -> Optional[Dict[str, Any]]:
    """Convert an OpenAI/Responses image URL (string or object) to an image block."""
    if isinstance(image_url, dict):
        image_url = image_url.get("url") or image_url.get("image_url") or image_url.get("file_id")
    if not isinstance(image_url, str) or not image_url.strip():
        return None
    image_url = image_url.strip()

    parsed = parse_data_url(image_url)
    if parsed:
        media_type, data = parsed
        return {
            "type": "image",
            "source": {"type": "base64", "media_type": media_type, "data": data},
        }

    if image_url.startswith(("http://", "https://")):
        return {"type": "image", "source": {"type": "url", "url": image_url}}

    return None


def anthropic_block_to_image_url(block: Dict[str, Any]) -> Optional[str]:
    """Convert an internal image block to a URL for OpenAI `image_url` parts."""
    source = block.get("source") or {}
    source_type = source.get("type")
    if source_type == "base64":
        data = source.get("data", "")
        if not isinstance(data, str) or not data:
            return None
        if data.startswith("data:"):
            return data
        media_type = source.get("media_type") or DEFAULT_MEDIA_TYPE
        return f"data:{media_type};base64,{data}"
    if source_type == "url" and source.get("url"):
        return source["url"]
    return None


def anthropic_block_to_gemini_part(block: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Convert an internal image block to a Gemini inlineData part."""
    source = block.get("source") or {}
    if source.get("type") != "base64":
        return None
    data = source.get("data", "")
    if not isinstance(data, str) or not data:
        return None
    parsed = parse_data_url(data)
    if parsed:
        media_type, data = parsed
    else:
        media_type = source.get("media_type") or DEFAULT_MEDIA_TYPE
    return {"inlineData": {"mimeType": media_type, "data": data}}

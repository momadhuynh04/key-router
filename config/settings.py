from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    openrouter_api_key: str = ""
    deepseek_base_url_anthropic: str = "https://api.deepseek.com/anthropic"
    deepseek_base_url_openai: str = "https://api.deepseek.com"
    deepseek_api_key: str = ""
    google_api_key: str = ""
    google_base_url: str = "https://generativelanguage.googleapis.com/v1beta"
    replicate_api_token: str = ""
    port: int = 8082
    host: str = "127.0.0.1"
    # Image generation: AdCraft waits 120s. Multi-reference requests on
    # qwen-image-2.1-turbo routinely exceed 95s, so use 110s and keep the
    # remaining 10s for the response round-trip.
    image_deadline_seconds: float = 110.0
    image_max_response_bytes: int = 28 * 1024 * 1024
    # Video generation (isolated /v1/videos subsystem)
    keyrouter_api_key: str = ""
    modal_video_gateway_url: str = ""
    modal_video_gateway_token: str = ""
    # MiniMax H3 (FL2VA + Ref2VA) gateway URL, printed by `modal deploy minimaxh3.py`.
    modal_h3_gateway_url: str = ""
    video_job_ttl_seconds: int = 86400
    video_submit_timeout_seconds: float = 30.0
    video_poll_timeout_seconds: float = 15.0
    video_content_timeout_seconds: float = 120.0
    video_max_output_bytes: int = 256 * 1024 * 1024
    video_output_dir: str = ""
    # Audio (video-to-SFX) subsystem — isolated /v1/audio/* routes
    modal_audio_gateway_url: str = ""
    modal_audio_gateway_token: str = ""
    audio_job_ttl_seconds: int = 86400
    audio_submit_timeout_seconds: float = 60.0
    audio_poll_timeout_seconds: float = 15.0
    audio_content_timeout_seconds: float = 120.0
    audio_max_video_bytes: int = 100 * 1024 * 1024
    audio_max_output_bytes: int = 50 * 1024 * 1024
    # Comma-separated host:port allowlist for video_url fetching (SSRF guard).
    audio_video_allow_hosts: str = "127.0.0.1:8000,localhost:8000,127.0.0.1:8082"
    
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

settings = Settings()

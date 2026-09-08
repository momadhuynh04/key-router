from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    openrouter_api_key: str = ""
    deepseek_base_url_anthropic: str = "https://api.deepseek.com/anthropic"
    deepseek_base_url_openai: str = "https://api.deepseek.com"
    deepseek_api_key: str = ""
    google_api_key: str = ""
    google_base_url: str = "https://generativelanguage.googleapis.com/v1beta"
    port: int = 8082
    host: str = "127.0.0.1"
    
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

settings = Settings()

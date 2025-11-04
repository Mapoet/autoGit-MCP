"""Configuration management for Git MCP tools.

This module provides centralized configuration management using Pydantic Settings,
allowing environment variables to be read from:
1. System environment variables
2. MCP server configuration (mcpServers.env)
3. .env files (if using pydantic-settings)

All environment variables are validated at startup, providing fail-fast behavior.
"""
from typing import Optional
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        case_sensitive=True,  # 区分大小写（Linux/容器里更一致）
        env_file=".env",  # 可选：支持 .env 文件
        env_file_encoding="utf-8",
        extra="ignore",  # 忽略未定义的环境变量
    )

    # LLM Providers (git_flow, git_work)
    deepseek_api_key: Optional[str] = Field(default=None, alias="DEEPSEEK_API_KEY", description="DeepSeek API Key")
    deepseek_api_url: Optional[str] = Field(
        default="https://api.deepseek.com/v1/chat/completions",
        alias="DEEPSEEK_API_URL",
        description="DeepSeek API endpoint",
    )
    deepseek_model: Optional[str] = Field(
        default="deepseek-chat",
        alias="DEEPSEEK_MODEL",
        description="DeepSeek model name",
    )

    opengpt_api_key: Optional[str] = Field(default=None, alias="OPENGPT_API_KEY", description="OpenGPT API Key")
    opengpt_api_url: Optional[str] = Field(
        default="https://api.opengpt.com/v1/chat/completions",
        alias="OPENGPT_API_URL",
        description="OpenGPT API endpoint",
    )
    opengpt_model: Optional[str] = Field(
        default="gpt-4.1-mini",
        alias="OPENGPT_MODEL",
        description="OpenGPT model name",
    )

    openai_api_key: Optional[str] = Field(default=None, alias="OPENAI_API_KEY", description="OpenAI API Key")

    # Git Repository Access (git_work, git_catalog)
    github_token: Optional[str] = Field(default=None, alias="GITHUB_TOKEN", description="GitHub Personal Access Token")
    gitee_token: Optional[str] = Field(default=None, alias="GITEE_TOKEN", description="Gitee Personal Access Token")
    gitlab_token: Optional[str] = Field(
        default=None, alias="GITLAB_TOKEN", description="GitLab Personal Access Token"
    )
    gitlab_private_token: Optional[str] = Field(
        default=None, alias="GITLAB_PRIVATE_TOKEN", description="GitLab Personal Access Token (alternative name)"
    )
    gitlab_url: Optional[str] = Field(
        default="https://gitlab.com/api/v4",
        alias="GITLAB_URL",
        description="Custom GitLab instance URL",
    )

    # Request Configuration
    request_timeout_sec: int = Field(default=120, alias="REQUEST_TIMEOUT_SEC", description="Request timeout in seconds")

    @field_validator("request_timeout_sec")
    @classmethod
    def _validate_timeout(cls, value: int) -> int:
        """Ensure timeout is positive."""
        if value <= 0:
            raise ValueError("request_timeout_sec must be positive")
        return value

    def get_gitlab_token(self) -> Optional[str]:
        """Get GitLab token from either GITLAB_TOKEN or GITLAB_PRIVATE_TOKEN."""
        return self.gitlab_token or self.gitlab_private_token


# Global settings instance (loaded at module import time)
_settings: Optional[Settings] = None


def load_settings() -> Settings:
    """Load and validate settings from environment variables.
    
    This function can be called multiple times to reload settings.
    For production use, it's recommended to call once at startup.
    
    Returns:
        Settings: Validated settings object
        
    Raises:
        RuntimeError: If required settings are missing or invalid
    """
    global _settings
    try:
        _settings = Settings()
    except Exception as e:
        raise RuntimeError(f"Configuration error: {e}") from e
    return _settings


def get_settings() -> Settings:
    """Get the current settings instance.
    
    If settings haven't been loaded yet, this will load them automatically.
    
    Returns:
        Settings: Current settings instance
    """
    global _settings
    if _settings is None:
        _settings = load_settings()
    return _settings


def reload_settings() -> Settings:
    """Reload settings from environment variables.
    
    This can be useful for hot-reloading configuration without restarting the server.
    Note: External clients (OpenAI, GitHub, etc.) are not automatically recreated.
    
    Returns:
        Settings: Reloaded settings object
    """
    return load_settings()


def mask_secret(value: Optional[str], show_first: int = 4, show_last: int = 4) -> str:
    """Mask a secret value for safe display in logs or responses.
    
    Args:
        value: Secret value to mask
        show_first: Number of characters to show at the beginning
        show_last: Number of characters to show at the end
        
    Returns:
        Masked string (e.g., "sk-xxxxx...yyyy")
    """
    if not value:
        return "(unset)"
    if len(value) <= show_first + show_last:
        return "***"  # Too short to mask meaningfully
    return f"{value[:show_first]}...{value[-show_last:]}"


# Load settings at module import time
try:
    _settings = load_settings()
except Exception:
    # If loading fails at import time, we'll load it lazily when get_settings() is called
    _settings = None


from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env", env_file_encoding="utf-8", extra="ignore"
    )

    rabbitmq_url: str
    redis_url: str

    elevenlabs_api_key: str
    elevenlabs_voice_id: str = "21m00Tcm4TlvDq8ikWAM"  # ElevenLabs default demo voice
    elevenlabs_model_id: str = "eleven_v4"

    audio_output_dir: Path = BASE_DIR / "audio_output"
    static_dir: Path = BASE_DIR / "static"

    # keep <= the model's per-request limit (Multilingual v2: 10,000; Eleven v3: 3,000)
    max_chunk_chars: int = Field(4000, gt=0, le=10_000)
    tts_concurrency: int = Field(4, gt=0)  # max in-flight ElevenLabs requests per job
    context_chars: int = 300  # neighbouring text sent as previous_text/next_text

    max_text_chars: int = 100_000
    api_key: str | None = None  # unset/empty = auth disabled
    rate_limit_per_minute: int = 10  # per client IP; 0 disables

    job_ttl_seconds: int = 6 * 3600
    audio_retention_seconds: int = 24 * 3600  # keep this > job_ttl_seconds


settings = Settings()  # type: ignore[call-arg]
settings.audio_output_dir.mkdir(parents=True, exist_ok=True)

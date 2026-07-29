from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    rabbitmq_url: str
    redis_url: str

    elevenlabs_api_key: str
    elevenlabs_voice_id: str = "21m00Tcm4TlvDq8ikWAM"  # ElevenLabs default demo voice

    audio_output_dir: Path = BASE_DIR / "audio_output"
    max_chunk_chars: int = 4000


settings = Settings()  # type: ignore[call-arg]

settings.audio_output_dir.mkdir(parents=True, exist_ok=True)

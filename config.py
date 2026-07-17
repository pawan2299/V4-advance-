from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    input_video: str = "/content/drive/MyDrive/input.mp4"
    output_dir: str = "/content/drive/MyDrive/upscaled"
    upscale_factor: int = 4
    enable_face_restore: bool = True
    enable_denoise: bool = True
    tile_size: int = 256
    crf: int = 16
    preset: str = "slow"
    audio_copy: bool = True
    batch_mode: bool = False
    cleanup_temp: bool = True
    model_name: str = "Real-ESRGAN"
    face_model_name: str = "GFPGAN"


SETTINGS = Settings()

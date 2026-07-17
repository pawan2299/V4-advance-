# AI Video Upscaler Colab

Production-ready Google Colab workflow for upscaling 720p videos to higher resolution with open-source tools, GPU acceleration, audio preservation, and optional face restoration.

## What this repository provides

- Google Colab notebook for one-click execution
- Free GPU-friendly video upscaling pipeline
- Frame extraction and re-encoding workflow
- Optional face restoration for portrait/video faces
- Auto Drive mount and input/output path handling
- FFmpeg-based final export with original audio preserved
- Batch processing support
- Clean, modular Python code

## Recommended pipeline

1. Extract frames from the input video
2. Upscale frames with Real-ESRGAN
3. Restore faces with GFPGAN or CodeFormer when needed
4. Rebuild the video with FFmpeg
5. Copy original audio to the final output

## Colab inputs

Set these variables inside the notebook:

```python
INPUT_VIDEO = "/content/drive/MyDrive/input.mp4"
OUTPUT_DIR = "/content/drive/MyDrive/upscaled"
UPSCALE_FACTOR = 4
ENABLE_FACE_RESTORE = True
ENABLE_DENOISE = True
```

## Files to add

- `Video_Upscaler.ipynb` for Colab execution
- `requirements.txt` for dependencies
- `install.sh` for local/bootstrap setup
- `src/` for reusable Python modules
- `scripts/` for helper automation
- `docs/` for usage and troubleshooting notes

## Notes

This project is designed for quality-first processing on Colab free GPU sessions. It prioritizes visual fidelity over speed.

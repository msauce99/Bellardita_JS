"""
Standalone script to convert PNG frames to MP4 video using FFmpeg.
Use for converting camera output frames to video.

Usage:
    1. Set the CONFIGURATIONS section below (cam_frames_location, output_fps, etc.)
    2. Run:  python frames_to_mp4.py
"""

import re
import subprocess
import sys
from pathlib import Path


# =====================================================================================================
# CONFIGURATIONS (Set prior to running this script)
cam_frames_location = r"C:\Users\mahin\Documents\LabeoTech\BehavioralCamera\Data\25381544_20260303_153435" #path to frames folder to convert
output_fps = 30 #frames per second for output video
output_save_dir = r"C:\data_thesis" #directory to save output MP4
starting_frame_number = None #optional: specify the starting frame number (e.g. 1 if frames are named frame_000001.png, frame_000002.png, etc.). If None, it will auto-detect from the first frame in the directory.
# =====================================================================================================

def frames_to_mp4(frames_dir: str, out_mp4: str, fps: int = 60, ffmpeg_exe: str = "ffmpeg", preset: str = "fast", start_number: int = None):
    """
    Convert PNG frames to MP4 video using FFmpeg.
    
    Args:
        frames_dir: Directory containing frame_000001.png, frame_000002.png, etc.
        out_mp4: Output MP4 file path
        fps: Frames per second
        ffmpeg_exe: Path to FFmpeg executable (or 'ffmpeg' if on PATH)
        preset: FFmpeg encoding preset (ultrafast, superfast, veryfast, faster, fast, medium, slow, slower, veryslow)
        start_number: First frame number (auto-detected from directory if None)
    """
    frames_dir = Path(frames_dir)
    out_mp4 = Path(out_mp4)
    out_mp4.parent.mkdir(parents=True, exist_ok=True)

    # Auto-detect start_number from the first frame in the directory
    frame_files = sorted(frames_dir.glob("frame_*.png"))
    if not frame_files:
        raise FileNotFoundError(f"No frame_*.png files found in {frames_dir}")

    total_frames = len(frame_files)

    if start_number is None:
        # Extract number from filename like frame_000284.png -> 284
        start_number = int(frame_files[0].stem.split('_')[1])
        print(f"Auto-detected start_number: {start_number}")

    pattern = str(frames_dir / "frame_%06d.png")  # matches frame_000001.png etc.

    cmd = [
        ffmpeg_exe,
        "-y",
        "-framerate", str(fps),
        "-start_number", str(start_number),
        "-i", pattern,
        # Optimized encoding settings:
        "-c:v", "libx264",
        "-pix_fmt", "yuv420p",
        "-crf", "18",
        "-preset", preset,  # Configurable preset for speed/quality tradeoff
        str(out_mp4),
    ]

    print(f"Running ({total_frames} frames):\n", " ".join(cmd))

    # Run FFmpeg and parse stderr for progress
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )

    frame_re = re.compile(r"frame=\s*(\d+)")
    last_percent = -1

    for line in proc.stderr:
        m = frame_re.search(line)
        if m:
            current = int(m.group(1))
            percent = min(int(100 * current / total_frames), 100)
            if percent != last_percent:
                sys.stdout.write(f"\r[FFMPEG] {current}/{total_frames} frames ({percent}%)")
                sys.stdout.flush()
                last_percent = percent

    proc.wait()
    sys.stdout.write("\n")  # newline after progress

    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg failed with return code {proc.returncode}.")

    print(f"Saved: {out_mp4}")

if __name__ == "__main__":
    frames_dir = Path(cam_frames_location)
    if not frames_dir.exists():
        print(f"[ERROR] Frames directory not found: {frames_dir}")
        raise SystemExit(1)

    # Build output filename from the frames folder name
    save_dir = Path(output_save_dir)
    save_dir.mkdir(parents=True, exist_ok=True)
    out_mp4 = save_dir / f"{frames_dir.name}.mp4"

    print(f"\n{'='*80}")
    print(f"[CONVERT] Frames dir: {frames_dir}")
    print(f"[CONVERT] FPS: {output_fps}")
    print(f"[CONVERT] Start frame: {starting_frame_number or 'auto-detect'}")
    print(f"[CONVERT] Output: {out_mp4}")
    print(f"{'='*80}")

    frames_to_mp4(
        frames_dir=str(frames_dir),
        out_mp4=str(out_mp4),
        fps=output_fps,
        start_number=starting_frame_number,
    )
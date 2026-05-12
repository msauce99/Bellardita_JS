"""
Standalone script to convert trial CSV data to MP4 video - create joystick trajectory visualization

Usage:
    1. Set the CONFIGURATIONS section below (csv_location, output_fps, etc.)
    2. Run:  python convert_csv_to_mp4.py

This script:
1. Reads the specified CSV file containing joystick trial data.
2. Generates a visual replay of the trial using the generate_trial_replay_video function.
   - Pipes frames directly to FFmpeg for encoding without saving PNGs to disk.
   - Falls back to saving PNG frames if piping fails.
3. Saves the output MP4 to the specified output directory.

Use this after trials are complete to generate videos separately.
"""

import csv
import subprocess
import sys
from pathlib import Path

import matplotlib.patches as patches
import numpy as np

# Add current directory to path to import local modules
sys.path.insert(0, str(Path(__file__).parent))

from configurations import (
    CLAMP_RADIUS, DEBUG,
    TRAINING_INNER_X_MM, TRAINING_INNER_Y_MM, TRAINING_OUTSIDE_R_MM
)

# =====================================================================================================
# CONFIGURATIONS (Set prior to running this script)
Training_trial = True #set to True if csv is from a training trial (will show training visualization)
Training_stage = 1 #if Training_trial is True, set the training stage (1,2,3)
csv_location = r"C:\data_thesis\joystick_trial_2026-03-04_143855.csv" #path to csv file to convert
output_fps = 30 #frames per second for output video
output_save_dir = r"C:\data_thesis" #directory to save output MP4

#chart generation parameters
generate_chart = True #if True, generate a X vs Time / Y vs Time chart (saved as PNG)

#video generation parameters
playback_speed = 1.0 #playback speed multiplier (1.0 = real-time, 2.0 = 2x speed, 0.5 = half speed)
generate_video = False #if True, generate the MP4 replay video
# =====================================================================================================

# ==================================================
# Trial Replay Video Generation (Fast & Slow Versions)
# ==================================================
def render_frame_numpy(frame_idx, data_x, data_y, output_fps, clamp_radius, trail_points, TRAINING_MODE, TRAINING_STAGE, reward_events=None, reward_display_duration_s=2.0, elapsed_time=None):
    """
    Render a single frame as a NumPy array (RGB).
    Much faster than matplotlib.
    
    Args:
        frame_idx: Frame index (sample index into data arrays)
        data_x, data_y: Lists of x,y coordinates
        output_fps: Output frame rate (used for time calc if elapsed_time not given)
        clamp_radius: Circular boundary radius in mm
        trail_points: List of ALL (x, y) trail points up to this frame (NOT limited by deque)
        TRAINING_MODE: If True, show training visualization
        TRAINING_STAGE: Training stage (1, 2, or 3) - only used if TRAINING_MODE=True
        elapsed_time: If provided, use this as the elapsed time (seconds) instead of frame_idx/fps
        reward_events: List of (timestamp_in_trial_seconds, reason_string) tuples for reward display
        reward_display_duration_s: How long to display reward text (in seconds)
    
    Returns:
        NumPy array (H, W, 3) uint8 RGB image
    """
    from PIL import Image, ImageDraw, ImageFont
    
    if reward_events is None:
        reward_events = []
    
    # Image size and parameters
    width, height = 800, 800
    dpi_scale = 100 / 72  # Convert DPI
    scale_mm_to_px = (width / 30) * dpi_scale  # 30mm fits in image
    center_x, center_y = width // 2, height // 2
    
    # Create blank white image
    img = Image.new('RGB', (width, height), color='white')
    draw = ImageDraw.Draw(img)
    
    # Draw grid
    grid_spacing = int(scale_mm_to_px * 2)  # 2mm grid
    for i in range(0, width, grid_spacing):
        draw.line([(i, 0), (i, height)], fill=(200, 200, 200), width=1)
    for i in range(0, height, grid_spacing):
        draw.line([(0, i), (width, i)], fill=(200, 200, 200), width=1)
    
    # Draw training visualization (before circle so it's in background)
    if TRAINING_MODE:
        if TRAINING_STAGE == 2:
            # Stage 2: green dashed circle (outside this radius)
            circle_radius_px = int(scale_mm_to_px * TRAINING_OUTSIDE_R_MM)
            # Draw as series of dashed arcs
            for angle in range(0, 360, 15):
                draw.arc(
                    [(center_x - circle_radius_px, center_y - circle_radius_px),
                     (center_x + circle_radius_px, center_y + circle_radius_px)],
                    start=angle, end=angle+10,
                    fill='lightgreen', width=2
                )
        elif TRAINING_STAGE == 3:
            # Stage 3: green box (target zone)
            x_min_px = center_x - int(scale_mm_to_px * TRAINING_INNER_X_MM)
            x_max_px = center_x + int(scale_mm_to_px * TRAINING_INNER_X_MM)
            y_min_px = center_y - int(scale_mm_to_px * TRAINING_INNER_Y_MM)
            y_max_px = center_y + height // 4  # Extend upward
            draw.rectangle(
                [(x_min_px, y_min_px), (x_max_px, y_max_px)],
                outline='lightgreen', fill='lightgreen', width=2
            )
    
    # Draw reference circle (black, clamping boundary)
    circle_radius_px = int(scale_mm_to_px * clamp_radius)
    draw.ellipse(
        [(center_x - circle_radius_px, center_y - circle_radius_px),
         (center_x + circle_radius_px, center_y + circle_radius_px)],
        outline='black', width=2
    )
    
    # Label the clamp circle with its radius
    try:
        font = ImageFont.load_default()
    except:
        font = None
    
    circle_label = f"{int(clamp_radius)}mm"
    label_y = center_y + circle_radius_px + 5
    draw.text((center_x - 15, label_y), circle_label, fill='black', font=font)
    
    # Draw FULL trail (all points from start, not limited)
    if len(trail_points) > 1:
        trail_pixels = []
        for x_mm, y_mm in trail_points:
            px = center_x + int(x_mm * scale_mm_to_px)
            py = center_y - int(y_mm * scale_mm_to_px)  # Flip Y
            trail_pixels.append((px, py))
        draw.line(trail_pixels, fill='blue', width=2)
    
    # Draw current position (red dot)
    x_mm, y_mm = data_x[frame_idx], data_y[frame_idx]
    px = center_x + int(x_mm * scale_mm_to_px)
    py = center_y - int(y_mm * scale_mm_to_px)
    dot_radius = 4
    draw.ellipse([(px - dot_radius, py - dot_radius),
                  (px + dot_radius, py + dot_radius)], fill='red')
    
    # Draw text (time, coordinates, reward)
    elapsed = elapsed_time if elapsed_time is not None else frame_idx / output_fps
    text_lines = [
        f"Time: {elapsed:.2f}s | Sample: {frame_idx}",
        f"X: {x_mm:.2f} mm, Y: {y_mm:.2f} mm"
    ]
    
    # Check if a reward event occurred near this frame time
    reward_text = ""
    for event_time, reason in reward_events:
        # Display reward if within the reward_display_duration window after event
        if event_time <= elapsed < event_time + reward_display_duration_s:
            # Format the reason nicely based on what we find in the reason string
            reason_lower = reason.lower()
            if "manual" in reason_lower or "hotkey" in reason_lower:
                reward_text = "Reward - manual"
            elif "training" in reason_lower or "target" in reason_lower:
                reward_text = "Reward - target"
            elif "zone" in reason_lower:
                reward_text = "Reward - zone"
            else:
                # Default formatting: remove underscores and capitalize
                formatted = reason.replace("_", " ").title()
                reward_text = f"Reward - {formatted}"
            break
    
    y_offset = 10
    for line in text_lines:
        draw.text((10, y_offset), line, fill='black', font=font)
        y_offset += 20
    
    # Draw reward text in larger font if a reward is active
    if reward_text:
        # Draw reward text in green, centered at top
        draw.text((250, 30), reward_text, fill='green', font=font)
    
    # Convert to NumPy array
    return np.array(img)


def generate_trial_replay_video(csv_filepath, clamp_radius=CLAMP_RADIUS, output_fps=30, quiet=False, use_pipe=True, training_mode=False, training_stage=1, output_dir=None, playback_speed=1.0, events_csv_filepath=None):
    """
    Generate a replay video of a trial from the recorded CSV file.
    Shows joystick movement with trail (persists across entire video), same as live GUI.
    
    Args:
        csv_filepath: Path to the trial CSV file (position data only)
        clamp_radius: Circular boundary radius in mm
        output_fps: Output video frame rate
        quiet: If True, suppress progress messages (for background rendering)
        use_pipe: If True (default), pipe frames to FFmpeg directly (fast). 
                  If False, save PNG frames to disk (slow but allows inspection).
        training_mode: If True, show training visualization
        training_stage: Training stage (1, 2, or 3)
        output_dir: Directory to save output files. If None, saves next to the CSV.
        playback_speed: Playback speed multiplier (1.0 = real-time, 2.0 = 2x, etc.)
        events_csv_filepath: Path to events CSV (rewards, licks). If None, auto-detects
                             by replacing 'joystick_trial_' with 'joystick_events_' in filename.
    
    Returns:
        video_path: Path to generated MP4, or None if failed
    """
    import matplotlib
    matplotlib.use('Agg')  # Use non-interactive backend to avoid threading issues
    import matplotlib.pyplot as plt_agg
    
    if not csv_filepath or not Path(csv_filepath).exists():
        if DEBUG:
            print(f"[VIDEO] CSV file not found: {csv_filepath}")
        return None
    
    try:
        # Read position data from CSV
        data_x = []
        data_y = []
        data_t = []  # timestamps from CSV
        
        with open(csv_filepath, 'r') as f:
            reader = csv.DictReader(f)
            for row in reader:
                try:
                    data_x.append(float(row['x_mm']))
                    data_y.append(float(row['y_mm']))
                    data_t.append(float(row['t_s']))
                except (ValueError, KeyError):
                    continue
        
        if len(data_x) < 2:
            if DEBUG:
                print(f"[VIDEO] Not enough data points in {csv_filepath}")
            return None
        
        # Read reward events from events CSV (separate file)
        reward_events = []  # List of (t_s_relative, reason)
        events_path = events_csv_filepath
        if events_path is None:
            # Auto-detect: replace 'joystick_trial_' with 'joystick_events_' in filename
            csv_name = Path(csv_filepath).name
            if 'joystick_trial_' in csv_name:
                events_name = csv_name.replace('joystick_trial_', 'joystick_events_')
                events_path = Path(csv_filepath).parent / events_name
        
        if events_path and Path(events_path).exists():
            with open(events_path, 'r') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    try:
                        if row.get('event_type') == 'reward':
                            trigger_t = float(row['trigger_t_s'])
                            reason = row.get('reason', 'reward')
                            reward_events.append((trigger_t, reason))
                    except (ValueError, KeyError):
                        continue
            if not quiet:
                print(f"[VIDEO] Loaded {len(reward_events)} reward events from events CSV")
        elif not quiet:
            print(f"[VIDEO] No events CSV found (rewards won't be shown in video)")
        
        # Subsample for playback speed
        # At ~1kHz sampling and 30fps output, real-time = every ~33rd sample
        # playback_speed=2.0 means skip twice as many samples
        total_duration = data_t[-1] - data_t[0] if data_t else len(data_x) / 1000.0
        samples_per_frame = max(1, int((len(data_x) / total_duration) / output_fps * playback_speed))
        frame_indices = list(range(0, len(data_x), samples_per_frame))
        total_frames = len(frame_indices)
        
        if not quiet:
            video_duration = total_frames / output_fps
            print(f"[VIDEO] {len(data_x)} samples, {total_duration:.1f}s trial")
            print(f"[VIDEO] Playback speed: {playback_speed}x -> {total_frames} frames -> {video_duration:.1f}s video")
        
        # Use a LIST for trail (not deque) so it persists across entire video
        trail_points = []
        
        # Generate output filename
        csv_path = Path(csv_filepath)
        save_dir = Path(output_dir) if output_dir else csv_path.parent
        save_dir.mkdir(parents=True, exist_ok=True)
        mp4_path = save_dir / f"replay_{csv_path.stem}.mp4"
        
        # FAST MODE: Pipe frames directly to FFmpeg (no PNG files)
        if use_pipe:
            # FFmpeg command to read raw RGB frames from stdin
            cmd = [
                "ffmpeg",
                "-y",
                "-f", "rawvideo",
                "-pixel_format", "rgb24",
                "-video_size", "800x800",
                "-framerate", str(output_fps),
                "-i", "pipe:0",
                "-c:v", "libx264",
                "-pix_fmt", "yuv420p",
                "-crf", "18",
                "-preset", "fast",  # Faster encoding
                str(mp4_path),
            ]
            
            try:
                if not quiet:
                    print(f"[VIDEO] Starting FFmpeg encoding (please wait, this may take a minute)...")
                
                # Use DEVNULL for stdout/stderr to avoid pipe deadlock
                ffmpeg_proc = subprocess.Popen(
                    cmd,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL
                )
                
                # Render frames and pipe to FFmpeg
                prev_sample = 0
                for out_idx, sample_idx in enumerate(frame_indices):
                    # Add ALL skipped trail points so the trail is complete
                    for s in range(prev_sample, sample_idx + 1):
                        trail_points.append((data_x[s], data_y[s]))
                    prev_sample = sample_idx + 1
                    
                    # Render frame using the sampled index with actual trial time
                    actual_time = data_t[sample_idx] - data_t[0] if data_t else sample_idx / 1000.0
                    frame_array = render_frame_numpy(
                        sample_idx, data_x, data_y, output_fps, clamp_radius, trail_points,
                        TRAINING_MODE=training_mode, TRAINING_STAGE=training_stage,
                        reward_events=reward_events, reward_display_duration_s=2.0,
                        elapsed_time=actual_time
                    )
                    
                    # Write raw RGB24 to FFmpeg stdin
                    try:
                        ffmpeg_proc.stdin.write(frame_array.tobytes())
                    except BrokenPipeError:
                        ffmpeg_proc.wait()
                        raise RuntimeError(f"FFmpeg pipe broken at frame {out_idx}")
                    
                    # Progress indicator
                    if not quiet:
                        if (out_idx + 1) % 100 == 0 or (out_idx + 1) == total_frames:
                            percent = int(100 * (out_idx + 1) / total_frames)
                            print(f"[VIDEO] Frames: {out_idx + 1}/{total_frames} ({percent}%)")
                
                # Close stdin to signal FFmpeg we're done
                ffmpeg_proc.stdin.close()
                
                if not quiet:
                    print(f"[VIDEO] All frames sent. Waiting for FFmpeg to finish encoding...")
                
                # Wait for FFmpeg with timeout
                try:
                    ffmpeg_proc.wait(timeout=600)  # 10 minute timeout for safety
                except subprocess.TimeoutExpired:
                    ffmpeg_proc.kill()
                    raise RuntimeError("FFmpeg encoding timeout (exceeded 10 minutes)")
                
                if ffmpeg_proc.returncode != 0:
                    raise RuntimeError(f"FFmpeg encoding failed with return code {ffmpeg_proc.returncode}")
                
                if not quiet:
                    print(f"✓ [VIDEO] MP4 created (piped): {mp4_path}")
                
                return str(mp4_path)
            
            except Exception as e:
                print(f"[VIDEO] Pipe mode failed: {e}")
                print(f"[VIDEO] Falling back to PNG+FFmpeg mode...")
                use_pipe = False  # Fall back to PNG mode
        
        # FALLBACK: Save PNG frames and use FFmpeg (slow but reliable)
        if not use_pipe:
            # Create temporary figure for rendering (using Agg backend)
            fig_agg, ax = plt_agg.subplots(figsize=(8, 8), dpi=100)
            ax.set_aspect('equal')
            ax.grid(True)
            ax.set_xlim([-15, 15])
            ax.set_ylim([-15, 15])
            ax.set_xlabel("X [mm]", fontsize=10)
            ax.set_ylabel("Y [mm]", fontsize=10)
            ax.set_title("Trial Replay - Joystick Position")
            
            # Add reference circle
            circle = plt_agg.Circle((0, 0), clamp_radius, color='black', fill=False, linewidth=2)
            ax.add_patch(circle)
            
            # Add training visualization if needed
            if training_mode:
                if training_stage == 2:
                    outer_circle = plt_agg.Circle(
                        (0, 0),
                        TRAINING_OUTSIDE_R_MM,
                        color='lightgreen',
                        fill=False,
                        linewidth=2.5,
                        linestyle='--',
                        alpha=0.7,
                        label='Stage 2: Move Outside'
                    )
                    ax.add_patch(outer_circle)
                    ax.legend(loc='upper right')
                elif training_stage == 3:
                    ymin_target = TRAINING_INNER_Y_MM
                    ymax_axis = ax.get_ylim()[1]
                    
                    box = patches.Rectangle(
                        xy=(-TRAINING_INNER_X_MM, ymin_target),
                        width=2 * TRAINING_INNER_X_MM,
                        height=ymax_axis - ymin_target,
                        linewidth=2,
                        edgecolor='lightgreen',
                        facecolor='lightgreen',
                        alpha=0.3,
                        label='Stage 3: Target Zone'
                    )
                    ax.add_patch(box)
                    ax.legend(loc='upper right')
            
            # Create artists
            dot, = ax.plot([], [], 'ro', markersize=8, label='Position')
            line, = ax.plot([], [], 'b-', alpha=0.5, linewidth=1.5, label='Trail')
            time_text = ax.text(0.05, 0.95, '', transform=ax.transAxes, fontsize=12, 
                               verticalalignment='top', bbox=dict(boxstyle='round', facecolor='wheat', alpha=0.8))
            coord_text = ax.text(0.05, 0.85, '', transform=ax.transAxes, fontsize=11,
                                verticalalignment='top')
            ax.legend(loc='upper right')
            
            frames_dir = save_dir / f"replay_{csv_path.stem}_frames"
            frames_dir.mkdir(parents=True, exist_ok=True)
            
            # Reset trail_points for PNG fallback (in case it was already populated)
            trail_points = []
            
            # Render frames (subsampled) and save as PNG
            prev_sample = 0
            for out_idx, sample_idx in enumerate(frame_indices):
                # Add all skipped trail points so the trail is complete
                for s in range(prev_sample, sample_idx + 1):
                    trail_points.append((data_x[s], data_y[s]))
                prev_sample = sample_idx + 1
                
                # Current position
                x, y = data_x[sample_idx], data_y[sample_idx]
                
                # Update dot
                dot.set_data([x], [y])
                
                # Update trail
                if trail_points:
                    xs, ys = zip(*trail_points)
                    line.set_data(xs, ys)
                
                # Update text with actual trial time
                elapsed = data_t[sample_idx] - data_t[0] if data_t else sample_idx / 1000.0
                time_text.set_text(f"Time: {elapsed:.2f}s | Sample: {sample_idx}")
                coord_text.set_text(f"X: {x:.2f} mm, Y: {y:.2f} mm")
                
                # Draw and save frame
                fig_agg.canvas.draw()
                frame_path = frames_dir / f"frame_{out_idx:06d}.png"
                fig_agg.savefig(str(frame_path), dpi=100, bbox_inches='tight')
                
                # Progress indicator
                if not quiet:
                    if (out_idx + 1) % 100 == 0 or (out_idx + 1) == total_frames:
                        percent = int(100 * (out_idx + 1) / total_frames)
                        print(f"[VIDEO] Progress: {out_idx + 1}/{total_frames} frames ({percent}%)")
            
            plt_agg.close(fig_agg)
            if not quiet:
                print(f"✓ [VIDEO] Replay frames saved: {frames_dir}")
            
            return str(frames_dir)
    
    except Exception as e:
        print(f"[VIDEO] Error generating replay video: {e}")
        import traceback
        traceback.print_exc()
        return None


def generate_movement_chart(csv_filepath, output_dir=None, training_mode=False, training_stage=1, events_csv_filepath=None):
    """
    Generate a chart showing X and Y joystick position over time.
    Two subplots: X vs Time (top) and Y vs Time (bottom), with reward events marked.
    
    Args:
        csv_filepath: Path to the trial CSV file (position data)
        output_dir: Directory to save chart. If None, saves next to the CSV.
        training_mode: If True, show training zone boundaries
        training_stage: Training stage (1, 2, or 3)
        events_csv_filepath: Path to events CSV. If None, auto-detects.
    
    Returns:
        chart_path: Path to saved PNG, or None if failed
    """
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt_chart
    
    csv_path = Path(csv_filepath)
    if not csv_path.exists():
        print(f"[CHART] CSV file not found: {csv_filepath}")
        return None
    
    # Read position data
    data_t, data_x, data_y = [], [], []
    
    with open(csv_filepath, 'r') as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                data_t.append(float(row['t_s']))
                data_x.append(float(row['x_mm']))
                data_y.append(float(row['y_mm']))
            except (ValueError, KeyError):
                continue
    
    if len(data_t) < 2:
        print(f"[CHART] Not enough data points")
        return None
    
    # Read reward events from events CSV
    reward_times, reward_reasons = [], []
    events_path = events_csv_filepath
    if events_path is None:
        csv_name = csv_path.name
        if 'joystick_trial_' in csv_name:
            events_name = csv_name.replace('joystick_trial_', 'joystick_events_')
            events_path = csv_path.parent / events_name
    
    if events_path and Path(events_path).exists():
        with open(events_path, 'r') as f:
            reader = csv.DictReader(f)
            for row in reader:
                try:
                    if row.get('event_type') == 'reward':
                        reward_times.append(float(row['trigger_t_s']))
                        reward_reasons.append(row.get('reason', ''))
                except (ValueError, KeyError):
                    continue
    
    # Create figure with two subplots sharing the time axis
    fig, (ax_x, ax_y) = plt_chart.subplots(2, 1, figsize=(12, 6), sharex=True)
    fig.suptitle(f"Joystick Movement - {csv_path.stem}", fontsize=13)
    
    # X vs Time
    ax_x.plot(data_t, data_x, 'b-', linewidth=0.8, alpha=0.8)
    ax_x.set_ylabel("X [mm]", fontsize=11)
    ax_x.grid(True, alpha=0.3)
    ax_x.axhline(y=0, color='black', linewidth=0.5, alpha=0.5)
    
    # Y vs Time
    ax_y.plot(data_t, data_y, 'r-', linewidth=0.8, alpha=0.8)
    ax_y.set_ylabel("Y [mm]", fontsize=11)
    ax_y.set_xlabel("Time [s]", fontsize=11)
    ax_y.grid(True, alpha=0.3)
    ax_y.axhline(y=0, color='black', linewidth=0.5, alpha=0.5)
    
    # Add training zone boundaries if applicable
    if training_mode:
        if training_stage == 2:
            # Stage 2: outside radius boundary
            for ax, label in [(ax_x, 'X'), (ax_y, 'Y')]:
                ax.axhline(y=TRAINING_OUTSIDE_R_MM, color='green', linestyle='--', alpha=0.5, label=f'Stage 2 boundary ({TRAINING_OUTSIDE_R_MM}mm)')
                ax.axhline(y=-TRAINING_OUTSIDE_R_MM, color='green', linestyle='--', alpha=0.5)
        elif training_stage == 3:
            # Stage 3: target box boundaries
            ax_x.axhline(y=TRAINING_INNER_X_MM, color='green', linestyle='--', alpha=0.5, label=f'X boundary (±{TRAINING_INNER_X_MM}mm)')
            ax_x.axhline(y=-TRAINING_INNER_X_MM, color='green', linestyle='--', alpha=0.5)
            ax_y.axhline(y=TRAINING_INNER_Y_MM, color='green', linestyle='--', alpha=0.5, label=f'Y threshold ({TRAINING_INNER_Y_MM}mm)')
    
    # Add clamp radius boundaries
    for ax in [ax_x, ax_y]:
        ax.axhline(y=CLAMP_RADIUS, color='gray', linestyle=':', alpha=0.4)
        ax.axhline(y=-CLAMP_RADIUS, color='gray', linestyle=':', alpha=0.4)
    
    # Mark reward events
    for rt in reward_times:
        ax_x.axvline(x=rt, color='green', linewidth=1.5, alpha=0.6)
        ax_y.axvline(x=rt, color='green', linewidth=1.5, alpha=0.6)
    # Add a single legend entry for rewards if any exist
    if reward_times:
        ax_x.axvline(x=reward_times[0], color='green', linewidth=1.5, alpha=0.6, label=f'Reward ({len(reward_times)} total)')
    
    # Add legends
    for ax in [ax_x, ax_y]:
        handles, labels = ax.get_legend_handles_labels()
        if handles:
            ax.legend(loc='upper right', fontsize=9)
    
    fig.tight_layout()
    
    # Save
    save_dir = Path(output_dir) if output_dir else csv_path.parent
    save_dir.mkdir(parents=True, exist_ok=True)
    chart_path = save_dir / f"chart_{csv_path.stem}.png"
    fig.savefig(str(chart_path), dpi=150, bbox_inches='tight')
    plt_chart.close(fig)
    
    print(f"[CHART] Saved: {chart_path}")
    return str(chart_path)


if __name__ == "__main__":
    csv_path = Path(csv_location)
    if not csv_path.exists():
        print(f"[ERROR] CSV file not found: {csv_path}")
        sys.exit(1)

    print(f"\n{'='*80}")
    print(f"[CONVERT] Converting: {csv_path.name}")
    print(f"[CONVERT] FPS: {output_fps} | Playback: {playback_speed}x")
    if Training_trial:
        print(f"[CONVERT] Training: ON (Stage {Training_stage})")
    print(f"[CONVERT] Output dir: {output_save_dir}")
    print(f"{'='*80}")

    # Generate movement chart
    if generate_chart:
        generate_movement_chart(
            csv_filepath=csv_path,
            output_dir=output_save_dir,
            training_mode=Training_trial,
            training_stage=Training_stage,
        )

    # Generate replay video
    if generate_video:
        result = generate_trial_replay_video(
            csv_filepath=csv_path,
            output_fps=output_fps,
            training_mode=Training_trial,
            training_stage=Training_stage,
            output_dir=output_save_dir,
            playback_speed=playback_speed,
        )

        if result:
            mp4_path = Path(result)
            try:
                size_mb = mp4_path.stat().st_size / (1024 ** 2)
                size_str = f" | Size: {size_mb:.1f} MB"
            except OSError:
                size_str = ""
            print(f"\n{'='*80}")
            print(f"[DONE] {mp4_path}{size_str}")
            print(f"{'='*80}\n")
        else:
            print(f"[ERROR] Video conversion failed")
            sys.exit(1)

    if not generate_video and not generate_chart:
        print(f"[WARN] Both generate_video and generate_chart are False. Nothing to do.")

# Audio Recording Feature

This feature automatically records all meeting audio to disk so you can listen to it later from your Windows host.

## How It Works

- Audio is recorded using PulseAudio's `parecord` utility
- Recordings are saved as WAV files in `/audio_recordings` inside the container
- Each recording gets a timestamp in the filename (e.g., `meeting_audio_20250129_143022.wav`)
- Recordings start automatically when containers start and stop when they shut down

## Accessing Recordings from Windows

**✅ NEW: Direct Windows Access via Bind Mount!**

The `audio_recordings` directory is now mounted directly to your Windows filesystem. You can access recordings immediately without Docker Desktop.

### **Location on Windows:**
```
C:\Users\7Dev\attendee\audio_recordings\
```

### **Alternative Access Methods:**

1. **Windows Explorer**: Navigate directly to the folder above
2. **Docker Desktop**: Still works via Volumes → audio_recordings → Explore
3. **Command Line**: Use the management script below

## Usage

### List available recordings:
```bash
./manage_audio_recordings.sh list
```

### Play a recording:
```bash
./manage_audio_recordings.sh play meeting_audio_20250129_143022.wav
```

### Clean up old recordings (older than 7 days):
```bash
./manage_audio_recordings.sh cleanup
```

## Technical Details

- **Format**: 48kHz, 16-bit stereo WAV
- **Source**: Records from PulseAudio monitor (what you would hear)
- **Automatic**: Starts/stops with container lifecycle
- **Storage**: Files are written directly to Windows filesystem via bind mount

## Troubleshooting

If recordings aren't working:
1. Check that PulseAudio started successfully in container logs
2. Ensure the `personality` syscall is allowed in seccomp profile
3. Verify the `audio_recordings` directory exists in your project folder
4. Check Windows file permissions on the recordings directory

## Docker Compose Configuration

The `audio_recordings` directory is mounted as a **bind mount** to:
- `attendee-worker-local` (for bot audio)
- `attendee-webpage-streamer-local` (for webpage streaming audio)

Both containers will record audio simultaneously if running.

**Windows Path**: `C:\Users\7Dev\attendee\audio_recordings\`
**Container Path**: `/audio_recordings`

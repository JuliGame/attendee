#!/bin/bash

# Script to manage audio recordings from Docker containers
# Usage: ./manage_audio_recordings.sh [list|play <filename>|cleanup]

RECORDINGS_DIR="./audio_recordings"

case "${1:-list}" in
    "list")
        echo "Available audio recordings:"
        if [ -d "$RECORDINGS_DIR" ]; then
            ls -la "$RECORDINGS_DIR"/*.wav 2>/dev/null || echo "No WAV files found"
        else
            echo "Recordings directory not found"
        fi
        ;;

    "play")
        if [ -z "$2" ]; then
            echo "Usage: $0 play <filename>"
            exit 1
        fi

        FILE="$RECORDINGS_DIR/$2"
        if [ -f "$FILE" ]; then
            echo "Playing: $FILE"
            # Use ffplay if available, otherwise try aplay
            if command -v ffplay >/dev/null 2>&1; then
                ffplay -autoexit "$FILE"
            elif command -v aplay >/dev/null 2>&1; then
                aplay "$FILE"
            else
                echo "No audio player found. Install ffplay or aplay to play recordings."
            fi
        else
            echo "File not found: $FILE"
        fi
        ;;

    "cleanup")
        echo "Cleaning up old recordings (older than 7 days)..."
        if [ -d "$RECORDINGS_DIR" ]; then
            find "$RECORDINGS_DIR" -name "*.wav" -type f -mtime +7 -delete
            echo "Cleanup complete"
        else
            echo "Recordings directory not found"
        fi
        ;;

    *)
        echo "Usage: $0 [list|play <filename>|cleanup]"
        echo "  list    - List available recordings"
        echo "  play    - Play a specific recording"
        echo "  cleanup - Remove recordings older than 7 days"
        exit 1
        ;;
esac

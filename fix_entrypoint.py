#!/usr/bin/env python3
import os

# Read the file with Windows line endings
with open('entrypoint.sh', 'rb') as f:
    content = f.read()

# Convert CRLF to LF
content = content.replace(b'\r\n', b'\n')

# Write back with Unix line endings
with open('entrypoint.sh', 'wb') as f:
    f.write(content)

print("Fixed line endings in entrypoint.sh")

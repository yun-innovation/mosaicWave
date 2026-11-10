"""mosaicWave Python service."""

__version__ = "0.1.1"

# The API a client talks to, and the oldest mobile app build this server still answers correctly.
# Raise MIN_APP_VERSION only with a breaking change to /api/v1 (doc/process.md); the apps compare it
# with their own version and ask for an update instead of failing in odd ways (doc/mobile.md).
API_VERSION = "v1"
MIN_APP_VERSION = "0.1.0"

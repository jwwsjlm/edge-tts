"""Start the unified Edge TTS + Xiaomi MiMo HTTP service.

This is the local, non-Docker entry point.  The existing ``POST /v1/tts``
contract remains Edge-compatible when ``model`` is omitted; callers can select
MiMo with ``model=mimo-v2-tts`` in the same service.
"""

from edge_tts_server.cli import main

if __name__ == "__main__":
    main()

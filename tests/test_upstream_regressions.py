"""Regression checks for the integrated October 2026 upstream changes."""

import re
import subprocess
import sys

from edge_tts.constants import SEC_MS_GEC_VERSION
from edge_tts.drm import DRM, generate_sec_ms_gec_v2


def test_v2_token_uses_five_minute_time_buckets() -> None:
    """The protocol version and token must advance together at bucket boundaries."""
    assert SEC_MS_GEC_VERSION.startswith("2-")
    token = generate_sec_ms_gec_v2(0)
    assert re.fullmatch(r"[0-9A-F]{16}-[0-9A-F]{64}", token)
    assert token == generate_sec_ms_gec_v2(299)
    assert token != generate_sec_ms_gec_v2(300)


def test_http_date_parsing_does_not_use_locale_strptime() -> None:
    """English HTTP dates and obsolete RFC dates have the same absolute timestamp."""
    for value in (
        "Sun, 06 Nov 1994 08:49:37 GMT",
        "Sunday, 06-Nov-94 08:49:37 GMT",
        "Sun Nov  6 08:49:37 1994",
    ):
        assert DRM.parse_rfc2616_date(value) == 784111777.0
    assert DRM.parse_rfc2616_date("not a date") is None


def test_sync_stream_propagates_errors_without_hanging() -> None:
    """A bounded subprocess catches a regression that would otherwise hang pytest."""
    script = """
from edge_tts import Communicate
async def broken(self):
    yield {'type': 'audio', 'data': b'first'}
    raise RuntimeError('upstream unavailable')
Communicate.stream = broken
stream = Communicate('hello').stream_sync()
assert next(stream)['data'] == b'first'
try:
    next(stream)
except RuntimeError as exc:
    assert str(exc) == 'upstream unavailable'
else:
    raise AssertionError('lost upstream exception')
"""
    subprocess.run([sys.executable, "-c", script], check=True, timeout=15)

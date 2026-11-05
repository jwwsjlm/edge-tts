"""Start an extracted bundle without site packages or external network access."""

import argparse
import json
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path


def main() -> None:
    """Verify that the shipped runtime can boot in an isolated interpreter."""
    parser = argparse.ArgumentParser()
    parser.add_argument("bundle", type=Path)
    args = parser.parse_args()
    bundle = args.bundle.resolve()
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    with tempfile.TemporaryDirectory(prefix="tts-smoke-") as temporary:
        config = Path(temporary) / "config.yaml"
        config.write_text(
            f'api_key: "smoke-test"\nhost: "127.0.0.1"\nport: {port}\n',
            encoding="utf-8",
        )
        launcher = """
import runpy, socket, sys
def deny_external(event, args):
    if event == 'socket.getaddrinfo' and args[0] not in ('127.0.0.1', 'localhost', '::1'):
        raise RuntimeError('External network is disabled in bundle smoke tests')
    if event == 'socket.connect' and args[1][0] not in ('127.0.0.1', '::1'):
        raise RuntimeError('External network is disabled in bundle smoke tests')
sys.addaudithook(deny_external)
entry = sys.argv.pop(1)
sys.argv[0] = entry
runpy.run_path(entry, run_name='__main__')
"""
        process = subprocess.Popen(
            [
                sys.executable,
                "-I",
                "-S",
                "-c",
                launcher,
                str(bundle / "run.py"),
                "--config",
                str(config),
            ],
            cwd=temporary,
        )
        try:
            for _ in range(80):
                if process.poll() is not None:
                    raise RuntimeError("Bundle exited before becoming healthy")
                try:
                    with urllib.request.urlopen(
                        f"http://127.0.0.1:{port}/health", timeout=1
                    ) as response:
                        assert json.load(response) == {"status": "ok"}
                    print("Isolated bundle smoke test passed")
                    return
                except (urllib.error.URLError, TimeoutError):
                    time.sleep(0.25)
            raise RuntimeError("Bundle health check timed out")
        finally:
            process.terminate()
            process.wait(timeout=15)


if __name__ == "__main__":
    main()

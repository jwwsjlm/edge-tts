from setuptools import setup

# Metadata goes in setup.cfg. These are here for GitHub's dependency graph.
setup(
    name="edge-tts",
    install_requires=[
        "aiohttp>=3.8.0,<4.0.0",
        "certifi>=2023.11.17",
        "fastapi>=0.115,<1.0",
        "imageio-ffmpeg>=0.6,<1.0",
        "pydantic>=2.8,<3.0",
        "PyYAML>=6.0,<7.0",
        "tabulate>=0.4.4,<1.0.0",
        "typing-extensions>=4.1.0,<5.0.0",
        "uvicorn>=0.30,<1.0",
    ],
)

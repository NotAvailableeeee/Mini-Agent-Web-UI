"""Web backend configuration."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass
class WebConfig:
    """Static configuration for the web backend.

    Most settings (model, api_key, etc.) still come from the user's existing
    ``~/.mini-agent/config/config.yaml`` — this dataclass only holds the
    web-specific knobs.
    """

    host: str = "127.0.0.1"
    port: int = 8000

    # Default workspace directory for Agent file operations.
    # Resolved relative to this config file's location so it always points at
    # ``<repo>/workspace`` regardless of the process working directory.
    # Shared with the CLI — keeps web-generated artefacts alongside CLI ones.
    workspace_dir: Path = Path(__file__).resolve().parent.parent.parent / "workspace"

    # CORS origin(s) for the Vite dev server. Comma-separated in env if needed.
    dev_frontend_origins: tuple[str, ...] = (
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    )


def load_web_config() -> WebConfig:
    """Build a WebConfig. Later this can read from env vars / yaml."""
    return WebConfig()
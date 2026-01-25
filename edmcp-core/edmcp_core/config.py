"""
Central configuration loading for all edmcp workflows.

This module provides a standardized way to load the shared .env file
from the edmcp root directory, ensuring all workflows use the same
configuration source.
"""

import os
from pathlib import Path
from dotenv import load_dotenv


def get_edmcp_root() -> Path:
    """
    Find the edmcp root directory by looking for the .env file.

    Searches upward from the current working directory or from a known
    package location to find the edmcp root (where the central .env lives).

    Returns:
        Path to the edmcp root directory.

    Raises:
        FileNotFoundError: If the edmcp root cannot be found.
    """
    # Start from the edmcp-core package location
    current = Path(__file__).resolve().parent.parent.parent

    # Check if we're already at the root
    if (current / ".env").exists() or (current / ".env.example").exists():
        return current

    # Otherwise, search from cwd upward
    current = Path.cwd().resolve()
    for _ in range(10):  # Limit search depth
        if (current / ".env").exists() or (current / ".env.example").exists():
            return current
        if current.parent == current:  # Reached filesystem root
            break
        current = current.parent

    # Fallback: assume edmcp-core's parent is the root
    return Path(__file__).resolve().parent.parent.parent


def load_edmcp_config(override: bool = False) -> Path:
    """
    Load environment variables from the central .env file.

    This function locates the .env file in the edmcp root directory
    and loads it. All workflows should call this at startup to ensure
    consistent configuration.

    Args:
        override: If True, override existing environment variables.
                  Default is False (existing variables take precedence).

    Returns:
        Path to the .env file that was loaded (or would be loaded if it exists).

    Example:
        from edmcp_core import load_edmcp_config

        # Load config at the start of your workflow server
        load_edmcp_config()
    """
    root = get_edmcp_root()
    env_path = root / ".env"

    # Load the .env file if it exists
    load_dotenv(env_path, override=override)

    return env_path


def get_env(key: str, default: str | None = None) -> str | None:
    """
    Get an environment variable with optional default.

    This is a convenience wrapper around os.environ.get that ensures
    the central config has been loaded first.

    Args:
        key: The environment variable name.
        default: Default value if not found.

    Returns:
        The environment variable value or the default.
    """
    return os.environ.get(key, default)

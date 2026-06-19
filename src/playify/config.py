"""Persistent settings manager for Playify."""

import json
import os
from pathlib import Path
from typing import Any

# Default settings
DEFAULT_SETTINGS = {
    "bot_status_text": "",
    "bot_status_type": 0,  # discord.ActivityType.playing
    "controller_idle_image": "https://i.imgur.com/vDusBWD.png",
}

class SettingsManager:
    """Manages persistent settings in data/settings.json."""

    def __init__(self, project_root: Path):
        self.data_dir = project_root / "data"
        self.settings_file = self.data_dir / "settings.json"
        
        # Ensure data directory exists
        self.data_dir.mkdir(parents=True, exist_ok=True)
        
        self.settings = self._load()

    def _load(self) -> dict[str, Any]:
        """Load settings from file, falling back to defaults."""
        if not self.settings_file.exists():
            return DEFAULT_SETTINGS.copy()
            
        try:
            with open(self.settings_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                
            # Merge with defaults to ensure all keys exist
            settings = DEFAULT_SETTINGS.copy()
            settings.update(data)
            return settings
        except Exception as e:
            import logging
            logging.getLogger("playify.config").error(f"Failed to load settings: {e}")
            return DEFAULT_SETTINGS.copy()

    def save(self) -> None:
        """Save current settings to file."""
        try:
            with open(self.settings_file, "w", encoding="utf-8") as f:
                json.dump(self.settings, f, indent=4)
        except Exception as e:
            import logging
            logging.getLogger("playify.config").error(f"Failed to save settings: {e}")

    def reload(self) -> None:
        """Reload settings from disk."""
        self.settings = self._load()

    def get(self, key: str, default: Any = None) -> Any:
        """Get a setting value, with an optional fallback."""
        val = self.settings.get(key)
        if val is not None:
            return val
        if default is not None:
            return default
        return DEFAULT_SETTINGS.get(key)

    def set(self, key: str, value: Any) -> None:
        """Set a setting value and save."""
        self.settings[key] = value
        self.save()

# Global settings instance will be initialized by core.py or wherever needed

"""
Configuration Manager
Handles loading and accessing configuration from YAML files.
"""

import os
import yaml
import logging
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


class ConfigManager:
    """Manages application configuration from YAML files."""
    
    _instance: Optional['ConfigManager'] = None
    _config: dict = {}
    
    def __new__(cls):
        """Singleton pattern to ensure only one config instance."""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self):
        if not self._config:
            self.load_config()
    
    def load_config(self, config_path: Optional[str] = None) -> None:
        """Load configuration from YAML file."""
        if config_path is None:
            # Look for config in standard locations
            base_dir = Path(__file__).parent.parent
            possible_paths = [
                base_dir / "config" / "config.local.yaml",  # User overrides
                base_dir / "config" / "config.yaml",        # Default config
                Path.home() / ".photoframe" / "config.yaml", # Home directory
            ]
            
            for path in possible_paths:
                if path.exists():
                    config_path = str(path)
                    break
            else:
                logger.warning("No config file found, using defaults")
                self._config = self._get_defaults()
                return
        
        try:
            with open(config_path, 'r') as f:
                self._config = yaml.safe_load(f)
            logger.info(f"Loaded config from {config_path}")
        except Exception as e:
            logger.error(f"Error loading config: {e}")
            self._config = self._get_defaults()
    
    def _get_defaults(self) -> dict:
        """Return default configuration values."""
        return {
            'icloud': {
                'username': '',
                'album': 'Photo Frame',
                'download_dir': '~/photos/raw',
                'sync_interval': 86400,
                'recent_days': 0,
                'max_photos': 500,
            },
            'display': {
                'model': '7in3e',
                'width': 800,
                'height': 480,
                'orientation': 'landscape',
                'rotation_interval': 3600,
                'selection_strategy': 'hybrid',
                'hybrid_newest_percent': 70,
            },
            'button': {
                'gpio_pin': 4,
                'debounce_ms': 300,
                'enabled': True,
            },
            'battery': {
                'enabled': True,
                'i2c_address': 0x43,
                'min_refresh_percent': 10,
                'low_warning_percent': 20,
            },
            'web': {
                'enabled': True,
                'port': 8080,
                'host': '0.0.0.0',
                'auth_enabled': False,
                'auth_username': 'admin',
                'auth_password': 'photoframe',
            },
            'notifications': {
                'enabled': True,
                'pushover': {'enabled': False},
                'webhook': {'enabled': False},
                'local': {'enabled': True},
            },
            'logging': {
                'level': 'INFO',
                'file': '/var/log/photoframe/photoframe.log',
                'max_size_mb': 10,
                'backup_count': 3,
            },
            'paths': {
                'ready_dir': '~/photos/ready',
                'lib_dir': './lib',
                'state_file': '~/.photoframe/state.json',
            },
        }
    
    def get(self, key: str, default: Any = None) -> Any:
        """
        Get a configuration value using dot notation.
        
        Example: config.get('display.width') returns 800
        """
        keys = key.split('.')
        value = self._config
        
        try:
            for k in keys:
                value = value[k]
            return value
        except (KeyError, TypeError):
            return default
    
    def get_path(self, key: str) -> Path:
        """Get a path configuration value, expanding ~ and making absolute."""
        path_str = self.get(key, '')
        if not path_str:
            return Path()
        return Path(os.path.expanduser(path_str)).resolve()
    
    def set(self, key: str, value: Any) -> None:
        """Set a configuration value using dot notation."""
        keys = key.split('.')
        config = self._config
        
        for k in keys[:-1]:
            if k not in config:
                config[k] = {}
            config = config[k]
        
        config[keys[-1]] = value
    
    def save(self, config_path: Optional[str] = None) -> None:
        """Save current configuration to file."""
        if config_path is None:
            config_path = Path.home() / ".photoframe" / "config.yaml"
        
        config_path = Path(config_path)
        config_path.parent.mkdir(parents=True, exist_ok=True)
        
        with open(config_path, 'w') as f:
            yaml.dump(self._config, f, default_flow_style=False)
        
        logger.info(f"Saved config to {config_path}")
    
    @property
    def config(self) -> dict:
        """Return the full configuration dictionary."""
        return self._config


# Global config instance
config = ConfigManager()

"""
Photo Selector Module
Implements hybrid selection algorithm: prioritize newest photos with some older ones mixed in.
"""

import os
import json
import random
import logging
from pathlib import Path
from datetime import datetime
from typing import Optional, List, Dict, Set
from collections import deque

from .config_manager import config

logger = logging.getLogger(__name__)


class PhotoSelector:
    """Selects photos using configurable strategies."""
    
    def __init__(self):
        self.strategy = config.get('display.selection_strategy', 'hybrid')
        self.newest_percent = config.get('display.hybrid_newest_percent', 70)
        self.ready_dir = config.get_path('paths.ready_dir')
        
        # State tracking
        self._state_file = config.get_path('paths.state_file')
        self._state: Dict = {}
        self._recently_shown: deque = deque(maxlen=50)  # Track last 50 shown
        self._current_index = 0
        self._history: List[str] = []  # Full history for previous navigation
        self._history_position = -1  # Current position in history
        self._paused = False  # Pause auto-rotation
        self._skipped: Set[str] = set()  # Temporarily skipped photos
        self._favorites: Set[str] = set()  # Favorited photos
        
        self._load_state()
    
    def _load_state(self) -> None:
        """Load selector state from disk."""
        if self._state_file.exists():
            try:
                with open(self._state_file, 'r') as f:
                    self._state = json.load(f)
                    self._recently_shown = deque(
                        self._state.get('recently_shown', []),
                        maxlen=50
                    )
                    self._current_index = self._state.get('current_index', 0)
                    self._history = self._state.get('history', [])
                    self._history_position = self._state.get('history_position', -1)
                    self._paused = self._state.get('paused', False)
                    self._skipped = set(self._state.get('skipped', []))
                    self._favorites = set(self._state.get('favorites', []))
            except Exception as e:
                logger.warning(f"Could not load state: {e}")
                self._state = {}
        else:
            # Ensure state directory exists
            self._state_file.parent.mkdir(parents=True, exist_ok=True)
    
    def _save_state(self) -> None:
        """Save selector state to disk."""
        try:
            self._state['recently_shown'] = list(self._recently_shown)
            self._state['current_index'] = self._current_index
            self._state['history'] = self._history[-100:]  # Keep last 100
            self._state['history_position'] = self._history_position
            self._state['paused'] = self._paused
            self._state['skipped'] = list(self._skipped)
            self._state['favorites'] = list(self._favorites)
            self._state['last_updated'] = datetime.now().isoformat()
            
            with open(self._state_file, 'w') as f:
                json.dump(self._state, f, indent=2)
        except Exception as e:
            logger.warning(f"Could not save state: {e}")
    
    def _get_all_photos(self) -> List[Path]:
        """Get all ready photos sorted by modification time (newest first)."""
        if not self.ready_dir.exists():
            return []
        
        photos = list(self.ready_dir.glob('*.png'))
        # Sort by modification time, newest first
        photos.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        return photos
    
    def select_next(self, force: bool = False) -> Optional[Path]:
        """
        Select the next photo to display based on configured strategy.
        
        Args:
            force: If True, select even if paused (for manual next)
        
        Returns:
            Path to selected photo, or None if no photos available
        """
        photos = self._get_all_photos()
        
        if not photos:
            logger.warning("No photos available for selection")
            return None
        
        # Filter out skipped photos
        available_photos = [p for p in photos if str(p) not in self._skipped]
        if not available_photos:
            # All photos skipped, clear and use all
            self._skipped.clear()
            available_photos = photos
        
        selected = None
        
        if self.strategy == 'sequential':
            selected = self._select_sequential(available_photos)
        elif self.strategy == 'random':
            selected = self._select_random(available_photos)
        else:  # hybrid (default)
            selected = self._select_hybrid(available_photos)
        
        if selected:
            # Track recently shown and history
            self._recently_shown.append(str(selected))
            self._history.append(str(selected))
            self._history_position = len(self._history) - 1
            self._save_state()
            logger.info(f"Selected photo: {selected.name}")
        
        return selected
    
    def _select_sequential(self, photos: List[Path]) -> Optional[Path]:
        """Select photos in sequential order (newest to oldest)."""
        if not photos:
            return None
        
        # Wrap around if needed
        if self._current_index >= len(photos):
            self._current_index = 0
        
        selected = photos[self._current_index]
        self._current_index += 1
        
        return selected
    
    def _select_random(self, photos: List[Path]) -> Optional[Path]:
        """Select a random photo, avoiding recent repeats."""
        if not photos:
            return None
        
        # Filter out recently shown
        available = [p for p in photos if str(p) not in self._recently_shown]
        
        # If all photos were recently shown, use full list
        if not available:
            available = photos
            self._recently_shown.clear()
        
        return random.choice(available)
    
    def _select_hybrid(self, photos: List[Path]) -> Optional[Path]:
        """
        Hybrid selection: prioritize newest photos with some older ones mixed in.
        
        Split photos into "newest" (top X%) and "oldest" (rest).
        With configured probability, pick from newest; otherwise from oldest.
        """
        if not photos:
            return None
        
        # Calculate split point
        newest_count = max(1, int(len(photos) * (self.newest_percent / 100)))
        
        newest_photos = photos[:newest_count]
        oldest_photos = photos[newest_count:] if len(photos) > newest_count else []
        
        # Filter out recently shown from both pools
        available_newest = [p for p in newest_photos if str(p) not in self._recently_shown]
        available_oldest = [p for p in oldest_photos if str(p) not in self._recently_shown]
        
        # If pools are exhausted, reset
        if not available_newest:
            available_newest = newest_photos
        if not available_oldest and oldest_photos:
            available_oldest = oldest_photos
        
        # Decide which pool to pick from
        use_newest = random.random() < (self.newest_percent / 100)
        
        if use_newest or not available_oldest:
            selected = random.choice(available_newest) if available_newest else None
        else:
            selected = random.choice(available_oldest)
        
        return selected
    
    def get_current_photo(self) -> Optional[Path]:
        """Get the most recently shown photo."""
        if self._history and self._history_position >= 0:
            try:
                recent_path = Path(self._history[self._history_position])
                if recent_path.exists():
                    return recent_path
            except IndexError:
                pass
        
        if self._recently_shown:
            recent_path = Path(self._recently_shown[-1])
            if recent_path.exists():
                return recent_path
        return None
    
    def select_specific(self, photo_path: Path) -> Optional[Path]:
        """Display a specific photo by path."""
        if not photo_path.exists():
            logger.warning(f"Photo not found: {photo_path}")
            return None
        
        # Add to history
        self._history.append(str(photo_path))
        self._history_position = len(self._history) - 1
        self._recently_shown.append(str(photo_path))
        self._save_state()
        
        logger.info(f"Selected specific photo: {photo_path.name}")
        return photo_path
    
    def select_previous(self) -> Optional[Path]:
        """Go back to the previous photo in history."""
        if not self._history or self._history_position <= 0:
            logger.info("No previous photo in history")
            return None
        
        self._history_position -= 1
        photo_path = Path(self._history[self._history_position])
        
        if photo_path.exists():
            logger.info(f"Selected previous: {photo_path.name}")
            self._save_state()
            return photo_path
        else:
            # Photo was deleted, try the one before
            return self.select_previous()
    
    def skip_current(self) -> Optional[Path]:
        """Skip current photo and mark it to not show for a while."""
        current = self.get_current_photo()
        
        if current:
            self._skipped.add(str(current))
            logger.info(f"Skipped: {current.name}")
        
        # Try to get a different photo
        for _ in range(5):  # Max 5 attempts
            selected = self.select_next()
            if selected and selected != current:
                return selected
        
        return self.select_next()  # Fallback
    
    def toggle_favorite(self, photo_path: Optional[Path] = None) -> bool:
        """Toggle favorite status for a photo."""
        if photo_path is None:
            photo_path = self.get_current_photo()
        
        if not photo_path:
            return False
        
        path_str = str(photo_path)
        
        if path_str in self._favorites:
            self._favorites.discard(path_str)
            logger.info(f"Unfavorited: {photo_path.name}")
            is_favorite = False
        else:
            self._favorites.add(path_str)
            logger.info(f"Favorited: {photo_path.name}")
            is_favorite = True
        
        self._save_state()
        return is_favorite
    
    def is_favorite(self, photo_path: Optional[Path] = None) -> bool:
        """Check if photo is favorited."""
        if photo_path is None:
            photo_path = self.get_current_photo()
        return str(photo_path) in self._favorites if photo_path else False
    
    def get_favorites(self) -> List[Path]:
        """Get list of favorited photos."""
        return [Path(p) for p in self._favorites if Path(p).exists()]
    
    def pause(self) -> None:
        """Pause automatic rotation."""
        self._paused = True
        self._save_state()
        logger.info("Auto-rotation paused")
    
    def resume(self) -> None:
        """Resume automatic rotation."""
        self._paused = False
        self._save_state()
        logger.info("Auto-rotation resumed")
    
    def toggle_pause(self) -> bool:
        """Toggle pause state, returns new paused status."""
        if self._paused:
            self.resume()
        else:
            self.pause()
        return self._paused
    
    @property
    def is_paused(self) -> bool:
        """Check if auto-rotation is paused."""
        return self._paused
    
    def clear_skipped(self) -> int:
        """Clear all skipped photos."""
        count = len(self._skipped)
        self._skipped.clear()
        self._save_state()
        logger.info(f"Cleared {count} skipped photos")
        return count
    
    def get_stats(self) -> dict:
        """Get selector statistics for web UI."""
        photos = self._get_all_photos()
        newest_count = max(1, int(len(photos) * (self.newest_percent / 100)))
        current = self.get_current_photo()
        
        return {
            'strategy': self.strategy,
            'total_photos': len(photos),
            'newest_pool': newest_count,
            'oldest_pool': max(0, len(photos) - newest_count),
            'recently_shown_count': len(self._recently_shown),
            'current_index': self._current_index,
            'newest_percent': self.newest_percent,
            'paused': self._paused,
            'history_length': len(self._history),
            'history_position': self._history_position,
            'can_go_previous': self._history_position > 0,
            'skipped_count': len(self._skipped),
            'favorites_count': len(self._favorites),
            'current_photo': current.name if current else None,
            'current_is_favorite': self.is_favorite(current),
        }
    
    def reset(self) -> None:
        """Reset selector state."""
        self._recently_shown.clear()
        self._current_index = 0
        self._history.clear()
        self._history_position = -1
        self._skipped.clear()
        # Note: favorites are preserved
        self._save_state()
        logger.info("Photo selector state reset")


# Global selector instance
photo_selector = PhotoSelector()

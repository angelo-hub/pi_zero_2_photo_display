"""
iCloud Photo Sync Module
Handles downloading photos from iCloud shared albums using icloudpd.
"""

import os
import subprocess
import logging
import json
import shutil
from pathlib import Path
from datetime import datetime
from typing import Optional, Tuple, List
from enum import Enum

from .config_manager import config

logger = logging.getLogger(__name__)


class AuthStatus(Enum):
    """iCloud authentication status."""
    AUTHENTICATED = "authenticated"
    REQUIRES_2FA = "requires_2fa"
    INVALID_CREDENTIALS = "invalid_credentials"
    UNKNOWN_ERROR = "unknown_error"
    NOT_CONFIGURED = "not_configured"


class ICloudSync:
    """Manages iCloud photo synchronization."""
    
    def __init__(self):
        self.username = config.get('icloud.username', '')
        self.album = config.get('icloud.album', 'Photo Frame')
        self.download_dir = config.get_path('icloud.download_dir')
        self.max_photos = config.get('icloud.max_photos', 500)
        self.recent_days = config.get('icloud.recent_days', 0)
        
        # Cookie/session directory for icloudpd
        self.cookie_dir = Path.home() / '.pyicloud'
        
        # Status tracking
        self._auth_status = AuthStatus.NOT_CONFIGURED
        self._last_sync: Optional[datetime] = None
        self._last_error: Optional[str] = None
        self._photos_downloaded = 0
        
        # Ensure directories exist
        self.download_dir.mkdir(parents=True, exist_ok=True)
        self.cookie_dir.mkdir(parents=True, exist_ok=True)
    
    @property
    def auth_status(self) -> AuthStatus:
        """Current authentication status."""
        return self._auth_status
    
    @property
    def last_sync(self) -> Optional[datetime]:
        """Timestamp of last successful sync."""
        return self._last_sync
    
    @property
    def last_error(self) -> Optional[str]:
        """Last error message if any."""
        return self._last_error
    
    @property
    def is_configured(self) -> bool:
        """Check if iCloud credentials are configured."""
        return bool(self.username)
    
    def check_auth_status(self) -> AuthStatus:
        """Check current iCloud authentication status."""
        if not self.is_configured:
            self._auth_status = AuthStatus.NOT_CONFIGURED
            return self._auth_status
        
        try:
            # Try a dry-run to check auth status
            result = subprocess.run(
                [
                    'icloudpd',
                    '--username', self.username,
                    '--directory', str(self.download_dir),
                    '--cookie-directory', str(self.cookie_dir),
                    '--dry-run',
                    '--recent', '1',
                ],
                capture_output=True,
                text=True,
                timeout=60
            )
            
            output = result.stdout + result.stderr
            
            if 'Two-step authentication required' in output or \
               'Two-factor authentication required' in output or \
               'Please enter' in output:
                self._auth_status = AuthStatus.REQUIRES_2FA
            elif 'Invalid email/password' in output or \
                 'authentication failed' in output.lower():
                self._auth_status = AuthStatus.INVALID_CREDENTIALS
            elif result.returncode == 0:
                self._auth_status = AuthStatus.AUTHENTICATED
            else:
                self._auth_status = AuthStatus.UNKNOWN_ERROR
                self._last_error = output[:500]  # Truncate long errors
                
        except subprocess.TimeoutExpired:
            self._auth_status = AuthStatus.UNKNOWN_ERROR
            self._last_error = "Authentication check timed out"
        except FileNotFoundError:
            self._auth_status = AuthStatus.UNKNOWN_ERROR
            self._last_error = "icloudpd not installed. Run: pip install icloudpd"
        except Exception as e:
            self._auth_status = AuthStatus.UNKNOWN_ERROR
            self._last_error = str(e)
        
        return self._auth_status
    
    def authenticate(self, password: str, mfa_code: Optional[str] = None) -> Tuple[bool, str]:
        """
        Authenticate with iCloud.
        
        Args:
            password: iCloud password
            mfa_code: Optional 2FA code if required
            
        Returns:
            Tuple of (success, message)
        """
        if not self.is_configured:
            return False, "iCloud username not configured"
        
        try:
            # Build command
            cmd = [
                'icloudpd',
                '--username', self.username,
                '--password', password,
                '--directory', str(self.download_dir),
                '--cookie-directory', str(self.cookie_dir),
                '--auth-only',
            ]
            
            # If MFA code provided, we need to handle it differently
            # icloudpd reads MFA from stdin
            input_data = None
            if mfa_code:
                input_data = mfa_code + '\n'
            
            result = subprocess.run(
                cmd,
                input=input_data,
                capture_output=True,
                text=True,
                timeout=120
            )
            
            output = result.stdout + result.stderr
            
            if 'Two-step authentication required' in output or \
               'Two-factor authentication required' in output:
                self._auth_status = AuthStatus.REQUIRES_2FA
                return False, "2FA code required"
            elif 'Invalid email/password' in output:
                self._auth_status = AuthStatus.INVALID_CREDENTIALS
                return False, "Invalid email or password"
            elif 'Authentication successful' in output or result.returncode == 0:
                self._auth_status = AuthStatus.AUTHENTICATED
                return True, "Authentication successful"
            else:
                self._auth_status = AuthStatus.UNKNOWN_ERROR
                self._last_error = output[:500]
                return False, f"Authentication failed: {output[:200]}"
                
        except subprocess.TimeoutExpired:
            return False, "Authentication timed out"
        except Exception as e:
            self._last_error = str(e)
            return False, f"Authentication error: {e}"
    
    def sync_photos(self) -> Tuple[bool, int, str]:
        """
        Sync photos from iCloud shared album.
        
        Returns:
            Tuple of (success, photos_downloaded, message)
        """
        if not self.is_configured:
            return False, 0, "iCloud not configured"
        
        if self._auth_status != AuthStatus.AUTHENTICATED:
            # Check auth status first
            self.check_auth_status()
            if self._auth_status != AuthStatus.AUTHENTICATED:
                return False, 0, f"Authentication required: {self._auth_status.value}"
        
        try:
            # Count files before sync
            files_before = len(list(self.download_dir.glob('**/*.*')))
            
            # Build sync command
            cmd = [
                'icloudpd',
                '--username', self.username,
                '--directory', str(self.download_dir),
                '--cookie-directory', str(self.cookie_dir),
                '--album', self.album,
                '--auto-delete',  # Remove photos deleted from album
                '--no-progress-bar',
            ]
            
            # Add recent filter if configured
            if self.recent_days > 0:
                cmd.extend(['--recent', str(self.recent_days)])
            
            logger.info(f"Starting iCloud sync for album: {self.album}")
            
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=3600  # 1 hour timeout for large syncs
            )
            
            output = result.stdout + result.stderr
            
            # Check for auth errors
            if 'Two-step authentication required' in output or \
               'Two-factor authentication required' in output:
                self._auth_status = AuthStatus.REQUIRES_2FA
                return False, 0, "Re-authentication required (2FA)"
            
            # Count files after sync
            files_after = len(list(self.download_dir.glob('**/*.*')))
            photos_downloaded = max(0, files_after - files_before)
            
            self._photos_downloaded = photos_downloaded
            self._last_sync = datetime.now()
            
            # Enforce max photos limit
            if self.max_photos > 0:
                self._enforce_max_photos()
            
            logger.info(f"Sync complete. Downloaded {photos_downloaded} new photos.")
            return True, photos_downloaded, f"Synced {photos_downloaded} new photos"
            
        except subprocess.TimeoutExpired:
            self._last_error = "Sync timed out after 1 hour"
            return False, 0, self._last_error
        except Exception as e:
            self._last_error = str(e)
            logger.error(f"Sync error: {e}")
            return False, 0, f"Sync error: {e}"
    
    def _enforce_max_photos(self) -> None:
        """Remove oldest photos if over the max limit."""
        if self.max_photos <= 0:
            return
        
        # Get all image files sorted by modification time
        image_extensions = {'.jpg', '.jpeg', '.png', '.heic', '.gif'}
        all_photos = []
        
        for ext in image_extensions:
            all_photos.extend(self.download_dir.glob(f'**/*{ext}'))
            all_photos.extend(self.download_dir.glob(f'**/*{ext.upper()}'))
        
        # Sort by modification time (oldest first)
        all_photos.sort(key=lambda p: p.stat().st_mtime)
        
        # Remove oldest photos if over limit
        if len(all_photos) > self.max_photos:
            photos_to_remove = len(all_photos) - self.max_photos
            for photo in all_photos[:photos_to_remove]:
                try:
                    photo.unlink()
                    logger.debug(f"Removed old photo: {photo.name}")
                except Exception as e:
                    logger.warning(f"Could not remove {photo}: {e}")
    
    def get_downloaded_photos(self) -> List[Path]:
        """Get list of all downloaded photos."""
        image_extensions = {'.jpg', '.jpeg', '.png', '.heic', '.gif'}
        photos = []
        
        for ext in image_extensions:
            photos.extend(self.download_dir.glob(f'**/*{ext}'))
            photos.extend(self.download_dir.glob(f'**/*{ext.upper()}'))
        
        return sorted(photos, key=lambda p: p.stat().st_mtime, reverse=True)
    
    def get_status(self) -> dict:
        """Get current sync status for web UI."""
        return {
            'configured': self.is_configured,
            'username': self.username,
            'album': self.album,
            'auth_status': self._auth_status.value,
            'last_sync': self._last_sync.isoformat() if self._last_sync else None,
            'last_error': self._last_error,
            'photos_count': len(self.get_downloaded_photos()),
            'download_dir': str(self.download_dir),
        }


# Global sync instance
icloud_sync = ICloudSync()

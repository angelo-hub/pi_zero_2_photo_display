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
        
        # Find icloudpd binary (in venv or system)
        self._icloudpd_bin = self._find_icloudpd()
        
        # Status tracking
        self._auth_status = AuthStatus.NOT_CONFIGURED
        self._last_sync: Optional[datetime] = None
        self._last_error: Optional[str] = None
        self._photos_downloaded = 0
        
        # Ensure directories exist
        self.download_dir.mkdir(parents=True, exist_ok=True)
        self.cookie_dir.mkdir(parents=True, exist_ok=True)
    
    def _find_icloudpd(self) -> str:
        """Find the icloudpd binary path."""
        # Check for pre-built binary first (recommended for Pi Zero 2 W)
        binary_paths = [
            Path(__file__).parent.parent / 'bin' / 'icloudpd',
            Path.home() / 'photoframe' / 'bin' / 'icloudpd',
        ]
        
        for bin_path in binary_paths:
            if bin_path.exists():
                logger.info(f"Found icloudpd binary at: {bin_path}")
                return str(bin_path)
        
        # Check in virtual environment (pip install)
        venv_paths = [
            Path(__file__).parent.parent / 'venv' / 'bin' / 'icloudpd',
            Path.home() / 'photoframe' / 'venv' / 'bin' / 'icloudpd',
        ]
        
        for venv_path in venv_paths:
            if venv_path.exists():
                logger.info(f"Found icloudpd at: {venv_path}")
                return str(venv_path)
        
        # Check if it's in system PATH
        result = shutil.which('icloudpd')
        if result:
            logger.info(f"Found icloudpd in PATH: {result}")
            return result
        
        # Default to just 'icloudpd' and hope for the best
        logger.warning("icloudpd not found in bin/, venv, or PATH")
        return 'icloudpd'
    
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
    
    def _has_valid_cookies(self) -> bool:
        """Check if iCloud session cookies exist."""
        import re
        import time
        
        # icloudpd sanitizes usernames for filenames (removes . and @)
        sanitized_username = re.sub(r'[^a-zA-Z0-9]', '', self.username.lower())
        
        # Check both sanitized and original username patterns
        possible_files = [
            self.cookie_dir / sanitized_username,
            self.cookie_dir / f"{sanitized_username}.session",
            self.cookie_dir / self.username,
            self.cookie_dir / self.username.replace('@', '').replace('.', ''),
        ]
        
        for session_file in possible_files:
            if session_file.exists():
                # Check if cookie file is recent (less than 30 days old)
                age_days = (time.time() - session_file.stat().st_mtime) / 86400
                if age_days < 30:
                    logger.debug(f"Found valid session cookie: {session_file}")
                    return True
        
        logger.debug(f"No valid cookies found for {self.username}")
        return False
    
    def _sanitize_error(self, error: str) -> str:
        """Remove sensitive information from error messages."""
        import re
        
        # Remove tracebacks
        error = re.sub(r'Traceback \(most recent call last\):.*?(?=\n\S|\Z)', '', error, flags=re.DOTALL)
        error = re.sub(r'File ".*?", line \d+.*?\n', '', error)
        
        # Remove password prompts and values
        error = re.sub(r'iCloud Password for.*?:', '[password prompt]', error)
        error = re.sub(r'Password:.*', '[password prompt]', error)
        error = re.sub(r'password["\']?\s*[:=]\s*["\']?[^"\'}\s]+', 'password: [REDACTED]', error, flags=re.IGNORECASE)
        
        # Remove getpass warnings (not useful for users)
        error = re.sub(r'getpass\.py:\d+: GetPassWarning:.*?\n', '', error)
        error = re.sub(r'Warning: Password input may be echoed\.?\n?', '', error)
        error = re.sub(r'termios\.error:.*?\n?', '', error)
        
        # Clean up whitespace
        error = re.sub(r'\n\s*\n', '\n', error)
        error = error.strip()
        
        # Extract just the meaningful error message if possible
        if 'Invalid email/password' in error:
            return "Invalid email or password"
        if 'Two-factor authentication required' in error or 'Two-step authentication required' in error:
            return "2FA required - authenticate via SSH"
        
        return error if error else "Unknown error"
    
    def check_auth_status(self) -> AuthStatus:
        """Check current iCloud authentication status."""
        if not self.is_configured:
            self._auth_status = AuthStatus.NOT_CONFIGURED
            return self._auth_status
        
        # Check if icloudpd binary exists first
        if not Path(self._icloudpd_bin).exists() and shutil.which(self._icloudpd_bin) is None:
            self._auth_status = AuthStatus.UNKNOWN_ERROR
            self._last_error = f"icloudpd not found at '{self._icloudpd_bin}'. Run: source ~/photoframe/venv/bin/activate && pip install icloudpd"
            return self._auth_status
        
        # If no cookies exist, user needs to authenticate first
        if not self._has_valid_cookies():
            self._auth_status = AuthStatus.REQUIRES_2FA
            self._last_error = None
            logger.info("No valid session cookies found - authentication required")
            return self._auth_status
        
        try:
            # Try a dry-run to verify existing session is still valid
            # Use --no-progress-bar to avoid terminal issues
            result = subprocess.run(
                [
                    self._icloudpd_bin,
                    '--username', self.username,
                    '--directory', str(self.download_dir),
                    '--cookie-directory', str(self.cookie_dir),
                    '--dry-run',
                    '--recent', '1',
                    '--no-progress-bar',
                ],
                capture_output=True,
                text=True,
                timeout=60,
                stdin=subprocess.DEVNULL,  # Don't wait for input
            )
            
            output = result.stdout + result.stderr
            
            # Log raw output for debugging (at debug level to avoid noise)
            if output.strip():
                logger.debug(f"icloudpd auth check output: {output[:1000]}")
            
            # Sanitize output - remove sensitive info and tracebacks
            sanitized_output = self._sanitize_error(output)
            
            # Check for password prompt (means session expired)
            if 'Password' in output or 'getpass' in output or 'ioctl' in output or 'termios' in output:
                self._auth_status = AuthStatus.REQUIRES_2FA
                self._last_error = "Session expired - please authenticate via SSH first"
            elif 'Two-step authentication required' in output or \
               'Two-factor authentication required' in output or \
               'Please enter' in output:
                self._auth_status = AuthStatus.REQUIRES_2FA
                self._last_error = None
            elif 'Invalid email/password' in output or \
                 'authentication failed' in output.lower():
                self._auth_status = AuthStatus.INVALID_CREDENTIALS
                self._last_error = "Invalid credentials - check email and password"
            elif result.returncode == 0:
                self._auth_status = AuthStatus.AUTHENTICATED
                self._last_error = None
            else:
                # Check if it's just a password prompt issue
                if result.returncode != 0 and ('Password' in output or not output.strip()):
                    self._auth_status = AuthStatus.REQUIRES_2FA
                    self._last_error = "Session expired - please authenticate via SSH first"
                else:
                    self._auth_status = AuthStatus.UNKNOWN_ERROR
                    self._last_error = sanitized_output[:200] if sanitized_output else "Unknown error"
                    # Log the full error details for debugging
                    logger.error(f"iCloud auth check failed with unknown error. Return code: {result.returncode}")
                    logger.error(f"Raw stdout: {result.stdout[:500] if result.stdout else '(empty)'}")
                    logger.error(f"Raw stderr: {result.stderr[:500] if result.stderr else '(empty)'}")
                
        except subprocess.TimeoutExpired:
            self._auth_status = AuthStatus.UNKNOWN_ERROR
            self._last_error = "Authentication check timed out"
            logger.error("iCloud auth check timed out after 60 seconds")
        except FileNotFoundError:
            self._auth_status = AuthStatus.UNKNOWN_ERROR
            self._last_error = f"icloudpd not found at '{self._icloudpd_bin}'"
            logger.error(f"icloudpd binary not found at '{self._icloudpd_bin}'")
        except Exception as e:
            error_str = str(e)
            # Handle getpass errors gracefully - means we need to authenticate
            if 'getpass' in error_str or 'ioctl' in error_str or 'termios' in error_str:
                self._auth_status = AuthStatus.REQUIRES_2FA
                self._last_error = "Session expired - please authenticate via SSH first"
            else:
                self._auth_status = AuthStatus.UNKNOWN_ERROR
                self._last_error = self._sanitize_error(error_str)[:200]
                logger.exception(f"Unexpected error during iCloud auth check: {error_str}")
        
        return self._auth_status
    
    def authenticate(self, password: str, mfa_code: Optional[str] = None) -> Tuple[bool, str]:
        """
        Authenticate with iCloud.
        
        Password is passed via environment variable for security (not visible in process list).
        
        Args:
            password: iCloud password (or app-specific password)
            mfa_code: Optional 2FA code if required
            
        Returns:
            Tuple of (success, message)
        """
        if not self.is_configured:
            return False, "iCloud username not configured"
        
        try:
            # Build command - password passed via env var for security
            cmd = [
                self._icloudpd_bin,
                '--username', self.username,
                '--directory', str(self.download_dir),
                '--cookie-directory', str(self.cookie_dir),
                '--auth-only',
            ]
            
            # Pass password via environment variable (more secure than CLI arg)
            env = os.environ.copy()
            env['ICLOUD_PASSWORD'] = password
            
            # If MFA code provided, pass it via stdin
            input_data = None
            if mfa_code:
                input_data = mfa_code + '\n'
            
            result = subprocess.run(
                cmd,
                input=input_data,
                capture_output=True,
                text=True,
                timeout=120,
                env=env,
            )
            
            output = result.stdout + result.stderr
            logger.debug(f"Auth output: {output[:500]}")
            
            if 'Two-step authentication required' in output or \
               'Two-factor authentication required' in output or \
               'Please enter validation code' in output:
                self._auth_status = AuthStatus.REQUIRES_2FA
                return False, "2FA code required. Check your Apple device for the code."
            elif 'Invalid email/password' in output or 'invalid password' in output.lower():
                self._auth_status = AuthStatus.INVALID_CREDENTIALS
                logger.warning("iCloud authentication failed: invalid credentials")
                return False, "Invalid email or password. Try using an app-specific password from appleid.apple.com"
            elif 'Authentication successful' in output or result.returncode == 0:
                self._auth_status = AuthStatus.AUTHENTICATED
                logger.info("iCloud authentication successful")
                return True, "Authentication successful! Session saved."
            else:
                self._auth_status = AuthStatus.UNKNOWN_ERROR
                self._last_error = output[:500]
                logger.error(f"iCloud authentication failed with unknown error. Return code: {result.returncode}")
                logger.error(f"Raw stdout: {result.stdout[:500] if result.stdout else '(empty)'}")
                logger.error(f"Raw stderr: {result.stderr[:500] if result.stderr else '(empty)'}")
                return False, f"Authentication failed: {output[:200]}"
                
        except subprocess.TimeoutExpired:
            logger.error("iCloud authentication timed out after 120 seconds")
            return False, "Authentication timed out - Apple servers may be slow"
        except Exception as e:
            self._last_error = str(e)
            logger.exception(f"Unexpected error during iCloud authentication: {e}")
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
                self._icloudpd_bin,
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
                timeout=3600,  # 1 hour timeout for large syncs
                stdin=subprocess.DEVNULL,  # Don't hang on password prompts
            )
            
            output = result.stdout + result.stderr
            
            # Log sync output for debugging
            if output.strip():
                logger.debug(f"icloudpd sync output: {output[:1000]}")
            
            # Log non-zero return codes
            if result.returncode != 0:
                logger.warning(f"icloudpd sync returned non-zero exit code: {result.returncode}")
                logger.warning(f"stdout: {result.stdout[:500] if result.stdout else '(empty)'}")
                logger.warning(f"stderr: {result.stderr[:500] if result.stderr else '(empty)'}")
            
            # Check for auth errors (session expired)
            if 'Two-step authentication required' in output or \
               'Two-factor authentication required' in output or \
               'Password' in output or 'getpass' in output:
                self._auth_status = AuthStatus.REQUIRES_2FA
                logger.error("iCloud session expired during sync - re-authentication required")
                return False, 0, "Session expired - re-authentication required"
            
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

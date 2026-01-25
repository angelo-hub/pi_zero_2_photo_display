#!/usr/bin/env python3
"""
Photo Frame Main Service
Orchestrates all components: display, sync, scheduling, and web server.
"""

import os
import sys
import signal
import logging
import threading
from datetime import datetime
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

from src.config_manager import config
from src.icloud_sync import icloud_sync, AuthStatus
from src.image_converter import image_converter
from src.photo_selector import photo_selector
from src.display_manager import display_manager
from src.button_handler import button_handler
from src.battery_monitor import battery_monitor

# Configure logging
def setup_logging():
    """Setup logging configuration."""
    log_level = getattr(logging, config.get('logging.level', 'INFO'))
    log_file = config.get('logging.file', '/var/log/photoframe/photoframe.log')
    
    # Ensure log directory exists
    log_path = Path(log_file)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    
    # Create formatter
    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    # Console handler
    console_handler = logging.StreamHandler()
    console_handler.setLevel(log_level)
    console_handler.setFormatter(formatter)
    
    # File handler with rotation
    try:
        from logging.handlers import RotatingFileHandler
        file_handler = RotatingFileHandler(
            log_file,
            maxBytes=config.get('logging.max_size_mb', 10) * 1024 * 1024,
            backupCount=config.get('logging.backup_count', 3)
        )
        file_handler.setLevel(log_level)
        file_handler.setFormatter(formatter)
    except Exception as e:
        file_handler = None
        print(f"Warning: Could not setup file logging: {e}")
    
    # Root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)
    root_logger.addHandler(console_handler)
    if file_handler:
        root_logger.addHandler(file_handler)
    
    return logging.getLogger(__name__)


logger = setup_logging()


class PhotoFrameService:
    """Main service orchestrating all photo frame components."""
    
    def __init__(self):
        self.scheduler = BackgroundScheduler()
        self.running = False
        self.web_thread = None
        
        # Signal handling
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)
    
    def _signal_handler(self, signum, frame):
        """Handle shutdown signals."""
        logger.info(f"Received signal {signum}, shutting down...")
        self.stop()
    
    def start(self):
        """Start the photo frame service."""
        logger.info("=" * 50)
        logger.info("Starting Photo Frame Service")
        logger.info("=" * 50)
        
        self.running = True
        
        # Initialize components
        self._init_battery_monitor()
        self._init_display()
        self._init_button()
        
        # Check iCloud auth status
        self._check_icloud_auth()
        
        # Setup scheduled tasks
        self._setup_scheduler()
        
        # Start web server in background thread
        self._start_web_server()
        
        # Display initial photo
        self._display_next_photo()
        
        logger.info("Photo Frame Service started successfully")
        logger.info(f"Web UI available at http://0.0.0.0:{config.get('web.port', 8080)}")
        
        # Keep main thread alive
        try:
            while self.running:
                signal.pause()
        except AttributeError:
            # Windows doesn't have signal.pause
            import time
            while self.running:
                time.sleep(1)
    
    def stop(self):
        """Stop the photo frame service."""
        logger.info("Stopping Photo Frame Service...")
        
        self.running = False
        
        # Stop scheduler
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)
        
        # Stop components
        button_handler.shutdown()
        battery_monitor.shutdown()
        display_manager.shutdown()
        
        logger.info("Photo Frame Service stopped")
        sys.exit(0)
    
    def _init_battery_monitor(self):
        """Initialize battery monitoring."""
        if config.get('battery.enabled', True):
            battery_monitor.init(low_battery_callback=self._on_low_battery)
            battery_monitor.start_monitoring(interval=60)
            logger.info("Battery monitoring initialized")
    
    def _init_display(self):
        """Initialize the e-Paper display."""
        if display_manager.init():
            logger.info("Display initialized")
        else:
            logger.warning("Display initialization failed (may be in simulation mode)")
    
    def _init_button(self):
        """Initialize button handler."""
        if config.get('button.enabled', True):
            button_handler.init(callback=self._on_button_press)
            logger.info("Button handler initialized")
    
    def _check_icloud_auth(self):
        """Check iCloud authentication status."""
        status = icloud_sync.check_auth_status()
        
        if status == AuthStatus.AUTHENTICATED:
            logger.info("iCloud: Authenticated")
        elif status == AuthStatus.REQUIRES_2FA:
            logger.warning("iCloud: Re-authentication required (2FA)")
            self._send_auth_notification()
        elif status == AuthStatus.NOT_CONFIGURED:
            logger.info("iCloud: Not configured")
        else:
            logger.warning(f"iCloud: {status.value}")
    
    def _setup_scheduler(self):
        """Setup scheduled tasks."""
        # Photo rotation
        rotation_interval = config.get('display.rotation_interval', 3600)
        self.scheduler.add_job(
            self._display_next_photo,
            trigger=IntervalTrigger(seconds=rotation_interval),
            id='photo_rotation',
            name='Display next photo',
            replace_existing=True
        )
        logger.info(f"Scheduled photo rotation every {rotation_interval} seconds")
        
        # iCloud sync
        sync_interval = config.get('icloud.sync_interval', 86400)
        self.scheduler.add_job(
            self._sync_photos,
            trigger=IntervalTrigger(seconds=sync_interval),
            id='icloud_sync',
            name='Sync iCloud photos',
            replace_existing=True
        )
        logger.info(f"Scheduled iCloud sync every {sync_interval} seconds")
        
        # Start scheduler
        self.scheduler.start()
        logger.info("Scheduler started")
    
    def _start_web_server(self):
        """Start web server in background thread."""
        if not config.get('web.enabled', True):
            logger.info("Web server disabled")
            return
        
        from src.web_server import run_server
        
        self.web_thread = threading.Thread(
            target=run_server,
            daemon=True,
            name='WebServer'
        )
        self.web_thread.start()
        logger.info("Web server started")
    
    def _display_next_photo(self, force: bool = False):
        """Display the next photo in rotation."""
        # Check if paused (unless forced, e.g., from button press)
        if not force and photo_selector.is_paused:
            logger.debug("Skipping auto-rotation - paused")
            return
        
        # Check battery
        if not battery_monitor.can_refresh():
            logger.warning("Skipping photo rotation - battery too low")
            return
        
        # Select and display
        photo = photo_selector.select_next(force=force)
        if photo:
            success = display_manager.display_image(photo)
            if success:
                logger.info(f"Displayed: {photo.name}")
            else:
                logger.error(f"Failed to display: {photo.name}")
        else:
            logger.warning("No photos available for display")
    
    def _sync_photos(self):
        """Sync photos from iCloud and convert them."""
        logger.info("Starting iCloud sync...")
        
        # Check auth status first
        status = icloud_sync.check_auth_status()
        if status == AuthStatus.REQUIRES_2FA:
            logger.warning("Sync skipped - re-authentication required")
            self._send_auth_notification()
            return
        
        if status != AuthStatus.AUTHENTICATED:
            logger.warning(f"Sync skipped - not authenticated: {status.value}")
            return
        
        # Sync photos
        success, downloaded, message = icloud_sync.sync_photos()
        
        if success:
            logger.info(f"Sync complete: {downloaded} new photos")
            
            # Convert new photos
            if downloaded > 0:
                converted, errors = image_converter.convert_all_new()
                logger.info(f"Converted {converted} photos ({errors} errors)")
            
            # Cleanup orphaned converted images
            image_converter.cleanup_orphaned()
        else:
            logger.error(f"Sync failed: {message}")
            if "2FA" in message or "authentication" in message.lower():
                self._send_auth_notification()
    
    def _on_button_press(self):
        """Handle physical button press."""
        logger.info("Button press detected")
        self._display_next_photo(force=True)  # Force even if paused
    
    def _on_low_battery(self, percent):
        """Handle low battery warning."""
        logger.warning(f"Low battery: {percent:.1f}%")
        self._send_notification(
            f"Photo Frame battery low: {percent:.1f}%",
            "low_battery"
        )
    
    def _send_auth_notification(self):
        """Send notification that re-authentication is needed."""
        host = config.get('web.host', '0.0.0.0')
        port = config.get('web.port', 8080)
        
        # Try to get actual IP
        try:
            import socket
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
        except:
            ip = "photoframe.local"
        
        message = f"iCloud re-authentication required. Visit http://{ip}:{port}/auth"
        self._send_notification(message, "auth_required")
    
    def _send_notification(self, message: str, notification_type: str):
        """Send notification via configured channels."""
        logger.info(f"Notification ({notification_type}): {message}")
        
        # Local notification (stored for web UI)
        if config.get('notifications.local.enabled', True):
            self._store_local_notification(message, notification_type)
        
        # Pushover
        if config.get('notifications.pushover.enabled', False):
            self._send_pushover(message)
        
        # Webhook
        if config.get('notifications.webhook.enabled', False):
            self._send_webhook(message, notification_type)
    
    def _store_local_notification(self, message: str, notification_type: str):
        """Store notification for web UI display."""
        notifications_file = Path.home() / '.photoframe' / 'notifications.json'
        notifications_file.parent.mkdir(parents=True, exist_ok=True)
        
        import json
        
        notifications = []
        if notifications_file.exists():
            try:
                with open(notifications_file) as f:
                    notifications = json.load(f)
            except:
                pass
        
        notifications.append({
            'message': message,
            'type': notification_type,
            'timestamp': datetime.now().isoformat(),
            'read': False
        })
        
        # Keep only last 50 notifications
        notifications = notifications[-50:]
        
        with open(notifications_file, 'w') as f:
            json.dump(notifications, f)
    
    def _send_pushover(self, message: str):
        """Send Pushover notification."""
        try:
            import requests
            
            requests.post('https://api.pushover.net/1/messages.json', data={
                'token': config.get('notifications.pushover.api_token'),
                'user': config.get('notifications.pushover.user_key'),
                'message': message,
                'title': 'Photo Frame'
            })
        except Exception as e:
            logger.error(f"Pushover notification failed: {e}")
    
    def _send_webhook(self, message: str, notification_type: str):
        """Send webhook notification."""
        try:
            import requests
            
            url = config.get('notifications.webhook.url')
            if url:
                requests.post(url, json={
                    'message': message,
                    'type': notification_type,
                    'timestamp': datetime.now().isoformat()
                }, timeout=10)
        except Exception as e:
            logger.error(f"Webhook notification failed: {e}")


def main():
    """Main entry point."""
    service = PhotoFrameService()
    service.start()


if __name__ == '__main__':
    main()

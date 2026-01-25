"""
Button Handler Module
Handles GPIO button input for manual photo rotation.
"""

import logging
import threading
from typing import Callable, Optional
from datetime import datetime, timedelta

from .config_manager import config

logger = logging.getLogger(__name__)


class ButtonHandler:
    """Handles physical button input via GPIO."""
    
    def __init__(self):
        self.gpio_pin = config.get('button.gpio_pin', 4)
        self.debounce_ms = config.get('button.debounce_ms', 300)
        self.enabled = config.get('button.enabled', True)
        
        self._button = None
        self._callback: Optional[Callable] = None
        self._last_press: Optional[datetime] = None
        self._press_count = 0
        self._initialized = False
        
        # Check if on Raspberry Pi
        self._simulate = not self._check_raspberry_pi()
    
    def _check_raspberry_pi(self) -> bool:
        """Check if running on Raspberry Pi."""
        try:
            with open('/proc/cpuinfo', 'r') as f:
                return 'Raspberry' in f.read()
        except:
            return False
    
    def init(self, callback: Callable) -> bool:
        """
        Initialize button handler with callback function.
        
        Args:
            callback: Function to call when button is pressed
            
        Returns:
            True if successful, False otherwise
        """
        if not self.enabled:
            logger.info("Button handler disabled in config")
            return False
        
        self._callback = callback
        
        if self._simulate:
            logger.info("Button handler in simulation mode (not on Raspberry Pi)")
            self._initialized = True
            return True
        
        try:
            from gpiozero import Button
            
            self._button = Button(
                self.gpio_pin,
                pull_up=True,
                bounce_time=self.debounce_ms / 1000.0
            )
            
            self._button.when_pressed = self._on_button_press
            self._initialized = True
            
            logger.info(f"Button handler initialized on GPIO {self.gpio_pin}")
            return True
            
        except ImportError:
            logger.warning("gpiozero not available, button disabled")
            return False
        except Exception as e:
            logger.error(f"Button init failed: {e}")
            return False
    
    def _on_button_press(self) -> None:
        """Internal callback for button press events."""
        now = datetime.now()
        
        # Additional software debounce
        if self._last_press:
            elapsed = (now - self._last_press).total_seconds() * 1000
            if elapsed < self.debounce_ms:
                logger.debug(f"Button press ignored (debounce: {elapsed:.0f}ms)")
                return
        
        self._last_press = now
        self._press_count += 1
        
        logger.info(f"Button pressed (count: {self._press_count})")
        
        if self._callback:
            # Run callback in separate thread to not block GPIO handler
            threading.Thread(target=self._callback, daemon=True).start()
    
    def simulate_press(self) -> None:
        """Simulate a button press (for testing/web UI)."""
        if self._callback:
            logger.info("Simulated button press")
            self._on_button_press()
    
    def shutdown(self) -> None:
        """Cleanup button handler."""
        if self._button:
            try:
                self._button.close()
            except:
                pass
        
        self._initialized = False
        logger.info("Button handler shutdown")
    
    def get_status(self) -> dict:
        """Get button handler status for web UI."""
        return {
            'enabled': self.enabled,
            'gpio_pin': self.gpio_pin,
            'initialized': self._initialized,
            'simulation_mode': self._simulate,
            'press_count': self._press_count,
            'last_press': self._last_press.isoformat() if self._last_press else None,
        }


# Global button handler instance
button_handler = ButtonHandler()

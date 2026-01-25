"""
Display Manager Module
Controls the Waveshare e-Paper display.
"""

import os
import sys
import logging
import time
from pathlib import Path
from typing import Optional
from datetime import datetime

from PIL import Image

from .config_manager import config

logger = logging.getLogger(__name__)

# Add Waveshare driver to path
LIB_DIR = Path(__file__).parent.parent / 'lib'
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))


class DisplayManager:
    """Manages the e-Paper display."""
    
    def __init__(self):
        self.model = config.get('display.model', '7in3e')
        self.width = config.get('display.width', 800)
        self.height = config.get('display.height', 480)
        self.rotation = config.get('display.rotation', 0)  # 0, 90, 180, 270
        
        self._epd = None
        self._driver_module = None
        self._initialized = False
        self._last_refresh: Optional[datetime] = None
        self._last_image: Optional[Path] = None
        self._is_sleeping = True
        
        # Simulation mode for development
        self._simulate = not self._check_raspberry_pi()
    
    def _check_raspberry_pi(self) -> bool:
        """Check if running on Raspberry Pi."""
        try:
            with open('/proc/cpuinfo', 'r') as f:
                return 'Raspberry' in f.read()
        except:
            return False
    
    def _load_driver(self) -> bool:
        """Load the Waveshare driver module."""
        if self._simulate:
            logger.info("Running in simulation mode (not on Raspberry Pi)")
            return True
        
        try:
            module_name = f"waveshare_epd.epd{self.model}"
            self._driver_module = __import__(module_name, fromlist=['EPD'])
            logger.info(f"Loaded display driver: {module_name}")
            return True
        except ImportError as e:
            logger.error(f"Could not load display driver: {e}")
            return False
    
    def init(self) -> bool:
        """Initialize the display."""
        if self._initialized:
            return True
        
        if not self._load_driver():
            return False
        
        if self._simulate:
            self._initialized = True
            logger.info("Display initialized (simulation mode)")
            return True
        
        try:
            self._epd = self._driver_module.EPD()
            self._epd.init()
            self.width = self._epd.width
            self.height = self._epd.height
            self._initialized = True
            self._is_sleeping = False
            logger.info(f"Display initialized: {self.width}x{self.height}")
            return True
        except Exception as e:
            logger.error(f"Display init failed: {e}")
            return False
    
    def display_image(self, image_path: Path) -> bool:
        """
        Display an image on the e-Paper.
        
        Args:
            image_path: Path to the image file
            
        Returns:
            True if successful, False otherwise
        """
        if not image_path.exists():
            logger.error(f"Image not found: {image_path}")
            return False
        
        if not self._initialized:
            if not self.init():
                return False
        
        try:
            # Load image
            img = Image.open(image_path)
            
            # Ensure RGB mode
            if img.mode != 'RGB':
                img = img.convert('RGB')
            
            # Resize if needed
            if img.size != (self.width, self.height):
                logger.warning(f"Image size {img.size} doesn't match display {self.width}x{self.height}")
                img = img.resize((self.width, self.height), Image.Resampling.LANCZOS)
            
            # Apply display rotation for physical mounting orientation
            # Reload rotation setting in case it changed
            self.rotation = config.get('display.rotation', 0)
            if self.rotation != 0:
                # PIL rotate is counter-clockwise, so we use negative for clockwise
                # rotate(90) = 90° counter-clockwise, rotate(-90) or rotate(270) = 90° clockwise
                img = img.rotate(-self.rotation, expand=False)
                logger.debug(f"Applied {self.rotation}° rotation for display")
            
            if self._simulate:
                # In simulation mode, just save a preview
                preview_path = Path('/tmp/photoframe_preview.png')
                img.save(preview_path)
                logger.info(f"Simulation: Would display {image_path.name}")
                logger.info(f"Preview saved to {preview_path}")
            else:
                # Wake display if sleeping
                if self._is_sleeping:
                    self._epd.init()
                    self._is_sleeping = False
                
                # Display the image
                logger.info(f"Refreshing display with: {image_path.name}")
                start_time = time.time()
                
                buffer = self._epd.getbuffer(img)
                self._epd.display(buffer)
                
                refresh_time = time.time() - start_time
                logger.info(f"Display refresh complete ({refresh_time:.1f}s)")
                
                # Put display to sleep for power saving
                time.sleep(1)  # Wait for display to stabilize
                self._epd.sleep()
                self._is_sleeping = True
            
            self._last_refresh = datetime.now()
            self._last_image = image_path
            return True
            
        except Exception as e:
            logger.error(f"Display error: {e}")
            self._cleanup_on_error()
            return False
    
    def clear(self) -> bool:
        """Clear the display to white."""
        if not self._initialized:
            if not self.init():
                return False
        
        if self._simulate:
            logger.info("Simulation: Display cleared")
            return True
        
        try:
            if self._is_sleeping:
                self._epd.init()
                self._is_sleeping = False
            
            self._epd.Clear(0x11)  # White
            time.sleep(1)
            self._epd.sleep()
            self._is_sleeping = True
            
            logger.info("Display cleared")
            return True
        except Exception as e:
            logger.error(f"Clear failed: {e}")
            return False
    
    def _cleanup_on_error(self) -> None:
        """Cleanup after an error."""
        if self._simulate:
            return
        
        try:
            if self._driver_module and hasattr(self._driver_module, 'epdconfig'):
                self._driver_module.epdconfig.module_exit()
        except:
            pass
        
        self._initialized = False
        self._is_sleeping = True
    
    def shutdown(self) -> None:
        """Properly shutdown the display."""
        if self._simulate:
            logger.info("Simulation: Display shutdown")
            return
        
        if self._epd and not self._is_sleeping:
            try:
                self._epd.sleep()
                self._is_sleeping = True
            except:
                pass
        
        if self._driver_module and hasattr(self._driver_module, 'epdconfig'):
            try:
                self._driver_module.epdconfig.module_exit()
            except:
                pass
        
        self._initialized = False
        logger.info("Display shutdown complete")
    
    def get_status(self) -> dict:
        """Get display status for web UI."""
        return {
            'model': self.model,
            'resolution': f"{self.width}x{self.height}",
            'rotation': self.rotation,
            'initialized': self._initialized,
            'is_sleeping': self._is_sleeping,
            'simulation_mode': self._simulate,
            'last_refresh': self._last_refresh.isoformat() if self._last_refresh else None,
            'last_image': str(self._last_image) if self._last_image else None,
        }


# Global display manager instance
display_manager = DisplayManager()

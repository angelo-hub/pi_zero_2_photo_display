"""
Battery Monitor Module
Monitors UPS battery status via INA219 chip on I2C.
"""

import logging
import threading
import time
from typing import Optional, Callable
from datetime import datetime

from .config_manager import config

logger = logging.getLogger(__name__)


class BatteryMonitor:
    """Monitors battery status via INA219 chip."""
    
    def __init__(self):
        self.enabled = config.get('battery.enabled', True)
        self.i2c_address = config.get('battery.i2c_address', 0x43)
        self.min_refresh_percent = config.get('battery.min_refresh_percent', 10)
        self.low_warning_percent = config.get('battery.low_warning_percent', 20)
        
        self._ina219 = None
        self._initialized = False
        self._simulate = not self._check_raspberry_pi()
        
        # Cached readings
        self._voltage: float = 0.0
        self._current: float = 0.0
        self._power: float = 0.0
        self._percent: float = 100.0
        self._last_read: Optional[datetime] = None
        
        # Low battery callback
        self._low_battery_callback: Optional[Callable] = None
        self._low_battery_warned = False
        
        # Background monitoring
        self._monitor_thread: Optional[threading.Thread] = None
        self._stop_monitoring = threading.Event()
    
    def _check_raspberry_pi(self) -> bool:
        """Check if running on Raspberry Pi."""
        try:
            with open('/proc/cpuinfo', 'r') as f:
                return 'Raspberry' in f.read()
        except:
            return False
    
    def init(self, low_battery_callback: Optional[Callable] = None) -> bool:
        """
        Initialize battery monitor.
        
        Args:
            low_battery_callback: Function to call when battery is low
            
        Returns:
            True if successful, False otherwise
        """
        if not self.enabled:
            logger.info("Battery monitor disabled in config")
            return False
        
        self._low_battery_callback = low_battery_callback
        
        if self._simulate:
            logger.info("Battery monitor in simulation mode")
            self._initialized = True
            self._percent = 85.0  # Simulated battery level
            return True
        
        try:
            import smbus2
            
            # INA219 configuration
            self._bus = smbus2.SMBus(1)  # I2C bus 1
            
            # Verify device is present
            try:
                self._bus.read_byte(self.i2c_address)
            except:
                logger.warning(f"INA219 not found at address 0x{self.i2c_address:02x}")
                return False
            
            # Configure INA219
            self._configure_ina219()
            
            self._initialized = True
            logger.info(f"Battery monitor initialized (I2C: 0x{self.i2c_address:02x})")
            
            # Do initial read
            self.read_battery()
            
            return True
            
        except ImportError:
            logger.warning("smbus2 not available, battery monitoring disabled")
            return False
        except Exception as e:
            logger.error(f"Battery monitor init failed: {e}")
            return False
    
    def _configure_ina219(self) -> None:
        """Configure INA219 for battery monitoring."""
        # INA219 registers
        REG_CONFIG = 0x00
        REG_CALIBRATION = 0x05
        
        # Configuration: 16V range, 80mV shunt, 12-bit, continuous
        config_value = (
            (0x00 << 13) |  # Bus voltage range: 16V
            (0x01 << 11) |  # Gain: /2 (80mV)
            (0x0D << 7) |   # Bus ADC: 12-bit, 32 samples
            (0x0D << 3) |   # Shunt ADC: 12-bit, 32 samples
            (0x07)          # Mode: continuous
        )
        
        # Write config
        self._write_register(REG_CONFIG, config_value)
        
        # Calibration for 0.01 ohm shunt
        self._write_register(REG_CALIBRATION, 26868)
    
    def _write_register(self, reg: int, value: int) -> None:
        """Write to INA219 register."""
        data = [(value >> 8) & 0xFF, value & 0xFF]
        self._bus.write_i2c_block_data(self.i2c_address, reg, data)
    
    def _read_register(self, reg: int) -> int:
        """Read from INA219 register."""
        data = self._bus.read_i2c_block_data(self.i2c_address, reg, 2)
        return (data[0] << 8) | data[1]
    
    def read_battery(self) -> dict:
        """
        Read current battery status.
        
        Returns:
            Dict with voltage, current, power, percent
        """
        if not self._initialized:
            return self._get_cached_status()
        
        if self._simulate:
            # Simulate slowly decreasing battery
            self._percent = max(0, self._percent - 0.01)
            self._voltage = 3.0 + (self._percent / 100) * 1.2
            self._current = 150.0  # mA
            self._power = self._voltage * self._current / 1000
            self._last_read = datetime.now()
            return self._get_cached_status()
        
        try:
            # Read bus voltage (V- side, load voltage)
            REG_BUSVOLTAGE = 0x02
            raw = self._read_register(REG_BUSVOLTAGE)
            self._voltage = (raw >> 3) * 0.004  # 4mV per bit
            
            # Read current
            REG_CURRENT = 0x04
            raw = self._read_register(REG_CURRENT)
            if raw > 32767:
                raw -= 65535
            self._current = raw * 0.1524  # Current LSB
            
            # Read power
            REG_POWER = 0x03
            raw = self._read_register(REG_POWER)
            if raw > 32767:
                raw -= 65535
            self._power = raw * 0.003048  # Power LSB
            
            # Calculate percentage
            # Battery range: 3.0V (0%) to 4.2V (100%)
            self._percent = (self._voltage - 3.0) / 1.2 * 100
            self._percent = max(0, min(100, self._percent))
            
            self._last_read = datetime.now()
            
            # Check for low battery
            self._check_low_battery()
            
            return self._get_cached_status()
            
        except Exception as e:
            logger.error(f"Battery read error: {e}")
            return self._get_cached_status()
    
    def _get_cached_status(self) -> dict:
        """Get cached battery status."""
        return {
            'voltage': round(self._voltage, 3),
            'current_ma': round(self._current, 1),
            'power_w': round(self._power, 3),
            'percent': round(self._percent, 1),
            'last_read': self._last_read.isoformat() if self._last_read else None,
        }
    
    def _check_low_battery(self) -> None:
        """Check and handle low battery condition."""
        if self._percent <= self.low_warning_percent and not self._low_battery_warned:
            self._low_battery_warned = True
            logger.warning(f"Low battery warning: {self._percent:.1f}%")
            
            if self._low_battery_callback:
                self._low_battery_callback(self._percent)
        
        elif self._percent > self.low_warning_percent + 5:
            # Reset warning when battery recovers
            self._low_battery_warned = False
    
    def can_refresh(self) -> bool:
        """Check if battery level allows display refresh."""
        if not self.enabled:
            return True
        
        if self._simulate:
            return True
        
        # Update reading
        self.read_battery()
        
        if self._percent < self.min_refresh_percent:
            logger.warning(f"Battery too low for refresh: {self._percent:.1f}%")
            return False
        
        return True
    
    def start_monitoring(self, interval: int = 60) -> None:
        """
        Start background battery monitoring.
        
        Args:
            interval: Seconds between readings
        """
        if self._monitor_thread and self._monitor_thread.is_alive():
            return
        
        self._stop_monitoring.clear()
        self._monitor_thread = threading.Thread(
            target=self._monitor_loop,
            args=(interval,),
            daemon=True
        )
        self._monitor_thread.start()
        logger.info(f"Battery monitoring started (interval: {interval}s)")
    
    def stop_monitoring(self) -> None:
        """Stop background battery monitoring."""
        self._stop_monitoring.set()
        if self._monitor_thread:
            self._monitor_thread.join(timeout=5)
        logger.info("Battery monitoring stopped")
    
    def _monitor_loop(self, interval: int) -> None:
        """Background monitoring loop."""
        while not self._stop_monitoring.is_set():
            try:
                self.read_battery()
            except Exception as e:
                logger.error(f"Battery monitor error: {e}")
            
            self._stop_monitoring.wait(interval)
    
    def shutdown(self) -> None:
        """Cleanup battery monitor."""
        self.stop_monitoring()
        
        if hasattr(self, '_bus') and self._bus:
            try:
                self._bus.close()
            except:
                pass
        
        self._initialized = False
        logger.info("Battery monitor shutdown")
    
    def get_status(self) -> dict:
        """Get battery monitor status for web UI."""
        status = self._get_cached_status()
        status.update({
            'enabled': self.enabled,
            'initialized': self._initialized,
            'simulation_mode': self._simulate,
            'min_refresh_percent': self.min_refresh_percent,
            'low_warning_percent': self.low_warning_percent,
            'can_refresh': self.can_refresh() if self._initialized else True,
        })
        return status


# Global battery monitor instance
battery_monitor = BatteryMonitor()

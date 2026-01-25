"""
Image Converter Module
Handles resizing and converting images to 6-color palette for e-Paper display.
Uses Floyd-Steinberg dithering for best visual results.
"""

import os
import logging
from pathlib import Path
from typing import Optional, Tuple, List
from datetime import datetime
import hashlib

from PIL import Image
import numpy as np

# Register HEIC/HEIF support if available
try:
    import pillow_heif
    pillow_heif.register_heif_opener()
    HEIC_SUPPORTED = True
except ImportError:
    HEIC_SUPPORTED = False

from .config_manager import config

logger = logging.getLogger(__name__)

if not HEIC_SUPPORTED:
    logger.warning("HEIC support not available. Install with: pip install pillow-heif")

# 6-color palette for the Waveshare 7.3" e-Paper display
# Colors: Black, White, Yellow, Red, (unused), Blue, Green
EPAPER_PALETTE = [
    0, 0, 0,        # 0: Black
    255, 255, 255,  # 1: White
    255, 255, 0,    # 2: Yellow
    255, 0, 0,      # 3: Red
    0, 0, 0,        # 4: (unused, same as black)
    0, 0, 255,      # 5: Blue
    0, 255, 0,      # 6: Green
] + [0, 0, 0] * 249  # Pad to 256 colors


class ImageConverter:
    """Converts images for e-Paper display."""
    
    def __init__(self):
        self.width = config.get('display.width', 800)
        self.height = config.get('display.height', 480)
        self.orientation = config.get('display.orientation', 'landscape')
        
        self.source_dir = config.get_path('icloud.download_dir')
        self.ready_dir = config.get_path('paths.ready_dir')
        
        # Ensure ready directory exists
        self.ready_dir.mkdir(parents=True, exist_ok=True)
        
        # Create palette image for quantization
        self._palette_image = Image.new("P", (1, 1))
        self._palette_image.putpalette(EPAPER_PALETTE)
        
        # Track converted images
        self._conversion_cache: dict = {}
        self._load_cache()
    
    def _load_cache(self) -> None:
        """Load conversion cache from disk."""
        cache_file = self.ready_dir / '.conversion_cache.json'
        if cache_file.exists():
            try:
                import json
                with open(cache_file, 'r') as f:
                    self._conversion_cache = json.load(f)
            except Exception as e:
                logger.warning(f"Could not load conversion cache: {e}")
                self._conversion_cache = {}
    
    def _save_cache(self) -> None:
        """Save conversion cache to disk."""
        cache_file = self.ready_dir / '.conversion_cache.json'
        try:
            import json
            with open(cache_file, 'w') as f:
                json.dump(self._conversion_cache, f)
        except Exception as e:
            logger.warning(f"Could not save conversion cache: {e}")
    
    def _get_file_hash(self, file_path: Path) -> str:
        """Get hash of file for cache checking."""
        stat = file_path.stat()
        # Use filename + size + mtime for quick hash
        key = f"{file_path.name}_{stat.st_size}_{stat.st_mtime}"
        return hashlib.md5(key.encode()).hexdigest()[:16]
    
    def _needs_conversion(self, source_path: Path) -> Tuple[bool, Optional[Path]]:
        """Check if source image needs conversion."""
        file_hash = self._get_file_hash(source_path)
        
        if file_hash in self._conversion_cache:
            ready_path = Path(self._conversion_cache[file_hash])
            if ready_path.exists():
                return False, ready_path
        
        return True, None
    
    def convert_image(self, source_path: Path, force: bool = False) -> Optional[Path]:
        """
        Convert a single image to e-Paper format.
        
        Args:
            source_path: Path to source image
            force: Force reconversion even if cached
            
        Returns:
            Path to converted image, or None on failure
        """
        if not source_path.exists():
            logger.error(f"Source image not found: {source_path}")
            return None
        
        # Check cache
        if not force:
            needs_conv, cached_path = self._needs_conversion(source_path)
            if not needs_conv and cached_path:
                logger.debug(f"Using cached conversion: {cached_path}")
                return cached_path
        
        try:
            # Load image
            img = Image.open(source_path)
            
            # Convert to RGB if necessary
            if img.mode != 'RGB':
                img = img.convert('RGB')
            
            # Handle EXIF orientation
            img = self._fix_orientation(img)
            
            # Determine target size based on orientation
            target_w, target_h = self.width, self.height
            
            # Resize and crop intelligently
            img = self._smart_crop(img, target_w, target_h)
            
            # Convert to 6-color palette with dithering
            img_dithered = img.quantize(palette=self._palette_image, dither=Image.Dither.FLOYDSTEINBERG)
            
            # Convert back to RGB for saving (better compatibility)
            img_final = img_dithered.convert('RGB')
            
            # Generate output filename
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            output_name = f"{source_path.stem}_{timestamp}.png"
            output_path = self.ready_dir / output_name
            
            # Save converted image
            img_final.save(output_path, 'PNG', optimize=True)
            
            # Update cache
            file_hash = self._get_file_hash(source_path)
            self._conversion_cache[file_hash] = str(output_path)
            self._save_cache()
            
            logger.info(f"Converted: {source_path.name} -> {output_name}")
            return output_path
            
        except Exception as e:
            logger.error(f"Conversion error for {source_path}: {e}")
            return None
    
    def _fix_orientation(self, img: Image.Image) -> Image.Image:
        """Fix image orientation based on EXIF data."""
        try:
            from PIL import ExifTags
            
            # Get EXIF orientation tag
            for orientation in ExifTags.TAGS.keys():
                if ExifTags.TAGS[orientation] == 'Orientation':
                    break
            
            exif = img._getexif()
            if exif is None:
                return img
            
            orientation_value = exif.get(orientation)
            
            if orientation_value == 3:
                img = img.rotate(180, expand=True)
            elif orientation_value == 6:
                img = img.rotate(270, expand=True)
            elif orientation_value == 8:
                img = img.rotate(90, expand=True)
                
        except Exception:
            pass  # No EXIF or error reading it
        
        return img
    
    def _smart_crop(self, img: Image.Image, target_w: int, target_h: int) -> Image.Image:
        """
        Intelligently crop image to target dimensions.
        Tries to keep the most important parts of the image.
        """
        img_w, img_h = img.size
        
        # Check if image needs rotation to better fit display
        img_aspect = img_w / img_h
        target_aspect = target_w / target_h
        
        # If image is portrait but display is landscape (or vice versa),
        # check if rotating would be better
        if (img_aspect < 1 and target_aspect > 1) or (img_aspect > 1 and target_aspect < 1):
            # Rotate the image 90 degrees
            img = img.rotate(90, expand=True)
            img_w, img_h = img.size
            img_aspect = img_w / img_h
        
        # Calculate resize dimensions to fill target (crop excess)
        if img_aspect > target_aspect:
            # Image is wider - resize by height, crop width
            new_h = target_h
            new_w = int(new_h * img_aspect)
        else:
            # Image is taller - resize by width, crop height
            new_w = target_w
            new_h = int(new_w / img_aspect)
        
        # Resize image
        img = img.resize((new_w, new_h), Image.Resampling.LANCZOS)
        
        # Center crop to target size
        left = (new_w - target_w) // 2
        top = (new_h - target_h) // 2
        right = left + target_w
        bottom = top + target_h
        
        img = img.crop((left, top, right, bottom))
        
        return img
    
    def convert_all_new(self, delay_seconds: float = None) -> Tuple[int, int]:
        """
        Convert all new images from source directory.
        
        Args:
            delay_seconds: Delay between conversions to avoid overloading Pi Zero
                          (defaults to config value or 5 seconds)
        
        Returns:
            Tuple of (converted_count, error_count)
        """
        import time
        
        if delay_seconds is None:
            delay_seconds = config.get('conversion.delay_seconds', 5.0)
        
        converted = 0
        errors = 0
        
        # Supported image extensions
        extensions = {'.jpg', '.jpeg', '.png', '.heic', '.gif', '.bmp', '.webp'}
        
        # Get all source images
        source_images = []
        for ext in extensions:
            source_images.extend(self.source_dir.glob(f'**/*{ext}'))
            source_images.extend(self.source_dir.glob(f'**/*{ext.upper()}'))
        
        # Count how many need conversion
        to_convert = []
        for source_path in source_images:
            needs_conv, _ = self._needs_conversion(source_path)
            if needs_conv:
                to_convert.append(source_path)
        
        total = len(to_convert)
        logger.info(f"Found {len(source_images)} source images, {total} need conversion")
        
        for i, source_path in enumerate(to_convert, 1):
            logger.info(f"Converting {i}/{total}: {source_path.name}")
            result = self.convert_image(source_path)
            if result:
                converted += 1
            else:
                errors += 1
            
            # Delay between conversions to avoid overloading Pi Zero
            if i < total and delay_seconds > 0:
                time.sleep(delay_seconds)
        
        logger.info(f"Conversion complete: {converted} converted, {errors} errors")
        return converted, errors
    
    def get_ready_images(self) -> List[Path]:
        """Get list of all ready-to-display images."""
        images = list(self.ready_dir.glob('*.png'))
        return sorted(images, key=lambda p: p.stat().st_mtime, reverse=True)
    
    def cleanup_orphaned(self) -> int:
        """Remove converted images whose source no longer exists."""
        removed = 0
        
        # Get all source file hashes
        source_hashes = set()
        extensions = {'.jpg', '.jpeg', '.png', '.heic', '.gif', '.bmp', '.webp'}
        
        for ext in extensions:
            for source_path in self.source_dir.glob(f'**/*{ext}'):
                source_hashes.add(self._get_file_hash(source_path))
            for source_path in self.source_dir.glob(f'**/*{ext.upper()}'):
                source_hashes.add(self._get_file_hash(source_path))
        
        # Remove orphaned entries from cache
        orphaned_hashes = set(self._conversion_cache.keys()) - source_hashes
        
        for file_hash in orphaned_hashes:
            ready_path = Path(self._conversion_cache[file_hash])
            if ready_path.exists():
                try:
                    ready_path.unlink()
                    removed += 1
                    logger.debug(f"Removed orphaned: {ready_path.name}")
                except Exception as e:
                    logger.warning(f"Could not remove {ready_path}: {e}")
            del self._conversion_cache[file_hash]
        
        if removed:
            self._save_cache()
            logger.info(f"Cleaned up {removed} orphaned images")
        
        return removed
    
    def get_status(self) -> dict:
        """Get converter status for web UI."""
        ready_images = self.get_ready_images()
        return {
            'ready_count': len(ready_images),
            'ready_dir': str(self.ready_dir),
            'target_size': f"{self.width}x{self.height}",
            'orientation': self.orientation,
            'cache_entries': len(self._conversion_cache),
        }


# Global converter instance
image_converter = ImageConverter()

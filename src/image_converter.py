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
        import threading
        
        self.width = config.get('display.width', 800)
        self.height = config.get('display.height', 480)
        self.orientation = config.get('display.orientation', 'landscape')
        
        self.source_dir = config.get_path('icloud.download_dir')
        self.ready_dir = config.get_path('paths.ready_dir')
        self.thumbnails_dir = config.get_path('paths.thumbnails_dir')
        
        # Thumbnail settings
        self.thumb_width = config.get('thumbnails.width', 200)
        self.thumb_height = config.get('thumbnails.height', 120)
        self.thumb_quality = config.get('thumbnails.quality', 85)
        self.thumb_format = config.get('thumbnails.format', 'webp').lower()
        
        # Ensure directories exist
        self.ready_dir.mkdir(parents=True, exist_ok=True)
        self.thumbnails_dir.mkdir(parents=True, exist_ok=True)
        
        # Create palette image for quantization
        self._palette_image = Image.new("P", (1, 1))
        self._palette_image.putpalette(EPAPER_PALETTE)
        
        # Track converted images
        self._conversion_cache: dict = {}
        self._load_cache()
        
        # Lock to prevent concurrent conversions (important for Pi Zero memory)
        self._conversion_lock = threading.Lock()
        self._is_converting = False
    
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
            
            # Generate thumbnail for gallery preview
            self._generate_thumbnail(output_path)
            
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
    
    def _generate_thumbnail(self, ready_path: Path) -> Optional[Path]:
        """
        Generate a thumbnail for a converted image.
        
        Args:
            ready_path: Path to the ready (converted) image
            
        Returns:
            Path to thumbnail, or None on failure
        """
        try:
            # Determine thumbnail filename and path
            if self.thumb_format == 'webp':
                thumb_name = ready_path.stem + '.webp'
            else:
                thumb_name = ready_path.stem + '.jpg'
            
            thumb_path = self.thumbnails_dir / thumb_name
            
            # Skip if thumbnail already exists and is newer than source
            if thumb_path.exists():
                if thumb_path.stat().st_mtime >= ready_path.stat().st_mtime:
                    return thumb_path
            
            # Load the ready image
            img = Image.open(ready_path)
            
            # Resize to thumbnail size (maintain aspect ratio, fit within bounds)
            img.thumbnail((self.thumb_width, self.thumb_height), Image.Resampling.LANCZOS)
            
            # Convert to RGB if necessary (for JPEG/WebP compatibility)
            if img.mode != 'RGB':
                img = img.convert('RGB')
            
            # Save thumbnail
            if self.thumb_format == 'webp':
                img.save(thumb_path, 'WEBP', quality=self.thumb_quality)
            else:
                img.save(thumb_path, 'JPEG', quality=self.thumb_quality, optimize=True)
            
            logger.debug(f"Generated thumbnail: {thumb_name}")
            return thumb_path
            
        except Exception as e:
            logger.warning(f"Could not generate thumbnail for {ready_path.name}: {e}")
            return None
    
    def get_thumbnail_path(self, ready_filename: str) -> Optional[Path]:
        """
        Get the thumbnail path for a ready image filename.
        
        Args:
            ready_filename: Filename of the ready image (e.g., 'photo_20240101.png')
            
        Returns:
            Path to thumbnail if exists, None otherwise
        """
        stem = Path(ready_filename).stem
        
        # Check for webp first, then jpg
        webp_path = self.thumbnails_dir / f"{stem}.webp"
        jpg_path = self.thumbnails_dir / f"{stem}.jpg"
        
        if webp_path.exists():
            return webp_path
        elif jpg_path.exists():
            return jpg_path
        
        return None
    
    def backfill_thumbnails(self) -> Tuple[int, int]:
        """
        Generate thumbnails for all existing ready images that don't have one.
        
        Returns:
            Tuple of (generated_count, error_count)
        """
        import gc
        
        generated = 0
        errors = 0
        
        ready_images = self.get_ready_images()
        total = len(ready_images)
        
        logger.info(f"Backfilling thumbnails for {total} images...")
        
        for i, ready_path in enumerate(ready_images, 1):
            try:
                # Check if thumbnail already exists
                existing = self.get_thumbnail_path(ready_path.name)
                if existing:
                    continue
                
                # Generate thumbnail
                result = self._generate_thumbnail(ready_path)
                if result:
                    generated += 1
                    if generated % 10 == 0:
                        logger.info(f"Generated {generated} thumbnails...")
                else:
                    errors += 1
                
                # Free memory periodically
                if i % 20 == 0:
                    gc.collect()
                    
            except Exception as e:
                errors += 1
                logger.warning(f"Error generating thumbnail for {ready_path.name}: {e}")
        
        logger.info(f"Thumbnail backfill complete: {generated} generated, {errors} errors")
        return generated, errors
    
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
        
        # Prevent concurrent conversions (critical for Pi Zero memory)
        if self._is_converting:
            logger.warning("Conversion already in progress, skipping")
            return 0, 0
        
        if not self._conversion_lock.acquire(blocking=False):
            logger.warning("Could not acquire conversion lock, skipping")
            return 0, 0
        
        self._is_converting = True
        converted = 0
        errors = 0
        
        try:
            if delay_seconds is None:
                delay_seconds = config.get('conversion.delay_seconds', 5.0)
            
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
            
            import gc
            
            for i, source_path in enumerate(to_convert, 1):
                try:
                    logger.info(f"Converting {i}/{total}: {source_path.name}")
                    result = self.convert_image(source_path)
                    if result:
                        converted += 1
                        logger.debug(f"Successfully converted {i}/{total}")
                    else:
                        errors += 1
                        logger.warning(f"Failed to convert {i}/{total}: {source_path.name}")
                except MemoryError:
                    errors += 1
                    logger.error(f"MemoryError converting {source_path.name} - skipping")
                    gc.collect()
                except Exception as e:
                    errors += 1
                    logger.error(f"Unexpected error converting {source_path.name}: {e}")
                
                # Free memory after each conversion (important for Pi Zero)
                gc.collect()
                
                # Delay between conversions to avoid overloading Pi Zero
                if i < total and delay_seconds > 0:
                    logger.debug(f"Sleeping {delay_seconds}s before next conversion...")
                    time.sleep(delay_seconds)
            
            logger.info(f"Conversion complete: {converted} converted, {errors} errors")
        
        except Exception as e:
            logger.error(f"Batch conversion failed: {e}")
        
        finally:
            # Always release lock
            self._is_converting = False
            self._conversion_lock.release()
        
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
        
        # Count thumbnails
        thumb_count = 0
        if self.thumbnails_dir.exists():
            thumb_count = len(list(self.thumbnails_dir.glob('*.webp'))) + \
                         len(list(self.thumbnails_dir.glob('*.jpg')))
        
        return {
            'ready_count': len(ready_images),
            'ready_dir': str(self.ready_dir),
            'target_size': f"{self.width}x{self.height}",
            'orientation': self.orientation,
            'cache_entries': len(self._conversion_cache),
            'thumbnail_count': thumb_count,
            'thumbnails_dir': str(self.thumbnails_dir),
            'thumb_size': f"{self.thumb_width}x{self.thumb_height}",
        }


# Global converter instance
image_converter = ImageConverter()

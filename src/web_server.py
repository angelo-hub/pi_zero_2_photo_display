"""
Web Server Module
Flask-based web interface for authentication, status, and control.
"""

import os
import logging
import subprocess
from functools import wraps
from datetime import datetime
from pathlib import Path

from flask import Flask, render_template, request, jsonify, redirect, url_for, flash, send_from_directory, send_file
from io import BytesIO

from .config_manager import config
from .icloud_sync import icloud_sync, AuthStatus
from .image_converter import image_converter
from .photo_selector import photo_selector
from .display_manager import display_manager
from .button_handler import button_handler
from .battery_monitor import battery_monitor

logger = logging.getLogger(__name__)

# Create Flask app
app = Flask(__name__, 
            template_folder=str(Path(__file__).parent.parent / 'templates'),
            static_folder=str(Path(__file__).parent.parent / 'static'))

app.secret_key = os.urandom(24)


def auth_required(f):
    """Decorator for routes requiring authentication."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if not config.get('web.auth_enabled', False):
            return f(*args, **kwargs)
        
        auth = request.authorization
        if not auth:
            return ('Authentication required', 401, 
                    {'WWW-Authenticate': 'Basic realm="Photo Frame"'})
        
        if (auth.username != config.get('web.auth_username') or 
            auth.password != config.get('web.auth_password')):
            return ('Invalid credentials', 401,
                    {'WWW-Authenticate': 'Basic realm="Photo Frame"'})
        
        return f(*args, **kwargs)
    return decorated


# =============================================================================
# Main Dashboard
# =============================================================================

@app.route('/')
@auth_required
def dashboard():
    """Main dashboard page."""
    return render_template('dashboard.html',
                         icloud_status=icloud_sync.get_status(),
                         display_status=display_manager.get_status(),
                         battery_status=battery_monitor.get_status(),
                         converter_status=image_converter.get_status(),
                         selector_stats=photo_selector.get_stats(),
                         button_status=button_handler.get_status())


# =============================================================================
# iCloud Authentication
# =============================================================================

@app.route('/auth', methods=['GET'])
@auth_required
def auth_page():
    """iCloud authentication page."""
    return render_template('auth.html',
                         status=icloud_sync.get_status())


@app.route('/auth/login', methods=['POST'])
@auth_required
def auth_login():
    """Handle iCloud login."""
    password = request.form.get('password', '')
    mfa_code = request.form.get('mfa_code', '')
    
    if not password:
        flash('Password is required', 'error')
        return redirect(url_for('auth_page'))
    
    success, message = icloud_sync.authenticate(password, mfa_code if mfa_code else None)
    
    if success:
        flash(message, 'success')
        return redirect(url_for('dashboard'))
    else:
        flash(message, 'error')
        return redirect(url_for('auth_page'))


@app.route('/auth/status')
@auth_required
def auth_status():
    """Get current auth status as JSON."""
    icloud_sync.check_auth_status()
    return jsonify(icloud_sync.get_status())


# =============================================================================
# Photo Management
# =============================================================================

@app.route('/photos')
@auth_required
def photos_page():
    """Photos management page with pagination."""
    page = request.args.get('page', 1, type=int)
    per_page = 50
    
    ready_images = image_converter.get_ready_images()
    total_count = len(ready_images)
    total_pages = (total_count + per_page - 1) // per_page  # Ceiling division
    
    # Clamp page to valid range
    page = max(1, min(page, total_pages)) if total_pages > 0 else 1
    
    # Slice for current page
    start = (page - 1) * per_page
    end = start + per_page
    photos = ready_images[start:end]
    
    return render_template('photos.html',
                         photos=photos,
                         total_count=total_count,
                         page=page,
                         per_page=per_page,
                         total_pages=total_pages,
                         selector_stats=photo_selector.get_stats())


@app.route('/photos/sync', methods=['POST'])
@auth_required
def sync_photos():
    """Trigger iCloud sync."""
    success, count, message = icloud_sync.sync_photos()
    
    if success:
        # Convert new photos
        converted, errors = image_converter.convert_all_new()
        return jsonify({
            'success': True,
            'downloaded': count,
            'converted': converted,
            'errors': errors,
            'message': message
        })
    else:
        return jsonify({
            'success': False,
            'message': message
        }), 400


@app.route('/photos/convert', methods=['POST'])
@auth_required
def convert_photos():
    """Trigger image conversion."""
    converted, errors = image_converter.convert_all_new()
    return jsonify({
        'success': True,
        'converted': converted,
        'errors': errors
    })


@app.route('/photos/backfill-thumbnails', methods=['POST'])
@auth_required
def backfill_thumbnails():
    """Generate thumbnails for existing images that don't have one."""
    generated, errors = image_converter.backfill_thumbnails()
    return jsonify({
        'success': True,
        'generated': generated,
        'errors': errors,
        'message': f'Generated {generated} thumbnails ({errors} errors)'
    })


@app.route('/photos/preview/<path:filename>')
@auth_required
def photo_preview(filename):
    """Serve photo preview thumbnail (or full image if no thumbnail)."""
    # Try to serve thumbnail first (much smaller, faster loading)
    thumb_path = image_converter.get_thumbnail_path(filename)
    if thumb_path and thumb_path.exists():
        return send_from_directory(thumb_path.parent, thumb_path.name)
    
    # Fall back to full image if no thumbnail
    ready_dir = config.get_path('paths.ready_dir')
    return send_from_directory(ready_dir, filename)


@app.route('/photos/full/<path:filename>')
@auth_required
def photo_full(filename):
    """Serve full-size converted image."""
    ready_dir = config.get_path('paths.ready_dir')
    return send_from_directory(ready_dir, filename)


@app.route('/photos/favorites')
@auth_required
def favorites_page():
    """Favorites page with pagination."""
    page = request.args.get('page', 1, type=int)
    per_page = 50
    
    favorites = photo_selector.get_favorites()
    total_count = len(favorites)
    total_pages = (total_count + per_page - 1) // per_page if total_count > 0 else 1
    
    # Clamp page to valid range
    page = max(1, min(page, total_pages))
    
    # Slice for current page
    start = (page - 1) * per_page
    end = start + per_page
    photos = favorites[start:end]
    
    return render_template('photos.html',
                         photos=photos,
                         total_count=total_count,
                         page=page,
                         per_page=per_page,
                         total_pages=total_pages,
                         selector_stats=photo_selector.get_stats(),
                         show_favorites=True)


# =============================================================================
# Display Control
# =============================================================================

@app.route('/display')
@auth_required
def display_page():
    """Display control page."""
    current_photo = photo_selector.get_current_photo()
    return render_template('display.html',
                         status=display_manager.get_status(),
                         selector_stats=photo_selector.get_stats(),
                         current_photo=current_photo)


@app.route('/display/next', methods=['POST'])
@auth_required
def display_next():
    """Show next photo."""
    # Check battery
    if not battery_monitor.can_refresh():
        return jsonify({
            'success': False,
            'message': 'Battery too low for refresh'
        }), 400
    
    # Select and display next photo
    photo = photo_selector.select_next(force=True)
    if not photo:
        return jsonify({
            'success': False,
            'message': 'No photos available'
        }), 400
    
    success = display_manager.display_image(photo)
    stats = photo_selector.get_stats()
    
    return jsonify({
        'success': success,
        'photo': photo.name if photo else None,
        'is_favorite': stats.get('current_is_favorite', False),
        'message': 'Photo displayed' if success else 'Display failed'
    })


@app.route('/display/previous', methods=['POST'])
@auth_required
def display_previous():
    """Show previous photo from history."""
    # Check battery
    if not battery_monitor.can_refresh():
        return jsonify({
            'success': False,
            'message': 'Battery too low for refresh'
        }), 400
    
    photo = photo_selector.select_previous()
    if not photo:
        return jsonify({
            'success': False,
            'message': 'No previous photo in history'
        }), 400
    
    success = display_manager.display_image(photo)
    stats = photo_selector.get_stats()
    
    return jsonify({
        'success': success,
        'photo': photo.name if photo else None,
        'is_favorite': stats.get('current_is_favorite', False),
        'can_go_previous': stats.get('can_go_previous', False),
        'message': 'Previous photo displayed' if success else 'Display failed'
    })


@app.route('/display/specific', methods=['POST'])
@auth_required
def display_specific():
    """Display a specific photo by filename."""
    filename = request.json.get('filename') if request.is_json else request.form.get('filename')
    
    if not filename:
        return jsonify({
            'success': False,
            'message': 'No filename provided'
        }), 400
    
    # Check battery
    if not battery_monitor.can_refresh():
        return jsonify({
            'success': False,
            'message': 'Battery too low for refresh'
        }), 400
    
    ready_dir = config.get_path('paths.ready_dir')
    photo_path = ready_dir / filename
    
    if not photo_path.exists():
        return jsonify({
            'success': False,
            'message': 'Photo not found'
        }), 404
    
    photo = photo_selector.select_specific(photo_path)
    success = display_manager.display_image(photo)
    stats = photo_selector.get_stats()
    
    return jsonify({
        'success': success,
        'photo': photo.name if photo else None,
        'is_favorite': stats.get('current_is_favorite', False),
        'message': 'Photo displayed' if success else 'Display failed'
    })


@app.route('/display/skip', methods=['POST'])
@auth_required
def display_skip():
    """Skip current photo and show next."""
    # Check battery
    if not battery_monitor.can_refresh():
        return jsonify({
            'success': False,
            'message': 'Battery too low for refresh'
        }), 400
    
    photo = photo_selector.skip_current()
    if not photo:
        return jsonify({
            'success': False,
            'message': 'No photos available'
        }), 400
    
    success = display_manager.display_image(photo)
    
    return jsonify({
        'success': success,
        'photo': photo.name if photo else None,
        'message': 'Photo skipped' if success else 'Display failed'
    })


@app.route('/display/favorite', methods=['POST'])
@auth_required
def toggle_favorite():
    """Toggle favorite status for current photo."""
    is_favorite = photo_selector.toggle_favorite()
    current = photo_selector.get_current_photo()
    
    return jsonify({
        'success': True,
        'is_favorite': is_favorite,
        'photo': current.name if current else None,
        'message': 'Added to favorites' if is_favorite else 'Removed from favorites'
    })


@app.route('/display/pause', methods=['POST'])
@auth_required
def toggle_pause():
    """Toggle auto-rotation pause."""
    is_paused = photo_selector.toggle_pause()
    
    return jsonify({
        'success': True,
        'paused': is_paused,
        'message': 'Auto-rotation paused' if is_paused else 'Auto-rotation resumed'
    })


@app.route('/display/clear', methods=['POST'])
@auth_required
def display_clear():
    """Clear display."""
    success = display_manager.clear()
    return jsonify({
        'success': success,
        'message': 'Display cleared' if success else 'Clear failed'
    })


# =============================================================================
# System Status
# =============================================================================

@app.route('/status')
@auth_required
def system_status():
    """Get full system status as JSON."""
    return jsonify({
        'icloud': icloud_sync.get_status(),
        'display': display_manager.get_status(),
        'battery': battery_monitor.get_status(),
        'converter': image_converter.get_status(),
        'selector': photo_selector.get_stats(),
        'button': button_handler.get_status(),
        'timestamp': datetime.now().isoformat()
    })


@app.route('/api/next-photo', methods=['POST'])
@auth_required
def api_next_photo():
    """API endpoint for next photo (used by button handler)."""
    return display_next()


# =============================================================================
# Settings
# =============================================================================

@app.route('/settings')
@auth_required
def settings_page():
    """Settings page."""
    return render_template('settings.html', config=config.config)


@app.route('/settings/update', methods=['POST'])
@auth_required
def update_settings():
    """Update settings."""
    # Handle form data
    updates = request.form.to_dict()
    
    # Track previous rotation setting to detect changes
    old_rotation = config.get('display.rotation', 0)
    
    # Track which checkbox fields exist (they don't send value when unchecked)
    checkbox_fields = [
        'button.enabled',
        'battery.enabled', 
        'quiet_hours.enabled',
        'quiet_hours.disable_button',
        'web.auth_enabled',
    ]
    
    # Set unchecked checkboxes to False
    for field in checkbox_fields:
        if field not in updates:
            config.set(field, False)
    
    # Update config with proper type conversion
    for key, value in updates.items():
        if '.' in key:
            # Convert types appropriately
            if key in checkbox_fields:
                # Checkbox: any value means True
                config.set(key, True)
            elif value.isdigit():
                # Integer
                config.set(key, int(value))
            elif value.replace('.', '', 1).isdigit():
                # Float
                config.set(key, float(value))
            elif value.lower() in ('true', 'false'):
                # Boolean string
                config.set(key, value.lower() == 'true')
            else:
                # String
                config.set(key, value)
    
    config.save()
    
    # Reload config and dependent modules
    config.reload()
    icloud_sync.__init__()
    
    # Check if display rotation changed - trigger refresh to show new orientation
    new_rotation = config.get('display.rotation', 0)
    if new_rotation != old_rotation:
        current_photo = photo_selector.get_current_photo()
        if current_photo and current_photo.exists():
            logger.info(f"Display rotation changed ({old_rotation}° -> {new_rotation}°), refreshing display")
            display_manager.display_image(current_photo)
            flash(f'Settings saved! Display refreshed with {new_rotation}° rotation.', 'success')
        else:
            flash('Settings saved! Rotation will apply on next photo.', 'success')
    else:
        flash('Settings saved successfully!', 'success')
    
    return redirect(url_for('settings_page'))


# =============================================================================
# System Management
# =============================================================================

@app.route('/system')
@auth_required
def system_page():
    """System management page."""
    return render_template('system.html',
                         system_info=get_system_info(),
                         wifi_networks=get_wifi_networks(),
                         recent_logs=get_recent_logs(50))


def get_system_info() -> dict:
    """Get system information."""
    info = {
        'hostname': 'unknown',
        'ip_address': 'unknown',
        'cpu_temp': 'N/A',
        'cpu_usage': 'N/A',
        'memory_used': 'N/A',
        'memory_total': 'N/A',
        'memory_percent': 0,
        'disk_used': 'N/A',
        'disk_total': 'N/A',
        'disk_percent': 0,
        'uptime': 'N/A',
        'is_raspberry_pi': False,
    }
    
    try:
        import socket
        info['hostname'] = socket.gethostname()
        
        # Get IP address
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 80))
            info['ip_address'] = s.getsockname()[0]
        except:
            pass
        finally:
            s.close()
    except:
        pass
    
    # Check if Raspberry Pi
    try:
        with open('/proc/cpuinfo', 'r') as f:
            if 'Raspberry' in f.read():
                info['is_raspberry_pi'] = True
    except:
        pass
    
    # CPU Temperature (Raspberry Pi)
    try:
        with open('/sys/class/thermal/thermal_zone0/temp', 'r') as f:
            temp = int(f.read().strip()) / 1000
            info['cpu_temp'] = f"{temp:.1f}°C"
    except:
        pass
    
    # CPU Usage
    try:
        with open('/proc/loadavg', 'r') as f:
            load = f.read().split()[0]
            info['cpu_usage'] = f"{float(load)*100:.1f}%"
    except:
        pass
    
    # Memory
    try:
        with open('/proc/meminfo', 'r') as f:
            meminfo = {}
            for line in f:
                parts = line.split(':')
                if len(parts) == 2:
                    key = parts[0].strip()
                    value = parts[1].strip().split()[0]
                    meminfo[key] = int(value)
            
            total = meminfo.get('MemTotal', 0) / 1024  # MB
            available = meminfo.get('MemAvailable', 0) / 1024  # MB
            used = total - available
            
            info['memory_total'] = f"{total:.0f} MB"
            info['memory_used'] = f"{used:.0f} MB"
            info['memory_percent'] = int((used / total) * 100) if total > 0 else 0
    except:
        pass
    
    # Disk usage
    try:
        import shutil
        usage = shutil.disk_usage('/')
        info['disk_total'] = f"{usage.total / (1024**3):.1f} GB"
        info['disk_used'] = f"{usage.used / (1024**3):.1f} GB"
        info['disk_percent'] = int((usage.used / usage.total) * 100)
    except:
        pass
    
    # Uptime
    try:
        with open('/proc/uptime', 'r') as f:
            uptime_seconds = float(f.read().split()[0])
            days = int(uptime_seconds // 86400)
            hours = int((uptime_seconds % 86400) // 3600)
            minutes = int((uptime_seconds % 3600) // 60)
            
            if days > 0:
                info['uptime'] = f"{days}d {hours}h {minutes}m"
            elif hours > 0:
                info['uptime'] = f"{hours}h {minutes}m"
            else:
                info['uptime'] = f"{minutes}m"
    except:
        pass
    
    return info


def get_wifi_networks() -> list:
    """Get configured WiFi networks."""
    networks = []
    
    try:
        # Try to read wpa_supplicant.conf
        wpa_conf = Path('/etc/wpa_supplicant/wpa_supplicant.conf')
        if wpa_conf.exists():
            content = wpa_conf.read_text()
            
            # Parse networks (basic parsing)
            import re
            network_blocks = re.findall(r'network=\{([^}]+)\}', content, re.DOTALL)
            
            for block in network_blocks:
                ssid_match = re.search(r'ssid="([^"]+)"', block)
                priority_match = re.search(r'priority=(\d+)', block)
                
                if ssid_match:
                    networks.append({
                        'ssid': ssid_match.group(1),
                        'priority': int(priority_match.group(1)) if priority_match else 0,
                    })
        
        # Get current connection
        result = subprocess.run(['iwgetid', '-r'], capture_output=True, text=True, timeout=5)
        current_ssid = result.stdout.strip() if result.returncode == 0 else None
        
        for network in networks:
            network['connected'] = network['ssid'] == current_ssid
            
    except Exception as e:
        logger.warning(f"Could not get WiFi networks: {e}")
    
    return sorted(networks, key=lambda x: x.get('priority', 0), reverse=True)


def get_recent_logs(lines: int = 50) -> list:
    """Get recent log entries."""
    logs = []
    
    log_file = Path(config.get('logging.file', '/var/log/photoframe/photoframe.log'))
    
    try:
        if log_file.exists():
            with open(log_file, 'r') as f:
                all_lines = f.readlines()
                logs = all_lines[-lines:]
    except Exception as e:
        logs = [f"Could not read logs: {e}"]
    
    return logs


@app.route('/system/info')
@auth_required
def system_info_api():
    """Get system info as JSON."""
    return jsonify(get_system_info())


@app.route('/system/reboot', methods=['POST'])
@auth_required
def system_reboot():
    """Reboot the system."""
    try:
        subprocess.Popen(['sudo', 'reboot'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return jsonify({'success': True, 'message': 'Rebooting...'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500


@app.route('/system/shutdown', methods=['POST'])
@auth_required
def system_shutdown():
    """Shutdown the system."""
    try:
        subprocess.Popen(['sudo', 'shutdown', '-h', 'now'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return jsonify({'success': True, 'message': 'Shutting down...'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500


@app.route('/system/restart-service', methods=['POST'])
@auth_required
def restart_service():
    """Restart the photoframe service."""
    try:
        subprocess.Popen(['sudo', 'systemctl', 'restart', 'photoframe'], 
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return jsonify({'success': True, 'message': 'Service restarting...'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500


@app.route('/system/logs')
@auth_required
def get_logs_api():
    """Get recent logs as JSON."""
    lines = request.args.get('lines', 50, type=int)
    return jsonify({'logs': get_recent_logs(lines)})


@app.route('/system/wifi/add', methods=['POST'])
@auth_required
def add_wifi():
    """Add a WiFi network."""
    ssid = request.form.get('ssid', '').strip()
    password = request.form.get('password', '').strip()
    priority = request.form.get('priority', '1').strip()
    
    if not ssid or not password:
        return jsonify({'success': False, 'message': 'SSID and password required'}), 400
    
    try:
        # Build network block
        network_block = f'''
network={{
    ssid="{ssid}"
    psk="{password}"
    priority={priority}
}}
'''
        # Append to wpa_supplicant.conf
        result = subprocess.run(
            ['sudo', 'tee', '-a', '/etc/wpa_supplicant/wpa_supplicant.conf'],
            input=network_block,
            capture_output=True,
            text=True,
            timeout=10
        )
        
        if result.returncode == 0:
            # Reconfigure WiFi
            subprocess.run(['sudo', 'wpa_cli', '-i', 'wlan0', 'reconfigure'], 
                          capture_output=True, timeout=10)
            return jsonify({'success': True, 'message': f'Added network: {ssid}'})
        else:
            return jsonify({'success': False, 'message': 'Failed to add network'}), 500
            
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500


# =============================================================================
# Favicon and Meta Images
# =============================================================================

def generate_emoji_image(size: int, emoji: str = '🖼️', bg_color: tuple = (26, 26, 46)) -> BytesIO:
    """Generate a PNG image with an emoji centered on a background."""
    from PIL import Image, ImageDraw, ImageFont
    
    # Create image with background color
    img = Image.new('RGBA', (size, size), bg_color + (255,))
    draw = ImageDraw.Draw(img)
    
    # Try to use a system font that supports emoji
    font_size = int(size * 0.6)
    font = None
    
    # Common emoji font paths
    emoji_fonts = [
        '/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf',
        '/System/Library/Fonts/Apple Color Emoji.ttc',
        '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',
        '/usr/share/fonts/TTF/DejaVuSans.ttf',
    ]
    
    for font_path in emoji_fonts:
        try:
            font = ImageFont.truetype(font_path, font_size)
            break
        except (OSError, IOError):
            continue
    
    if font is None:
        # Fallback to default font
        font = ImageFont.load_default()
    
    # Get text bounding box and center it
    bbox = draw.textbbox((0, 0), emoji, font=font)
    text_width = bbox[2] - bbox[0]
    text_height = bbox[3] - bbox[1]
    
    x = (size - text_width) // 2 - bbox[0]
    y = (size - text_height) // 2 - bbox[1]
    
    draw.text((x, y), emoji, font=font, embedded_color=True)
    
    # Save to BytesIO
    buffer = BytesIO()
    img.save(buffer, format='PNG')
    buffer.seek(0)
    return buffer


@app.route('/favicon.ico')
def favicon_ico():
    """Serve favicon.ico as PNG for older browsers."""
    buffer = generate_emoji_image(32)
    return send_file(buffer, mimetype='image/png')


@app.route('/apple-touch-icon.png')
def apple_touch_icon():
    """Generate Apple touch icon with frame emoji."""
    buffer = generate_emoji_image(180)
    return send_file(buffer, mimetype='image/png')


@app.route('/og-image.png')
def og_image():
    """Generate Open Graph preview image with frame emoji."""
    buffer = generate_emoji_image(1200)
    return send_file(buffer, mimetype='image/png')


# =============================================================================
# Error Handlers
# =============================================================================

@app.errorhandler(404)
def not_found(e):
    return render_template('error.html', error='Page not found'), 404


@app.errorhandler(500)
def server_error(e):
    return render_template('error.html', error='Server error'), 500


def run_server():
    """Start the web server."""
    host = config.get('web.host', '0.0.0.0')
    port = config.get('web.port', 8080)
    
    logger.info(f"Starting web server on {host}:{port}")
    
    # Use threaded mode for handling multiple requests
    app.run(host=host, port=port, threaded=True, debug=False)

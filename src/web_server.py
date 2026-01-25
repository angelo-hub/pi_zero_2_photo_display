"""
Web Server Module
Flask-based web interface for authentication, status, and control.
"""

import os
import logging
from functools import wraps
from datetime import datetime
from pathlib import Path

from flask import Flask, render_template, request, jsonify, redirect, url_for, flash, send_from_directory

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
    """Photos management page."""
    ready_images = image_converter.get_ready_images()
    return render_template('photos.html',
                         photos=ready_images[:50],  # Limit to 50 for performance
                         total_count=len(ready_images),
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


@app.route('/photos/preview/<path:filename>')
@auth_required
def photo_preview(filename):
    """Serve photo preview."""
    ready_dir = config.get_path('paths.ready_dir')
    return send_from_directory(ready_dir, filename)


@app.route('/photos/favorites')
@auth_required
def favorites_page():
    """Favorites page."""
    favorites = photo_selector.get_favorites()
    return render_template('photos.html',
                         photos=favorites,
                         total_count=len(favorites),
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
    
    # Update config (basic implementation)
    for key, value in updates.items():
        if '.' in key:
            config.set(key, value)
    
    config.save()
    flash('Settings saved', 'success')
    return redirect(url_for('settings_page'))


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

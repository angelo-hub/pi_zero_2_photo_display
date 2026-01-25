# 🖼️ iCloud Photo Frame

A Raspberry Pi Zero 2 W powered digital photo frame using a Waveshare 7.3" 6-color e-Paper display. Automatically syncs photos from your private iCloud shared album.

## Features

- 📱 **iCloud Integration** - Automatically syncs photos from a shared iCloud album
- 🎨 **6-Color e-Paper Display** - Beautiful, paper-like display with Black, White, Yellow, Red, Blue, and Green
- ⏰ **Automatic Rotation** - Cycles through photos hourly (configurable)
- 🔘 **Physical Button** - Press to advance to the next photo
- 🔋 **Battery Monitoring** - Built-in UPS support with low battery protection
- 🌐 **Web Interface** - Control and configure via browser
- 📧 **Notifications** - Get notified when re-authentication is needed

## Hardware Requirements

- Raspberry Pi Zero 2 W
- [Waveshare RPi Zero PhotoPainter](https://www.waveshare.com/wiki/RPi_Zero_PhotoPainter) (7.3" 6-color e-Paper)
- MicroSD card (16GB+ recommended)
- Power supply (5V/2.5A)

## Quick Start

### 1. Flash Raspberry Pi OS

Use [Raspberry Pi Imager](https://www.raspberrypi.com/software/) to flash Raspberry Pi OS Lite (64-bit) to your SD card.

Enable SSH and configure WiFi during the imaging process.

### 2. Install

SSH into your Pi and run:

```bash
git clone https://github.com/angelo-hub/pi_zero_2_photo_display.git
cd pi_zero_2_photo_display
./scripts/install.sh
```

### 3. Reboot

```bash
sudo reboot
```

### 4. Configure via Web UI

After reboot, the service starts automatically. Access the web UI at:

```
http://photoframe.local:8080
```

**No manual file editing required!** Configure everything through the web interface:

1. **Settings** → Enter your iCloud email and album name
2. **iCloud** → Authenticate with your password and 2FA code
3. **System** → Add additional WiFi networks if needed

That's it! Photos will start syncing and displaying automatically.

## Configuration

**All settings are configurable via the Web UI** at `Settings`. No need to edit files manually!

Settings are stored in `config/config.yaml` and `config/config.local.yaml`.

### Key Settings

| Setting | Default | Description |
|---------|---------|-------------|
| `icloud.username` | - | Your Apple ID email |
| `icloud.album` | "Photo Frame" | Shared album name to sync |
| `icloud.sync_interval` | 86400 | Seconds between syncs (24h) |
| `display.rotation_interval` | 3600 | Seconds between photo changes (1h) |
| `display.selection_strategy` | "hybrid" | Photo selection: hybrid, sequential, random |
| `button.gpio_pin` | 4 | GPIO pin for physical button |
| `battery.min_refresh_percent` | 10 | Min battery % for display refresh |
| `web.port` | 8080 | Web interface port |

### Photo Selection Strategies

- **hybrid** (default): 70% chance to show newest photos, 30% oldest - keeps things fresh while revisiting memories
- **sequential**: Shows photos in order from newest to oldest
- **random**: Pure random selection

## Web Interface

Access at `http://photoframe.local:8080`:

- **Dashboard** - System overview and quick controls
- **Photos** - View and manage your photo library
- **Display** - Control the e-Paper display (next, previous, pause, favorites)
- **iCloud** - Authenticate and manage sync
- **System** - WiFi, reboot, logs, system info
- **Settings** - Configure all options (no file editing needed!)

## Project Structure

```
photoframe/
├── config/
│   ├── config.yaml          # Default configuration
│   └── config.local.yaml    # Your overrides (gitignored)
├── src/
│   ├── main.py              # Main service daemon
│   ├── config_manager.py    # Configuration handling
│   ├── icloud_sync.py       # iCloud photo download
│   ├── image_converter.py   # Image processing & dithering
│   ├── photo_selector.py    # Photo selection algorithm
│   ├── display_manager.py   # e-Paper display control
│   ├── button_handler.py    # Physical button input
│   ├── battery_monitor.py   # UPS battery monitoring
│   └── web_server.py        # Flask web interface
├── lib/
│   └── waveshare_epd/       # Waveshare e-Paper drivers
├── templates/               # Web UI HTML templates
├── static/                  # CSS and JavaScript
├── scripts/
│   └── install.sh           # Installation script
└── systemd/
    └── photoframe.service   # Systemd service file
```

## Updating

To update an existing installation:

```bash
cd ~/pi_zero_2_photo_display
git pull
./scripts/update.sh
```

The update script will:
- Pull latest changes from git
- Backup your configuration
- Copy updated files (src, templates, static, lib)
- Update Python dependencies
- Restart the service

Your `config.local.yaml` settings are preserved.

## Commands

### Service Control

```bash
# Start service
sudo systemctl start photoframe

# Stop service
sudo systemctl stop photoframe

# Restart service
sudo systemctl restart photoframe

# View status
sudo systemctl status photoframe

# View logs
journalctl -u photoframe -f
```

### Manual Operations

```bash
# Activate virtual environment
source ~/photoframe/venv/bin/activate

# Manual sync
python -c "from src.icloud_sync import icloud_sync; print(icloud_sync.sync_photos())"

# Convert all images
python -c "from src.image_converter import image_converter; print(image_converter.convert_all_new())"
```

## Troubleshooting

### iCloud Authentication Issues

- Session tokens expire after ~2 months
- You'll be notified via web UI when re-authentication is needed
- If 2FA fails, try generating a new code from your trusted device

### Display Not Working

1. Check SPI is enabled: `ls /dev/spidev*`
2. Check I2C is enabled: `ls /dev/i2c*`
3. Verify connections to GPIO pins
4. Check logs: `tail -f /var/log/photoframe/photoframe.log`

### Button Not Responding

1. Check the correct GPIO pin in config (default: GPIO 4)
2. Verify button is connected to the right pin on your board
3. Check button handler status in web UI

### Battery Monitoring Not Working

1. Check I2C is enabled
2. Verify INA219 chip address (default: 0x43)
3. Run `i2cdetect -y 1` to scan I2C devices

## License

MIT License - feel free to modify and share!

## Acknowledgments

- [Waveshare](https://www.waveshare.com/) for the PhotoPainter hardware and drivers
- [icloudpd](https://github.com/icloud-photos-downloader/icloud_photos_downloader) for iCloud photo download
- [Dylan's PaperPiAI](https://github.com/dylski/PaperPiAI) for inspiration

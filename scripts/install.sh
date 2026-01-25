#!/bin/bash
# Photo Frame Installation Script for Raspberry Pi Zero 2 W

set -e

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${GREEN}"
echo "╔════════════════════════════════════════════════════════════╗"
echo "║           iCloud Photo Frame Installer                     ║"
echo "║           For Raspberry Pi Zero 2 W                        ║"
echo "╚════════════════════════════════════════════════════════════╝"
echo -e "${NC}"

# Check if running as root
if [ "$EUID" -eq 0 ]; then
    echo -e "${RED}Please don't run this script as root${NC}"
    exit 1
fi

# Check if on Raspberry Pi
if ! grep -q "Raspberry" /proc/cpuinfo 2>/dev/null; then
    echo -e "${YELLOW}Warning: This doesn't appear to be a Raspberry Pi${NC}"
    read -p "Continue anyway? (y/n) " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        exit 1
    fi
fi

INSTALL_DIR="$HOME/photoframe"
PHOTOS_DIR="$HOME/photos"
CONFIG_DIR="$HOME/.photoframe"
LOG_DIR="/var/log/photoframe"

echo -e "${GREEN}[1/8] Creating directories...${NC}"
mkdir -p "$INSTALL_DIR"
mkdir -p "$PHOTOS_DIR/raw"
mkdir -p "$PHOTOS_DIR/ready"
mkdir -p "$CONFIG_DIR"
sudo mkdir -p "$LOG_DIR"
sudo chown $USER:$USER "$LOG_DIR"

echo -e "${GREEN}[2/8] Updating system packages...${NC}"
sudo apt-get update
sudo apt-get upgrade -y

echo -e "${GREEN}[3/8] Installing system dependencies...${NC}"
sudo apt-get install -y \
    python3-pip \
    python3-dev \
    python3-venv \
    python3-pil \
    python3-numpy \
    python3-spidev \
    python3-gpiozero \
    python3-smbus \
    libjpeg-dev \
    zlib1g-dev \
    libfreetype-dev \
    liblcms2-dev \
    libopenjp2-7-dev \
    libtiff-dev \
    git \
    wireless-tools

echo -e "${GREEN}[4/8] Enabling SPI and I2C interfaces...${NC}"
# Enable SPI
if ! grep -q "^dtparam=spi=on" /boot/config.txt; then
    echo "dtparam=spi=on" | sudo tee -a /boot/config.txt
fi

# Enable I2C
if ! grep -q "^dtparam=i2c_arm=on" /boot/config.txt; then
    echo "dtparam=i2c_arm=on" | sudo tee -a /boot/config.txt
fi

echo -e "${GREEN}[5/8] Copying project files...${NC}"
# Assuming script is run from project directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

cp -r "$SCRIPT_DIR/src" "$INSTALL_DIR/"
cp -r "$SCRIPT_DIR/lib" "$INSTALL_DIR/" 2>/dev/null || mkdir -p "$INSTALL_DIR/lib"
cp -r "$SCRIPT_DIR/templates" "$INSTALL_DIR/"
cp -r "$SCRIPT_DIR/static" "$INSTALL_DIR/"
cp "$SCRIPT_DIR/requirements.txt" "$INSTALL_DIR/"

# Copy Waveshare drivers
if [ -d "$SCRIPT_DIR/Waveshare_E-Paper/lib/waveshare_epd" ]; then
    cp -r "$SCRIPT_DIR/Waveshare_E-Paper/lib/waveshare_epd" "$INSTALL_DIR/lib/"
fi

echo -e "${GREEN}[6/8] Setting up Python virtual environment...${NC}"
# Use --system-site-packages to access apt-installed packages (gpiozero, spidev, etc.)
python3 -m venv --system-site-packages "$INSTALL_DIR/venv"
source "$INSTALL_DIR/venv/bin/activate"

# Upgrade pip
pip install --upgrade pip 2>/dev/null || pip install --break-system-packages --upgrade pip

# Install Python dependencies
# Note: Some packages may already be available via system, pip will skip them
echo -e "${GREEN}Installing Python packages (this may take a while on Pi Zero)...${NC}"

# Try installing, with fallback for Bookworm's externally-managed-environment
if pip install -r "$INSTALL_DIR/requirements.txt"; then
    echo "  ✓ Packages installed"
elif pip install --break-system-packages -r "$INSTALL_DIR/requirements.txt"; then
    echo "  ✓ Packages installed (with --break-system-packages)"
else
    echo -e "${YELLOW}Trying individual critical packages...${NC}"
    pip install --break-system-packages icloudpd Flask APScheduler PyYAML Pillow requests numpy || true
fi

deactivate

echo -e "${GREEN}[7/8] Setting up configuration...${NC}"
# Copy default config if not exists
mkdir -p "$INSTALL_DIR/config"
if [ -f "$SCRIPT_DIR/config/config.yaml" ]; then
    cp "$SCRIPT_DIR/config/config.yaml" "$INSTALL_DIR/config/"
fi

# Create empty local config (will be populated via web UI)
if [ ! -f "$INSTALL_DIR/config/config.local.yaml" ]; then
    cat > "$INSTALL_DIR/config/config.local.yaml" << 'EOF'
# Local configuration - managed via Web UI
# Visit http://photoframe.local:8080/settings to configure
EOF
fi

echo -e "${GREEN}[8/8] Installing systemd service...${NC}"
# Create service file with correct paths
cat > /tmp/photoframe.service << EOF
[Unit]
Description=Photo Frame Service
After=network.target
Wants=network-online.target

[Service]
Type=simple
User=$USER
Group=$USER
WorkingDirectory=$INSTALL_DIR
ExecStart=$INSTALL_DIR/venv/bin/python $INSTALL_DIR/src/main.py
Restart=on-failure
RestartSec=10

Environment=PYTHONUNBUFFERED=1
Environment=HOME=$HOME

StandardOutput=append:$LOG_DIR/service.log
StandardError=append:$LOG_DIR/service.log

[Install]
WantedBy=multi-user.target
EOF

sudo mv /tmp/photoframe.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable photoframe.service

echo ""
echo -e "${GREEN}╔════════════════════════════════════════════════════════════╗"
echo "║           Installation Complete! 🎉                         ║"
echo "╚════════════════════════════════════════════════════════════╝${NC}"
echo ""
echo "Next steps:"
echo ""
echo "1. Reboot to enable SPI and I2C:"
echo "   sudo reboot"
echo ""
echo "2. After reboot, access the web UI at:"
echo "   http://$(hostname).local:8080"
echo "   or"
echo "   http://$(hostname -I | awk '{print $1}'):8080"
echo ""
echo "3. Configure everything via the web UI:"
echo "   - Go to Settings → Set your iCloud email and album name"
echo "   - Go to iCloud → Enter password and 2FA code"
echo "   - Go to System → Add WiFi networks if needed"
echo ""
echo "   No manual file editing required! 🎉"
echo ""
echo "To view logs:"
echo "   journalctl -u photoframe -f"
echo "   or via Web UI: System → Logs"
echo ""

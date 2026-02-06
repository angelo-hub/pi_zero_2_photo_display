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

# Detect architecture
ARCH=$(uname -m)
echo -e "${GREEN}Detected architecture: ${ARCH}${NC}"

echo -e "${GREEN}[1/9] Setting up swap for Pi Zero 2 W...${NC}"
# Pi Zero 2 W has only 512MB RAM, needs file-based swap (not just zram)
# Check specifically for our swapfile, not just any swap
if [ ! -f /swapfile ] || [ $(stat -f%z /swapfile 2>/dev/null || stat -c%s /swapfile 2>/dev/null || echo 0) -lt 1000000000 ]; then
    echo "  Creating 1GB swapfile (this takes a minute)..."
    
    # Disable swapfile if it exists
    sudo swapoff /swapfile 2>/dev/null || true
    
    # Remove old swapfile if exists
    sudo rm -f /swapfile 2>/dev/null || true
    
    # Create 1GB swap file
    sudo dd if=/dev/zero of=/swapfile bs=1M count=1024 status=progress
    sudo chmod 600 /swapfile
    sudo mkswap /swapfile
    sudo swapon /swapfile
    
    # Make permanent - remove old entry first, then add
    sudo sed -i '/\/swapfile/d' /etc/fstab
    echo "/swapfile none swap sw 0 0" | sudo tee -a /etc/fstab
    
    echo "  ✓ 1GB swapfile created and enabled"
else
    # Swapfile exists, make sure it's active
    if ! swapon --show | grep -q "/swapfile"; then
        echo "  Enabling existing swapfile..."
        sudo swapon /swapfile
    fi
    echo "  ✓ Swapfile already configured"
fi

# Verify swap is working
TOTAL_SWAP=$(free -m | awk '/^Swap:/ {print $2}')
echo "  Total swap available: ${TOTAL_SWAP}MB"

echo -e "${GREEN}[2/9] Creating directories...${NC}"
mkdir -p "$INSTALL_DIR"
mkdir -p "$PHOTOS_DIR/raw"
mkdir -p "$PHOTOS_DIR/ready"
mkdir -p "$CONFIG_DIR"
sudo mkdir -p "$LOG_DIR"
sudo chown $USER:$USER "$LOG_DIR"

echo -e "${GREEN}[3/9] Updating system packages...${NC}"
sudo apt-get update
sudo apt-get upgrade -y

echo -e "${GREEN}[4/9] Installing system dependencies...${NC}"
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
    libheif-dev \
    git \
    wget \
    wireless-tools

echo -e "${GREEN}[5/9] Enabling SPI and I2C interfaces...${NC}"
# Enable SPI (check both /boot/config.txt and /boot/firmware/config.txt)
CONFIG_FILE="/boot/config.txt"
if [ -f "/boot/firmware/config.txt" ]; then
    CONFIG_FILE="/boot/firmware/config.txt"
fi

if ! grep -q "^dtparam=spi=on" "$CONFIG_FILE"; then
    echo "dtparam=spi=on" | sudo tee -a "$CONFIG_FILE"
fi

if ! grep -q "^dtparam=i2c_arm=on" "$CONFIG_FILE"; then
    echo "dtparam=i2c_arm=on" | sudo tee -a "$CONFIG_FILE"
fi

echo -e "${GREEN}[6/9] Copying project files...${NC}"
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

echo -e "${GREEN}[7/9] Setting up Python virtual environment...${NC}"
# Use --system-site-packages to access apt-installed packages (gpiozero, spidev, etc.)
python3 -m venv --system-site-packages "$INSTALL_DIR/venv"
source "$INSTALL_DIR/venv/bin/activate"

# Upgrade pip and install build tools
pip install --upgrade pip setuptools wheel 2>/dev/null || \
    pip install --break-system-packages --upgrade pip setuptools wheel

# Install Python dependencies
echo -e "${GREEN}Installing Python packages (this may take a while on Pi Zero)...${NC}"

# Install icloudpd separately with special handling
echo "  Installing icloudpd (may take several minutes)..."
if pip install icloudpd; then
    echo "  ✓ icloudpd installed"
elif pip install --break-system-packages icloudpd; then
    echo "  ✓ icloudpd installed (with --break-system-packages)"
elif pip install --break-system-packages --no-binary :all: icloudpd; then
    echo "  ✓ icloudpd installed from source"
else
    echo -e "${YELLOW}  Trying icloudpd from GitHub...${NC}"
    pip install --break-system-packages "git+https://github.com/icloud-photos-downloader/icloud_photos_downloader.git@v1.23.4" || \
        echo -e "${RED}  Warning: icloudpd installation failed. You may need to install manually.${NC}"
fi

# Install remaining dependencies
echo "  Installing other packages..."
if pip install -r "$INSTALL_DIR/requirements.txt"; then
    echo "  ✓ Packages installed"
elif pip install --break-system-packages -r "$INSTALL_DIR/requirements.txt"; then
    echo "  ✓ Packages installed (with --break-system-packages)"
else
    echo -e "${YELLOW}Trying individual critical packages...${NC}"
    pip install --break-system-packages Flask APScheduler PyYAML Pillow requests numpy || true
fi

# Verify icloudpd is available
if command -v icloudpd &>/dev/null || [ -f "$INSTALL_DIR/venv/bin/icloudpd" ]; then
    echo "  ✓ icloudpd verified"
else
    echo -e "${YELLOW}  Note: icloudpd may need manual installation after reboot${NC}"
fi

deactivate

echo -e "${GREEN}[8/10] Setting up configuration...${NC}"
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

echo -e "${GREEN}[9/10] Configuring sudo for WiFi (list/add networks)...${NC}"
# Allow the service user to read wpa_supplicant.conf and add networks (no password)
WIFI_SUDOERS="/etc/sudoers.d/photoframe-wifi"
if [ ! -f "$WIFI_SUDOERS" ]; then
    # Allow both /usr/sbin and /sbin wpa_cli (distro-dependent)
    sudo tee "$WIFI_SUDOERS" > /dev/null << SUDOEOF
# Photo Frame: list and add WiFi networks without password
$USER ALL=(ALL) NOPASSWD: /bin/cat /etc/wpa_supplicant/wpa_supplicant.conf
$USER ALL=(ALL) NOPASSWD: /usr/bin/tee -a /etc/wpa_supplicant/wpa_supplicant.conf
$USER ALL=(ALL) NOPASSWD: /usr/sbin/wpa_cli -i wlan0 reconfigure
$USER ALL=(ALL) NOPASSWD: /sbin/wpa_cli -i wlan0 reconfigure
SUDOEOF
    sudo chmod 440 "$WIFI_SUDOERS"
    echo "  ✓ $USER can list/add WiFi via web UI"
else
    echo "  ✓ WiFi sudoers already present"
fi

echo -e "${GREEN}[10/10] Installing systemd service...${NC}"
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
echo "║           Installation Complete! ✓                          ║"
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
echo "To view logs:"
echo "   journalctl -u photoframe -f"
echo "   or via Web UI: System → Logs"
echo ""

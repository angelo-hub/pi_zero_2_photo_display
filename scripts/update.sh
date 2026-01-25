#!/bin/bash
# Photo Frame Update Script
# Updates an existing installation with the latest changes

set -e

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${GREEN}"
echo "╔════════════════════════════════════════════════════════════╗"
echo "║           iCloud Photo Frame Updater                       ║"
echo "╚════════════════════════════════════════════════════════════╝"
echo -e "${NC}"

# Determine directories
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
INSTALL_DIR="${PHOTOFRAME_DIR:-$HOME/photoframe}"

# Check if installation exists
if [ ! -d "$INSTALL_DIR" ]; then
    echo -e "${RED}Error: Installation not found at $INSTALL_DIR${NC}"
    echo "Run install.sh first, or set PHOTOFRAME_DIR environment variable."
    exit 1
fi

echo -e "${GREEN}Updating from:${NC} $SCRIPT_DIR"
echo -e "${GREEN}Updating to:${NC}   $INSTALL_DIR"
echo ""

# Step 1: Pull latest changes (if in a git repo)
if [ -d "$SCRIPT_DIR/.git" ]; then
    echo -e "${GREEN}[1/6] Pulling latest changes from git...${NC}"
    cd "$SCRIPT_DIR"
    git pull || echo -e "${YELLOW}Warning: git pull failed, continuing with local files${NC}"
else
    echo -e "${YELLOW}[1/6] Not a git repo, skipping pull${NC}"
fi

# Step 2: Backup current config
echo -e "${GREEN}[2/6] Backing up configuration...${NC}"
if [ -f "$INSTALL_DIR/config/config.local.yaml" ]; then
    cp "$INSTALL_DIR/config/config.local.yaml" "$INSTALL_DIR/config/config.local.yaml.backup"
    echo "  Backed up config.local.yaml"
fi

# Step 3: Copy updated files
echo -e "${GREEN}[3/6] Copying updated files...${NC}"

# Source code
if [ -d "$SCRIPT_DIR/src" ]; then
    cp -r "$SCRIPT_DIR/src/"* "$INSTALL_DIR/src/"
    echo "  ✓ Updated src/"
fi

# Templates
if [ -d "$SCRIPT_DIR/templates" ]; then
    cp -r "$SCRIPT_DIR/templates/"* "$INSTALL_DIR/templates/"
    echo "  ✓ Updated templates/"
fi

# Static files (CSS, JS)
if [ -d "$SCRIPT_DIR/static" ]; then
    cp -r "$SCRIPT_DIR/static/"* "$INSTALL_DIR/static/"
    echo "  ✓ Updated static/"
fi

# Waveshare drivers
if [ -d "$SCRIPT_DIR/lib" ]; then
    mkdir -p "$INSTALL_DIR/lib"
    cp -r "$SCRIPT_DIR/lib/"* "$INSTALL_DIR/lib/"
    echo "  ✓ Updated lib/"
fi

# Default config (not local overrides)
if [ -f "$SCRIPT_DIR/config/config.yaml" ]; then
    cp "$SCRIPT_DIR/config/config.yaml" "$INSTALL_DIR/config/"
    echo "  ✓ Updated config/config.yaml"
fi

# Step 4: Verify swap and install system dependencies
echo -e "${GREEN}[4/6] Checking system requirements...${NC}"

# Check/fix swapfile (Pi Zero 2 W needs this)
if [ ! -f /swapfile ]; then
    echo "  Creating 1GB swapfile..."
    sudo dd if=/dev/zero of=/swapfile bs=1M count=1024 status=progress
    sudo chmod 600 /swapfile
    sudo mkswap /swapfile
    sudo swapon /swapfile
    sudo sed -i '/\/swapfile/d' /etc/fstab
    echo "/swapfile none swap sw 0 0" | sudo tee -a /etc/fstab
    echo "  ✓ Swapfile created"
elif ! swapon --show | grep -q "/swapfile"; then
    echo "  Enabling swapfile..."
    sudo swapon /swapfile 2>/dev/null || true
    echo "  ✓ Swapfile enabled"
else
    echo "  ✓ Swapfile OK"
fi

# Show swap status
TOTAL_SWAP=$(free -m | awk '/^Swap:/ {print $2}')
echo "  Total swap: ${TOTAL_SWAP}MB"

# Install libheif for HEIC support if not present
if ! dpkg -s libheif-dev &>/dev/null; then
    echo "  Installing libheif-dev for HEIC support..."
    sudo apt-get update
    sudo apt-get install -y libheif-dev
    echo "  ✓ libheif-dev installed"
else
    echo "  ✓ System dependencies OK"
fi

# Step 5: Update Python dependencies
echo -e "${GREEN}[5/6] Updating Python dependencies...${NC}"
if [ -f "$INSTALL_DIR/venv/bin/activate" ]; then
    source "$INSTALL_DIR/venv/bin/activate"
    
    # Try normal pip first, then with --break-system-packages for Bookworm
    echo "  Installing Python packages..."
    if pip install --upgrade -r "$SCRIPT_DIR/requirements.txt"; then
        echo "  ✓ Dependencies updated"
    elif pip install --break-system-packages --upgrade -r "$SCRIPT_DIR/requirements.txt"; then
        echo "  ✓ Dependencies updated (with --break-system-packages)"
    else
        echo -e "${YELLOW}  Warning: pip install had issues, trying individual packages...${NC}"
        # Try installing critical packages individually
        pip install --break-system-packages icloudpd Flask APScheduler PyYAML Pillow requests || true
    fi
    
    deactivate
else
    echo -e "${YELLOW}  Warning: Virtual environment not found, skipping pip${NC}"
fi

# Step 6: Restart service
echo -e "${GREEN}[6/6] Restarting service...${NC}"
if systemctl is-active --quiet photoframe; then
    sudo systemctl restart photoframe
    echo "  ✓ Service restarted"
else
    echo -e "${YELLOW}  Service not running, starting...${NC}"
    sudo systemctl start photoframe || echo -e "${YELLOW}  Could not start service${NC}"
fi

echo ""
echo -e "${GREEN}╔════════════════════════════════════════════════════════════╗"
echo "║           Update Complete! ✓                               ║"
echo "╚════════════════════════════════════════════════════════════╝${NC}"
echo ""
echo "Your configuration (config.local.yaml) was preserved."
echo ""
echo "Access the web UI at:"
echo "  http://$(hostname).local:8080"
if command -v hostname &> /dev/null; then
    IP=$(hostname -I 2>/dev/null | awk '{print $1}')
    if [ -n "$IP" ]; then
        echo "  http://$IP:8080"
    fi
fi
echo ""
echo "To check service status:"
echo "  sudo systemctl status photoframe"
echo ""

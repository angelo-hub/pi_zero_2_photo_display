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
    echo -e "${GREEN}[1/5] Pulling latest changes from git...${NC}"
    cd "$SCRIPT_DIR"
    git pull || echo -e "${YELLOW}Warning: git pull failed, continuing with local files${NC}"
else
    echo -e "${YELLOW}[1/5] Not a git repo, skipping pull${NC}"
fi

# Step 2: Backup current config
echo -e "${GREEN}[2/5] Backing up configuration...${NC}"
if [ -f "$INSTALL_DIR/config/config.local.yaml" ]; then
    cp "$INSTALL_DIR/config/config.local.yaml" "$INSTALL_DIR/config/config.local.yaml.backup"
    echo "  Backed up config.local.yaml"
fi

# Step 3: Copy updated files
echo -e "${GREEN}[3/5] Copying updated files...${NC}"

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

# Step 4: Update Python dependencies
echo -e "${GREEN}[4/5] Updating Python dependencies...${NC}"
if [ -f "$INSTALL_DIR/venv/bin/activate" ]; then
    source "$INSTALL_DIR/venv/bin/activate"
    pip install --quiet --upgrade -r "$SCRIPT_DIR/requirements.txt" 2>/dev/null || \
        pip install --quiet -r "$SCRIPT_DIR/requirements.txt" || \
        echo -e "${YELLOW}Warning: Some pip packages may have failed${NC}"
    deactivate
    echo "  ✓ Dependencies updated"
else
    echo -e "${YELLOW}  Warning: Virtual environment not found, skipping pip${NC}"
fi

# Step 5: Restart service
echo -e "${GREEN}[5/5] Restarting service...${NC}"
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

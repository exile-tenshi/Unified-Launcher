#!/usr/bin/env bash
# Tenshi Build System — macOS/Linux
# Usage: bash build_exe.sh

set -e

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'

echo ""
echo "  TENSHI — Desktop App Builder v3.0"
echo "  ──────────────────────────────────"
echo ""

# Step 1: Dependencies
echo -e "[1/4] ${YELLOW}Installing Python dependencies...${NC}"
pip install -r requirements_desktop.txt --quiet
echo -e "      ${GREEN}Done.${NC}"

# Step 2: Clean
echo -e "[2/4] ${YELLOW}Cleaning previous builds...${NC}"
rm -rf dist/TenshiVoice build/ 2>/dev/null || true
echo -e "      ${GREEN}Done.${NC}"

# Step 3: Build
echo -e "[3/4] ${YELLOW}Building (this may take 2-5 minutes)...${NC}"

# On macOS produce a .app bundle; on Linux produce a binary
if [[ "$OSTYPE" == "darwin"* ]]; then
    pyinstaller tenshi.spec --noconfirm --clean
    echo -e "      ${GREEN}macOS .app bundle created.${NC}"
else
    # Linux: produce single binary
    pyinstaller --onefile --windowed --name TenshiVoice \
        --add-data "../TenshiWeb/tenshi_logo.png:." \
        --hidden-import customtkinter \
        --hidden-import cryptography \
        --hidden-import cryptography.fernet \
        hub.py --noconfirm --clean
fi

# Step 4: Verify
echo -e "[4/4] ${YELLOW}Verifying output...${NC}"
if [[ "$OSTYPE" == "darwin"* ]]; then
    TARGET="dist/TenshiVoice.app"
else
    TARGET="dist/TenshiVoice"
fi

if [[ -e "$TARGET" ]]; then
    SIZE=$(du -sh "$TARGET" | cut -f1)
    echo ""
    echo -e "  ${GREEN}✓ Build successful!${NC}"
    echo -e "  ${GREEN}✓ Output: $TARGET  ($SIZE)${NC}"
    echo ""
    echo "  Upload dist/TenshiVoice (or zip the .app) to"
    echo "  files.tenshi.lol to make it available for download."
    echo ""
else
    echo -e "  ${RED}✗ Build failed — output not found.${NC}"
    exit 1
fi

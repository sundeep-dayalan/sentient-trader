#!/bin/bash

# Sentient Trader Code Formatter
# This script formats all Python files using Black and all other files using Prettier.

# Exit immediately if a command exits with a non-zero status
set -e

# Define ANSI color codes for premium terminal output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
BLUE='\033[0;34m'
MAGENTA='\033[0;35m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m' # No Color

# Print header
echo -e "${BOLD}${MAGENTA}=============================================${NC}"
echo -e "${BOLD}${CYAN}        SENTIENT TRADER CODE FORMATTER        ${NC}"
echo -e "${BOLD}${MAGENTA}=============================================${NC}"
echo ""

# Get the directory of this script to run relatively
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"
cd "$SCRIPT_DIR"

# -------------------------------------------------------------
# 1. Format Python Files using Black
# -------------------------------------------------------------
echo -e "${BOLD}${BLUE}[1/2] Formatting Python files with Black...${NC}"

# Find the best Black executable
BLACK_CMD=""
if [ -f ".venv/bin/black" ]; then
    BLACK_CMD=".venv/bin/black"
elif command -v black &> /dev/null; then
    BLACK_CMD="black"
fi

# Install Black if not found
if [ -z "$BLACK_CMD" ]; then
    echo -e "${YELLOW}Black formatter not found. Installing Black in the root virtual environment...${NC}"
    if [ -f ".venv/bin/pip" ]; then
        .venv/bin/pip install black
        BLACK_CMD=".venv/bin/black"
        echo -e "${GREEN}Successfully installed Black!${NC}"
    else
        echo -e "${RED}Error: Root virtual environment (.venv) or global pip not found. Please install Black manually.${NC}"
        exit 1
    fi
fi

# Run Black
echo -e "${CYAN}Running: $BLACK_CMD backend/${NC}"
set +e # allow non-zero exit status for black if some files are reformatted
"$BLACK_CMD" backend/
BLACK_STATUS=$?
set -e

if [ $BLACK_STATUS -eq 0 ] || [ $BLACK_STATUS -eq 1 ]; then
    echo -e "${GREEN}✓ Python files checked/formatted successfully!${NC}"
else
    echo -e "${RED}✗ Black formatting failed with exit code $BLACK_STATUS${NC}"
fi
echo ""

# -------------------------------------------------------------
# 2. Format JS/TS/CSS/JSON/MD using Prettier
# -------------------------------------------------------------
echo -e "${BOLD}${BLUE}[2/2] Formatting other files with Prettier...${NC}"

if command -v npx &> /dev/null; then
    echo -e "${CYAN}Running: npx prettier --write .${NC}"
    set +e
    npx prettier --write .
    PRETTIER_STATUS=$?
    set -e
    
    if [ $PRETTIER_STATUS -eq 0 ]; then
        echo -e "${GREEN}✓ Web, configuration, and markdown files formatted successfully!${NC}"
    else
        echo -e "${RED}✗ Prettier formatting failed with exit code $PRETTIER_STATUS${NC}"
    fi
else
    echo -e "${YELLOW}Warning: npx/Node.js not found. Skipping Prettier formatting.${NC}"
    echo -e "${YELLOW}Please install Node.js/npm to format frontend, css, markdown, and config files.${NC}"
fi

echo ""
echo -e "${BOLD}${GREEN}=============================================${NC}"
echo -e "${BOLD}${GREEN}    ✨  PROJECT FORMATTING COMPLETE!  ✨     ${NC}"
echo -e "${BOLD}${GREEN}=============================================${NC}"

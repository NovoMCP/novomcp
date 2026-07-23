#!/bin/bash
# Build the MCP UI apps and deploy them into the engine's ui-apps/ dir.
# Usage: ./scripts/build-and-deploy.sh
# The engine directory is resolved via ENGINE_ROOT (env var); defaults to
# the sibling ../novomcp directory.

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
ENGINE_ROOT="${ENGINE_ROOT:-$(dirname "$PROJECT_ROOT")/novomcp}"
UI_APPS_DIR="$ENGINE_ROOT/ui-apps"

echo "=========================================="
echo "  NovoMCP Apps - Build & Deploy"
echo "=========================================="
echo ""

cd "$PROJECT_ROOT"

# Install dependencies if needed
if [ ! -d "node_modules" ]; then
    echo "Installing dependencies..."
    npm install
fi

# Type check
echo "Type checking..."
npx tsc --noEmit

# Create dist directories
mkdir -p dist/apps

# Build each app
APPS=("molecule-viewer" "admet-dashboard" "research-explorer" "structure-viewer" "credit-usage" "faves-dashboard" "docking-viewer" "lead-comparison")

for APP in "${APPS[@]}"; do
    echo ""
    echo "Building $APP..."
    INPUT="html/${APP}.html" npx vite build

    # The output preserves the html/ path structure
    mv "dist/html/${APP}.html" "dist/apps/${APP}.html"
done

# Clean up empty html directory
rmdir dist/html 2>/dev/null || true

echo ""
echo "=========================================="
echo "  Deploying to $ENGINE_ROOT"
echo "=========================================="
echo ""

# Create target directories
for APP in "${APPS[@]}"; do
    mkdir -p "$UI_APPS_DIR/$APP"
    cp "dist/apps/${APP}.html" "$UI_APPS_DIR/$APP/index.html"
    echo "  - $APP deployed"
done

echo ""
echo "Done! UI apps deployed to: $UI_APPS_DIR"
echo ""
echo "Available apps:"
ls -la "$UI_APPS_DIR"

#!/bin/bash
# Setup script for Python virtual environment
# Usage: ./setup.sh

set -e  # Exit on error

# Always run from this script's directory so the relative editable install
# (`-e ../..` in requirements.txt) resolves regardless of caller CWD.
cd "$(dirname "$0")"

echo "🚀 Setting up the ArqSim frontend backend environment..."

# Check if a supported Python is available.
if ! command -v python3 &> /dev/null; then
    echo "❌ Error: python3 is not installed. Please install Python 3.10 or higher."
    exit 1
fi

if ! python3 -c 'import sys; raise SystemExit(sys.version_info < (3, 10))'; then
    echo "❌ Error: ArqSim requires Python 3.10 or higher."
    exit 1
fi

# Get Python version
PYTHON_VERSION=$(python3 --version | cut -d' ' -f2 | cut -d'.' -f1,2)
echo "📦 Python version: $(python3 --version)"

# Create virtual environment
if [ -d "venv" ]; then
    echo "⚠️  Virtual environment 'venv' already exists. Removing old one..."
    rm -rf venv
fi

echo "📦 Creating virtual environment..."
python3 -m venv venv

# Activate virtual environment
echo "🔌 Activating virtual environment..."
source venv/bin/activate

# Upgrade pip
echo "⬆️  Upgrading pip..."
pip install --upgrade pip

# Install dependencies
echo "📥 Installing dependencies from requirements.txt..."
pip install -r requirements.txt

echo ""
echo "✅ Setup complete!"
echo ""
echo "To activate the virtual environment in the future, run:"
echo "  source venv/bin/activate"
echo ""
echo "To deactivate, run:"
echo "  deactivate"

#!/bin/bash
# Script to verify a team member's development environment setup
# This can be run by developers to ensure they have everything configured

set -e

echo "🔍 Verifying Development Environment Setup"
echo "=========================================="
echo ""

# Color codes
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

ERRORS=0
WARNINGS=0

# Function to check and report
check_requirement() {
    if [ $? -eq 0 ]; then
        echo -e "${GREEN}✓${NC} $1"
    else
        echo -e "${RED}✗${NC} $1"
        ((ERRORS++))
    fi
}

check_warning() {
    if [ $? -eq 0 ]; then
        echo -e "${GREEN}✓${NC} $1"
    else
        echo -e "${YELLOW}⚠${NC} $1"
        ((WARNINGS++))
    fi
}

echo "📋 Checking Required Tools..."
echo ""

# Check Python
python3 --version > /dev/null 2>&1
check_requirement "Python 3 installed"

# Check Python version
PYTHON_VERSION=$(python3 -c 'import sys; print(".".join(map(str, sys.version_info[:2])))')
if [[ $(echo "$PYTHON_VERSION >= 3.11" | bc -l) -eq 1 ]] 2>/dev/null || [[ "$PYTHON_VERSION" > "3.11" ]] || [[ "$PYTHON_VERSION" == "3.11" ]]; then
    echo -e "${GREEN}✓${NC} Python version $PYTHON_VERSION (>= 3.11 required)"
else
    echo -e "${RED}✗${NC} Python version $PYTHON_VERSION (>= 3.11 required)"
    ((ERRORS++))
fi

# Check Git
git --version > /dev/null 2>&1
check_requirement "Git installed"

# Check if in git repository
git rev-parse --git-dir > /dev/null 2>&1
check_requirement "In a Git repository"

echo ""
echo "🔧 Checking Pre-commit Setup..."
echo ""

# Check if pre-commit is installed
if command -v pre-commit &> /dev/null; then
    echo -e "${GREEN}✓${NC} pre-commit is installed ($(pre-commit --version))"
else
    echo -e "${RED}✗${NC} pre-commit is NOT installed"
    echo "  → Run: pip install pre-commit"
    ((ERRORS++))
fi

# Check if pre-commit hooks are installed
if [ -f .git/hooks/pre-commit ]; then
    echo -e "${GREEN}✓${NC} Pre-commit hooks are installed"
else
    echo -e "${RED}✗${NC} Pre-commit hooks are NOT installed"
    echo "  → Run: pre-commit install"
    ((ERRORS++))
fi

# Validate pre-commit config
if command -v pre-commit &> /dev/null; then
    if pre-commit validate-config > /dev/null 2>&1; then
        echo -e "${GREEN}✓${NC} Pre-commit configuration is valid"
    else
        echo -e "${RED}✗${NC} Pre-commit configuration is invalid"
        ((ERRORS++))
    fi
fi

echo ""
echo "📦 Checking Python Packages..."
echo ""

# Check for key packages
check_package() {
    if python3 -c "import $1" 2>/dev/null; then
        echo -e "${GREEN}✓${NC} $2 installed"
    else
        echo -e "${YELLOW}⚠${NC} $2 NOT installed"
        echo "  → Run: pip install $1"
        ((WARNINGS++))
    fi
}

check_package "black" "black (formatter)"
check_package "isort" "isort (import sorter)"
check_package "ruff" "ruff (linter)"
check_package "bandit" "bandit (security)"
check_package "mypy" "mypy (type checker)"
check_package "django" "Django"

echo ""
echo "🔒 Checking Security Tools..."
echo ""

# Check for detect-secrets
if command -v detect-secrets &> /dev/null; then
    echo -e "${GREEN}✓${NC} detect-secrets is installed"
else
    echo -e "${YELLOW}⚠${NC} detect-secrets is NOT installed"
    echo "  → Run: pip install detect-secrets"
    ((WARNINGS++))
fi

# Check for secrets baseline
if [ -f .secrets.baseline ]; then
    echo -e "${GREEN}✓${NC} .secrets.baseline file exists"
else
    echo -e "${YELLOW}⚠${NC} .secrets.baseline file missing"
    echo "  → Run: detect-secrets scan > .secrets.baseline"
    ((WARNINGS++))
fi

echo ""
echo "🐍 Checking Django Setup..."
echo ""

# Check if manage.py exists
if [ -f manage.py ]; then
    echo -e "${GREEN}✓${NC} manage.py found"

    # Try to run Django checks (may fail if DB not set up)
    if python3 manage.py check --deploy > /dev/null 2>&1; then
        echo -e "${GREEN}✓${NC} Django system check passed"
    else
        echo -e "${YELLOW}⚠${NC} Django system check has warnings (may be expected)"
    fi
else
    echo -e "${YELLOW}⚠${NC} manage.py not found (not in project root?)"
    ((WARNINGS++))
fi

echo ""
echo "📁 Checking Project Files..."
echo ""

# Check for important config files
check_file() {
    if [ -f "$1" ]; then
        echo -e "${GREEN}✓${NC} $1 exists"
    else
        echo -e "${RED}✗${NC} $1 missing"
        ((ERRORS++))
    fi
}

check_file ".pre-commit-config.yaml"
check_file "pyproject.toml"
check_file "requirements.txt"

echo ""
echo "=========================================="
echo ""

# Summary
if [ $ERRORS -eq 0 ] && [ $WARNINGS -eq 0 ]; then
    echo -e "${GREEN}✅ All checks passed! Your environment is properly set up.${NC}"
    echo ""
    echo "🚀 You're ready to start developing!"
    echo ""
    echo "Next steps:"
    echo "  1. Make your changes"
    echo "  2. Run: git commit (hooks will run automatically)"
    echo "  3. Or run: make pre-commit-all (to test all files)"
    exit 0
elif [ $ERRORS -eq 0 ]; then
    echo -e "${YELLOW}⚠ Setup mostly complete with $WARNINGS warning(s)${NC}"
    echo ""
    echo "You can start developing, but consider fixing the warnings above."
    exit 0
else
    echo -e "${RED}❌ Setup incomplete: $ERRORS error(s), $WARNINGS warning(s)${NC}"
    echo ""
    echo "🔧 To fix the issues, run:"
    echo "  ./scripts/setup-pre-commit.sh"
    echo ""
    echo "Or manually:"
    echo "  1. pip install pre-commit"
    echo "  2. pre-commit install"
    echo "  3. pip install -r requirements.txt"
    exit 1
fi

#!/bin/bash

# Generate mypy baseline file
# This captures all current type errors as the starting point

set -e

BASELINE_FILE="mypy-baseline.txt"
BACKUP_FILE="mypy-baseline.txt.backup"
RAW_OUTPUT=$(mktemp)

# Must stay in sync with the identical filter in bin/check_mypy.sh — the
# baseline and the run it is compared against have to be shaped the same way.
# See that script for why the raw output cannot be used directly.
mypy_diagnostics_only() {
    grep -E '^[^[:space:]]+\.pyi?:' "$1" || true
}

echo "🔄 Generating mypy baseline..."

# Backup existing baseline if it exists
if [ -f "$BASELINE_FILE" ]; then
    echo "📦 Backing up existing baseline to $BACKUP_FILE"
    cp "$BASELINE_FILE" "$BACKUP_FILE"
    BASELINE_COUNT=$(wc -l < "$BACKUP_FILE")
    echo "   Previous baseline had $BASELINE_COUNT errors"
fi

# Generate new baseline
echo "🏃 Running mypy..."
if mypy . > "$RAW_OUTPUT" 2>&1; then
    MYPY_CLEAN=1
else
    MYPY_CLEAN=0
fi
mypy_diagnostics_only "$RAW_OUTPUT" > "$BASELINE_FILE"
rm -f "$RAW_OUTPUT"

if [ "$MYPY_CLEAN" -eq 1 ]; then
    echo "✅ Perfect! No type errors found!"
    echo "   Baseline is empty (all code is properly typed!)"
    echo "" > "$BASELINE_FILE"
else
    NEW_COUNT=$(wc -l < "$BASELINE_FILE")
    echo "📝 Generated baseline with $NEW_COUNT errors"

    if [ -f "$BACKUP_FILE" ]; then
        DIFF=$((NEW_COUNT - BASELINE_COUNT))
        if [ $DIFF -lt 0 ]; then
            echo "🎉 Improvement! You fixed $((-DIFF)) errors!"
        elif [ $DIFF -gt 0 ]; then
            echo "⚠️  Warning: Baseline increased by $DIFF errors"
        else
            echo "📊 Error count unchanged"
        fi
    fi
fi

echo ""
echo "✨ Baseline updated successfully!"
echo "   File: $BASELINE_FILE"
echo ""
echo "ℹ️  Commit this file to version control:"
echo "   git add $BASELINE_FILE"
echo "   git commit -m 'Update mypy baseline'"

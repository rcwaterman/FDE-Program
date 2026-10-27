#!/bin/bash
# Quick launcher for the review UI

set -e

PROJECT_ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
REVIEW_DIR="$PROJECT_ROOT/src/feature_extraction/review"

echo "📋 Starting Payroll Extraction Review UI... (extra args go to app.py, e.g. --source run --run-id <id>)"
echo "   Project: $PROJECT_ROOT"
echo "   App: $REVIEW_DIR/app.py"
echo ""
echo "Opening http://localhost:8501 in your browser..."
echo "(Press Ctrl+C to stop the server)"
echo ""

cd "$PROJECT_ROOT"
streamlit run "$REVIEW_DIR/app.py" -- "$@"

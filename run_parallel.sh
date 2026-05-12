#!/usr/bin/env bash
# Launch 10 parallel workers in background for ASAP omnizart conversion.
# A tmux session "asap-convert" is created with a live progress monitor.

set -e

DATASET="${1:-/home/sy/EPR/data/asap-dataset}"
OUTPUT="${2:-/home/sy/EPR/data/asap-dataset-omnizart}"
WORKERS="${3:-10}"
PYTHON="conda run -n omnizart38 python"
SCRIPT="/home/sy/EPR/wave-roll/batch_worker.py"
LOGDIR="/home/sy/EPR/wave-roll/worker-logs"
mkdir -p "$LOGDIR"

SESSION="asap-convert"
tmux kill-session -t "$SESSION" 2>/dev/null || true

# Launch workers in background
for (( i=0; i<WORKERS; i++ )); do
    $PYTHON "$SCRIPT" "$DATASET" "$OUTPUT" "$i" "$WORKERS" \
        > "$LOGDIR/worker-$i.log" 2>&1 &
    echo "[Launched] Worker $i (PID $!)"
done

echo ""
echo "All $WORKERS workers launched in background"

# Create tmux session for monitoring
tmux new-session -d -s "$SESSION" -n "monitor"

# Monitoring pane: count progress and tail logs
tmux send-keys -t "$SESSION:0.0" \
    'echo "=== ASAP Omnizart Conversion Monitor ===" && echo "Workers: '"$WORKERS"'" && echo "Dataset: '"$DATASET"'" && echo "Output: '"$OUTPUT"'" && echo "" && while true; do clear; echo "=== ASAP Omnizart Conversion Monitor ==="; echo "Workers: '"$WORKERS"' | Dataset: '"$DATASET"'"; echo "Output: '"$OUTPUT"'"; echo ""; TOTAL=$(find '"$OUTPUT"' -name "*.mid.tsv" 2>/dev/null | wc -l); echo "Completed files: $TOTAL / 1306"; echo ""; echo "Worker status:"; for f in '"$LOGDIR"'/worker-*.log; do w=$(basename "$f" .log); tail -1 "$f" 2>/dev/null | head -c 120; echo ""; done; echo ""; echo "(refreshing every 10s, Ctrl+C to exit monitor)"; sleep 10; done' Enter

echo ""
echo "Monitoring in tmux session '$SESSION'"
echo "Attach with: tmux attach -t $SESSION"

#!/bin/bash
# Run the curl pipeline on the L40 over held-open ssh, restart-on-drop (resumable).
LOG=~/stock_journal/logs/pipeline_l40.log
: > "$LOG"
SSHOPT='ssh -T -o ControlPath=none -o StrictHostKeyChecking=no -o ServerAliveInterval=15 -o ServerAliveCountMax=400 -o ConnectTimeout=45 -o ConnectionAttempts=8'
for n in $(seq 1 60); do
  echo "===== L40 RUN $n $(date) =====" >> "$LOG"
  $SSHOPT embed-l40 'bash ~/instance_pipeline_curl.sh > ~/pipeline_out.log 2>&1; echo REMOTE_RC=$?' >> "$LOG" 2>&1
  echo "===== run $n exited rc=$? $(date) =====" >> "$LOG"
  grep -qa "ALL_MARKETS_DONE" "$LOG" && { echo "L40_ALL_DONE $(date)" >> "$LOG"; exit 0; }
  sleep 8
done
echo "L40_GAVE_UP $(date)" >> "$LOG"; exit 1

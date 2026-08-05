#!/bin/bash
# Resume-safe per-account statement load: fresh python process per account so
# memory is released between accounts (prior single-process run was OOM-killed).
set -e
cd /Users/gauravsinghal/Documents/Work/G-master/drivelah/finance-api
set -a; source .env; set +a
PY=/Users/gauravsinghal/Documents/Work/G-master/drivelah/finance-api/venv/bin/python
SCRIPT=/Users/gauravsinghal/Documents/Work/G-master/drivelah/finance-api/.claude/worktrees/agent-ac6e818dc9b692c05/scripts/vr1c_load_statements.py
OUT=/Users/gauravsinghal/Documents/Work/G-master/drivelah/finance-api/scripts
for ACCT in OCBC_1001 OCBC_3001 CBA DBS; do
  echo "###### START $ACCT $(date) ######"
  $PY "$SCRIPT" "$ACCT" > "$OUT/vr1c_${ACCT}.out" 2> "$OUT/vr1c_${ACCT}.err" \
    && echo "###### DONE $ACCT ######" \
    || echo "###### FAILED $ACCT (exit $?) ######"
  tail -3 "$OUT/vr1c_${ACCT}.out"
done
echo "###### ALL ACCOUNTS COMPLETE $(date) ######"

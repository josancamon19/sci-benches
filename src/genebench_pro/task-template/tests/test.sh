#!/usr/bin/env bash
set -uo pipefail

mkdir -p /logs/verifier
python /tests/evaluate.py 2>&1 | tee /logs/verifier/evaluation.log

if [[ ! -s /logs/verifier/reward.txt ]]; then
    printf '0\n' > /logs/verifier/reward.txt
fi

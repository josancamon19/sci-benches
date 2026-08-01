#!/usr/bin/env bash
set -uo pipefail

mkdir -p /logs/verifier /logs/artifacts

if [[ -f /app/final_answer.txt ]]; then
    cp /app/final_answer.txt /logs/artifacts/final_answer.txt
fi

if ! rewardkit /tests 2>&1 | tee /logs/verifier/judge.log; then
    printf '{"reward": 0.0}\n' > /logs/verifier/reward.json
fi

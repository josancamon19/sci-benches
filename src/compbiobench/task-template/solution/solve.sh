#!/usr/bin/env bash
set -euo pipefail

mkdir -p /app /logs/artifacts
printf '%s\n' 'Official CompBioBench answers are not publicly shared.' > /app/final_answer.txt
cp /app/final_answer.txt /logs/artifacts/final_answer.txt

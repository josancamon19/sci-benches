#!/usr/bin/env bash
set -euo pipefail

mkdir -p /app /logs/artifacts
cp /solution/oracle_answer.txt /app/final_answer.txt
cp /solution/oracle_answer.txt /logs/artifacts/final_answer.txt

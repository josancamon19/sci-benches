#!/usr/bin/env bash
set -euo pipefail

mkdir -p /app /logs/artifacts
cp /solution/oracle_answer.json /app/result.json
cp /solution/oracle_answer.json /logs/artifacts/result.json

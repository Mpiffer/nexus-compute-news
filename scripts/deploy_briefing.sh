#!/bin/bash
set -e
cd "C:/Users/MAARA1/squads/nexus-compute-news"
python scripts/briefing.py 2>&1
git add -A
git diff --cached --quiet && echo "Nenhuma mudança para commitar." && exit 0
git commit -m "chore: auto-update briefing $(date '+%Y-%m-%d %H:%M')"
git push origin main 2>&1
echo "Deploy concluído: https://mpiffer.github.io/nexus-compute-news/"

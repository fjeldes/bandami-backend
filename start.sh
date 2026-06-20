#!/bin/sh
set -e
python -c "from app.db.migrate import run_migrations; import os; run_migrations(os.environ['DATABASE_URL'])"
exec uvicorn app.main:app --host 0.0.0.0 --port 8080 --proxy-headers --timeout-keep-alive 65 --no-access-log

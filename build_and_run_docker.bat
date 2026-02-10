@echo off
echo [JobAgent] Building Docker image...
docker build -t myjobagent .

echo [JobAgent] Starting Docker container...
echo Note: This assumes ActivityWatch is installed in default location: %LocalAppData%\ActivityWatch\activitywatch.db

:: Stop existing container if any
docker stop myjobagent 2>nul
docker rm myjobagent 2>nul

:: Run container
:: -v Mounts ActivityWatch DB (Read-Only)
:: -v Mounts .env file
:: -v Mounts output directory for wordclouds and logs
docker run -d ^
  --name myjobagent ^
  --restart unless-stopped ^
  -v "%LocalAppData%\activitywatch\activitywatch\aw-server\peewee-sqlite.v2.db:/data/activitywatch.db:ro" ^
  -v "%cd%\.env:/app/.env" ^
  -v "%cd%\core\output:/app/core/output" ^
  -v "%cd%\core\config.json:/app/core/config.json" ^
  myjobagent

echo [JobAgent] Container started. Check logs with: docker logs -f myjobagent

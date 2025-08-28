#!/bin/bash
set -e

echo "[startup] Installing ODBC dependencies for Azure SQL..."
apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
  curl gnupg unixodbc unixodbc-dev libgssapi-krb5-2 apt-transport-https ca-certificates

echo "[startup] Adding Microsoft ODBC repository..."
curl -fsSL https://packages.microsoft.com/keys/microsoft.asc | apt-key add -
curl -fsSL https://packages.microsoft.com/config/ubuntu/20.04/prod.list > /etc/apt/sources.list.d/mssql-release.list

apt-get update
ACCEPT_EULA=Y apt-get install -y --no-install-recommends msodbcsql17

echo "[startup] Installing extra libraries for OpenCV..."
DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends libgl1 libglib2.0-0

echo "[startup] Ensuring Python packages are installed..."
python -m pip install --upgrade pip
python -m pip install --no-cache-dir -r /home/site/wwwroot/requirements.txt

echo "[startup] Starting Gunicorn..."
exec gunicorn talha:app --bind=0.0.0.0:$PORT --workers=4 --timeout 600

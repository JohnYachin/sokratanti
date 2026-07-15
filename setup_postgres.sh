#!/bin/bash
# Setup PostgreSQL for Sokratanti bot

echo "[1/3] Создаём пользователя и базу..."
sudo -u postgres psql -c "CREATE USER sokratanti WITH PASSWORD 'Sokr4nt1DB2026.' CREATEDB;" 2>/dev/null || echo "User exists"
sudo -u postgres psql -c "CREATE DATABASE sokratantidb OWNER sokratanti;" 2>/dev/null || echo "DB exists"
sudo -u postgres psql -c "GRANT ALL PRIVILEGES ON DATABASE sokratantidb TO sokratanti;" 2>/dev/null

echo "[2/3] Устанавливаем psycopg2..."
/opt/sokratanti/venv/bin/pip install psycopg2-binary -q && echo "psycopg2 OK"

echo "[3/3] Добавляем DATABASE_URL в .env..."
# Remove old DATABASE_URL if exists
sed -i '/^DATABASE_URL=/d' /opt/sokratanti/.env
echo "DATABASE_URL=postgresql://sokratanti:Sokr4nt1DB2026.@localhost:5432/sokratantidb" >> /opt/sokratanti/.env

echo "=== PostgreSQL READY ==="
# Test connection
sudo -u postgres psql -c "\l" | grep sokratanti

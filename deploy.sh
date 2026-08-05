#!/bin/bash
# ============================================================
# CAIOS — Deploy Script for Hostinger VPS (Ubuntu)
# Run as root on VPS: bash deploy.sh
# ============================================================
set -e

echo "🚀 CAIOS Deploy Starting..."

# ── 1. System dependencies ─────────────────────────────────
echo ""
echo "📦 [1/6] Installing system packages..."
apt-get update -qq
apt-get install -y python3 python3-pip python3-venv git curl wget -qq
echo "✅ System packages installed"

# ── 2. Clone / Update repo ─────────────────────────────────
echo ""
echo "📁 [2/6] Setting up project..."
if [ -d "/opt/caios" ]; then
    echo "Updating existing installation..."
    cd /opt/caios
    git pull origin main
else
    echo "Fresh clone..."
    git clone https://github.com/JohnYachin/sokratanti.git /opt/caios
    cd /opt/caios
fi
echo "✅ Repository ready at /opt/caios"

# ── 3. Python virtualenv + deps ─────────────────────────────
echo ""
echo "🐍 [3/6] Setting up Python environment..."
cd /opt/caios/backend
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip -q
pip install -r requirements.txt -q 2>/dev/null || \
    pip install httpx supabase openai aiohttp pyngrok fastapi uvicorn \
        prometheus-fastapi-instrumentator python-dotenv pyproject-toml -q
echo "✅ Python environment ready"

# ── 4. .env file ────────────────────────────────────────────
echo ""
echo "🔑 [4/6] Creating .env file..."
cat > /opt/caios/backend/.env << 'ENVEOF'
APP_NAME=CAIOS
APP_ENV=production
APP_VERSION=1.0.0
DEBUG=false
SECRET_KEY=caios-prod-secret-2026

SUPABASE_URL=https://zrvsuwdlhnnfvqxxohex.supabase.co
SUPABASE_ANON_KEY=eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InpydnN1d2RsaG5uZnZxeHhvaGV4Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODU1MTE0MDAsImV4cCI6MjEwMTA4NzQwMH0.dXsn9QAFArIm6gP447NrzsskLx3mV6LvSfO9WPyjLeo
SUPABASE_SERVICE_KEY=eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InpydnN1d2RsaG5uZnZxeHhvaGV4Iiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc4NTUxMTQwMCwiZXhwIjoyMTAxMDg3NDAwfQ.19YNUSRWeJknVytkfQjvnzsjT0LmvqkWUX0eRRDSGJY

DATABASE_URL=postgresql+asyncpg://postgres.zrvsuwdlhnnfvqxxohex:azsx2004ZOL%40%25%23@aws-0-ap-northeast-1.pooler.supabase.com:6543/postgres

OPENAI_API_KEY=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
OPENAI_MODEL=gpt-4o

TELEGRAM_BOT_TOKEN=8937697751:AAFiTO-AnEowrT-XuSVlKZNs8d6BOVGoPXc
TELEGRAM_USER_ID=634964003

PERPLEXITY_API_KEY=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

COUNCIL_MIN_AGENTS=15
COUNCIL_TIMEOUT_SECONDS=60
COUNCIL_CONFIDENCE_THRESHOLD=0.65
LOG_LEVEL=INFO
ENVEOF
echo "✅ .env file created"

# ── 5. Systemd services ─────────────────────────────────────
echo ""
echo "⚙️  [5/6] Creating systemd services..."

# caios-bot.service — Telegram bot (24/7)
cat > /etc/systemd/system/caios-bot.service << 'EOF'
[Unit]
Description=CAIOS Telegram Bot (AI Council)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/caios/backend
ExecStart=/opt/caios/backend/venv/bin/python app/telegram/bot.py
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal
SyslogIdentifier=caios-bot

# Environment
EnvironmentFile=/opt/caios/backend/.env

[Install]
WantedBy=multi-user.target
EOF

# caios-api.service — FastAPI REST
cat > /etc/systemd/system/caios-api.service << 'EOF'
[Unit]
Description=CAIOS FastAPI Backend
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/caios/backend
ExecStart=/opt/caios/backend/venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 2
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal
SyslogIdentifier=caios-api
EnvironmentFile=/opt/caios/backend/.env

[Install]
WantedBy=multi-user.target
EOF

# ── 6. Enable & start ───────────────────────────────────────
echo ""
echo "🔄 [6/6] Starting services..."
systemctl daemon-reload

systemctl enable caios-bot
systemctl enable caios-api

systemctl restart caios-bot
systemctl restart caios-api

sleep 3

echo ""
echo "════════════════════════════════════════════════"
echo "✅ CAIOS DEPLOYED SUCCESSFULLY!"
echo "════════════════════════════════════════════════"
echo ""
systemctl status caios-bot --no-pager -l | head -15
echo ""
systemctl status caios-api --no-pager -l | head -10
echo ""
echo "📋 Commands:"
echo "  journalctl -u caios-bot -f     # Bot logs"
echo "  journalctl -u caios-api -f     # API logs"
echo "  systemctl restart caios-bot    # Restart bot"
echo ""
echo "🌐 API: http://$(curl -s ifconfig.me 2>/dev/null):8000"
echo "📚 Docs: http://$(curl -s ifconfig.me 2>/dev/null):8000/docs"

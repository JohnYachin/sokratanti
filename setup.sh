#!/bin/bash
# ============================================================
# Sokratanti — Автоматическая установка на VPS (Ubuntu/Debian)
# Запуск: bash setup.sh
# ============================================================

set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

echo -e "${CYAN}"
echo "  ╔══════════════════════════════════════╗"
echo "  ║   Sokratanti — установка на сервер   ║"
echo "  ╚══════════════════════════════════════╝"
echo -e "${NC}"

# ── 1. Системные пакеты ─────────────────────────────────────
echo -e "${YELLOW}[1/6] Обновляем систему...${NC}"
apt-get update -qq
apt-get install -y -qq python3 python3-pip python3-venv git curl

# ── 2. Клонируем репозиторий ────────────────────────────────
echo -e "${YELLOW}[2/6] Скачиваем проект с GitHub...${NC}"
cd /opt
if [ -d "sokratanti" ]; then
    echo "  Папка уже есть, обновляем..."
    cd sokratanti
    git pull origin main
else
    git clone https://github.com/JohnYachin/sokratanti.git
    cd sokratanti
fi

# ── 3. Python окружение ─────────────────────────────────────
echo -e "${YELLOW}[3/6] Создаём виртуальное окружение...${NC}"
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip -q
pip install -r requirements.txt -q
echo -e "${GREEN}  Зависимости установлены.${NC}"

# ── 4. .env файл ────────────────────────────────────────────
echo -e "${YELLOW}[4/6] Настраиваем .env...${NC}"
if [ ! -f ".env" ]; then
    cp .env.example .env
    echo -e "${RED}  ВАЖНО: заполни /opt/sokratanti/.env своими ключами!${NC}"
else
    echo -e "${GREEN}  .env уже существует.${NC}"
fi

# ── 5. systemd сервис ───────────────────────────────────────
echo -e "${YELLOW}[5/6] Регистрируем systemd сервис...${NC}"
cat > /etc/systemd/system/sokratanti.service << 'EOF'
[Unit]
Description=Sokratanti Crypto Telegram Bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=/opt/sokratanti
ExecStart=/opt/sokratanti/venv/bin/python run.py
Restart=always
RestartSec=15
StandardOutput=journal
StandardError=journal
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable sokratanti
echo -e "${GREEN}  Сервис зарегистрирован.${NC}"

# ── 6. Итог ─────────────────────────────────────────────────
echo ""
echo -e "${CYAN}╔══════════════════════════════════════════════╗${NC}"
echo -e "${CYAN}║           Установка завершена! ✅            ║${NC}"
echo -e "${CYAN}╚══════════════════════════════════════════════╝${NC}"
echo ""
echo -e "Следующие шаги:"
echo -e "  ${YELLOW}1.${NC} Заполни ключи:  ${GREEN}nano /opt/sokratanti/.env${NC}"
echo -e "  ${YELLOW}2.${NC} Запусти бота:   ${GREEN}systemctl start sokratanti${NC}"
echo -e "  ${YELLOW}3.${NC} Статус:         ${GREEN}systemctl status sokratanti${NC}"
echo -e "  ${YELLOW}4.${NC} Логи:           ${GREEN}journalctl -u sokratanti -f${NC}"
echo ""

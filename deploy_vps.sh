#!/bin/bash
set -e

echo "[1/5] Клонируем репозиторий..."
cd /opt
rm -rf sokratanti
git clone https://github.com/JohnYachin/sokratanti.git
cd sokratanti

echo "[2/5] Создаём venv..."
python3 -m venv venv
source venv/bin/activate

echo "[3/5] Устанавливаем зависимости..."
pip install -r requirements.txt -q

echo "[4/5] Создаём systemd сервис..."
cat > /etc/systemd/system/sokratanti.service << 'SVCEOF'
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
SVCEOF

systemctl daemon-reload
systemctl enable sokratanti

echo "[5/5] Установка завершена!"
echo "Файлы в /opt/sokratanti:"
ls /opt/sokratanti/

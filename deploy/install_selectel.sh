#!/bin/bash
# ====================================================================
# Автоматический скрипт установки Самбери Мониторинг на сервер Selectel
# ====================================================================

set -e

echo "=========================================================="
echo "🚀 Начало установки: Самбери Мониторинг Ценников (Selectel)"
echo "=========================================================="

# 1. Обновление системы
echo "[1/6] Обновление системных пакетов..."
apt update -y && apt upgrade -y
apt install -y python3 python3-pip python3-venv git curl ufw

# 2. Создание директории проекта
echo "[2/6] Создание директории /opt/samberi_monitor..."
mkdir -p /opt/samberi_monitor
cd /opt/samberi_monitor

# 3. Настройка виртуального окружения Python
echo "[3/6] Настройка виртуального окружения Python..."
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip

# 4. Установка библиотек
echo "[4/6] Установка зависимостей..."
pip install streamlit pandas openpyxl rapidfuzz plotly google-genai openai pillow requests python-dotenv python-telegram-bot

# 5. Настройка службы Systemd для работы 24/7
echo "[5/6] Создание службы автозапуска systemd..."
cat << 'EOF' > /etc/systemd/system/samberi-monitor.service
[Unit]
Description=Samberi Price Tag Monitoring Service (24/7)
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/samberi_monitor
ExecStart=/opt/samberi_monitor/venv/bin/streamlit run app.py --server.port 8501 --server.address 0.0.0.0 --server.headless true
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

# 6. Открытие порта в фаерволе
echo "[6/6] Настройка сетевого экрана (порт 8501)..."
ufw allow 8501/tcp || true
ufw allow 22/tcp || true

systemctl daemon-reload
systemctl enable samberi-monitor

echo "=========================================================="
echo "✅ Установка завершена!"
echo "Скопируйте файлы приложения в папку /opt/samberi_monitor"
echo "Укажите ваш API-ключ в /opt/samberi_monitor/.env"
echo "Затем запустите службу: systemctl start samberi-monitor"
echo "=========================================================="

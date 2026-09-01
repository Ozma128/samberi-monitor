# Инструкция: Как развернуть систему мониторинга ценников 24/7

Данное руководство объясняет, как запустить веб-приложение на удаленном сервере (VPS), чтобы сервис работал автономно круглые сутки, даже когда ваш рабочий компьютер выключен.

---

## Вариант 1. Российский VPS (TimeWeb Cloud / Beget / Selectel)

Стоимость: **~150–250 рублей в месяц**.

### Шаг 1. Аренда сервера
1. Зарегистрируйтесь на сайте [TimeWeb Cloud](https://timeweb.cloud/) или [Beget](https://beget.com/).
2. Создайте облачный сервер:
   - ОС: **Ubuntu 22.04 LTS** (или 24.04).
   - Конфигурация: минимальная (1 CPU, 1–2 GB RAM, 20 GB SSD).

### Шаг 2. Подключение и установка
Подключитесь к серверу через терминал (PowerShell / PuTTY / SSH):
```bash
ssh root@IP_ВАШЕГО_СЕРВЕРА
```

Установите Python, Git и клонируйте проект:
```bash
# Обновление пакетов
apt update && apt upgrade -y
apt install -y python3-pip python3-venv git

# Клонирование репозитория
git clone <URL_РЕПОЗИТОРИЯ> /opt/samberi_monitor
cd /opt/samberi_monitor

# Создание виртуального окружения
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### Шаг 3. Настройка ключей (.env)
Создайте файл `.env`:
```bash
nano .env
```
Вставьте ваш ключ:
```env
GEMINI_API_KEY=AIzaSy...
```
Сохраните (`Ctrl+O`, затем `Enter`, выход `Ctrl+X`).

### Шаг 4. Настройка автозапуска через Systemd (24/7)
Создайте системную службу, чтобы приложение автоматически перезапускалось при любых сбоях или перезагрузке сервера:

```bash
cat << 'EOF' > /etc/systemd/system/samberi-monitor.service
[Unit]
Description=Samberi Price Tag Monitoring Service
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
```

Запустите службу:
```bash
systemctl daemon-reload
systemctl enable samberi-monitor
systemctl start samberi-monitor
```

Проверить статус:
```bash
systemctl status samberi-monitor
```

### Шаг 5. Открытие в браузере
Откройте в браузере на телефоне или компьютере:
`http://IP_ВАШЕГО_СЕРВЕРА:8501`

---

## Вариант 2. Запуск через Docker Compose (в 1 команду)

Если на сервере установлен Docker:
```bash
cd /opt/samberi_monitor/deploy
docker compose up -d --build
```

---

## Вариант 3. Запуск Telegram-бота для сбора фото из магазина

Чтобы сотрудники могли прямо из торгового зала скидывать пачки фото ценников в Telegram-чат:
1. Создайте бота в Telegram через [@BotFather](https://t.me/BotFather) и скопируйте токен.
2. В файле `.env` укажите:
   ```env
   TELEGRAM_BOT_TOKEN=123456789:ABCdef...
   GEMINI_API_KEY=AIzaSy...
   ```
3. Запустите фоновую службу бота:
   ```bash
   python deploy/telegram_bot.py
   ```
4. Отправляйте боту фотографии. Напишите `/finish` — бот пришлет готовый Excel с расчетом Price Index!

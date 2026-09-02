# Безопасное развёртывание «Самбери: Мониторинг ценников»

В production используйте один из двух вариантов:

1. Docker Compose на VPS за HTTPS reverse proxy с обязательной аутентификацией.
2. Приватное приложение в Streamlit Cloud с секретами в настройках платформы.

Не публикуйте Streamlit-порт напрямую в интернет и не храните API-ключи, SSH-ключи, пароли или файлы `.env` в репозитории, Docker-образе и ZIP-архивах.

## До первого запуска

- Отзовите и перевыпустите все ключи, которые когда-либо попадали в Git или архивы проекта.
- Удалите секреты из всей Git-истории и старых release-артефактов. Удаления файла только из последнего коммита недостаточно.
- Настройте вход на VPS по SSH-ключу, запретите парольный вход и прямой вход системного администратора.
- Используйте отдельный домен, например `monitor.example.ru`.
- Убедитесь, что отправка фотографий во внешний Vision API разрешена политикой обработки данных компании.

## Вариант A: Docker Compose на VPS

### 1. Подготовка сервера

Установите поддерживаемые Docker Engine и Docker Compose Plugin по официальной инструкции для вашей ОС. Создайте отдельного системного пользователя и каталог приложения:

```bash
sudo useradd --system --create-home --shell /usr/sbin/nologin samberi
sudo install -d -o samberi -g samberi -m 0750 /opt/samberi-monitor
sudo -u samberi git clone <URL_ПРИВАТНОГО_РЕПОЗИТОРИЯ> /opt/samberi-monitor
```

Приложение внутри контейнера запускается непривилегированным пользователем UID 10001. Доступ к Docker socket ему не нужен.

### 2. Секреты вне репозитория

Создайте защищённый environment-файл на хосте:

```bash
sudo install -o root -g root -m 0600 /dev/null /etc/samberi-monitor.env
sudoedit /etc/samberi-monitor.env
```

Содержимое:

```dotenv
GEMINI_API_KEY=вставьте_новый_ключ
APP_PASSWORD=вставьте_длинный_случайный_пароль
```

Не передавайте ключ или пароль в аргументах командной строки: аргументы могут попасть в историю shell и список процессов. Файл `/etc/samberi-monitor.env` не должен находиться внутри checkout проекта.

### 3. Управление контейнером через systemd

Создайте `/etc/systemd/system/samberi-monitor.service`:

```ini
[Unit]
Description=Samberi price monitoring container
Requires=docker.service
After=docker.service network-online.target
Wants=network-online.target

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=/opt/samberi-monitor/deploy
EnvironmentFile=/etc/samberi-monitor.env
ExecStartPre=/usr/bin/docker compose config --quiet
ExecStartPre=/usr/bin/docker compose build --pull
ExecStart=/usr/bin/docker compose up --detach --remove-orphans
ExecStop=/usr/bin/docker compose down
TimeoutStartSec=0

[Install]
WantedBy=multi-user.target
```

Запустите сервис:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now samberi-monitor.service
sudo systemctl status samberi-monitor.service
curl --fail http://127.0.0.1:8501/_stcore/health
```

Compose публикует порт только на `127.0.0.1`. Он доступен reverse proxy на том же сервере, но недоступен извне напрямую. Контейнер также использует read-only filesystem, временный `/tmp`, сброшенные Linux capabilities, `no-new-privileges` и ограничения ресурсов.

### 4. HTTPS и аутентификация через Nginx

Для внутреннего корпоративного сервиса предпочтительны VPN или identity-aware proxy с SSO/MFA. Ниже приведён минимальный вариант с HTTPS и Basic Auth.

Установите Nginx, Certbot и утилиту для создания хеша пароля:

```bash
sudo apt-get update
sudo apt-get install --yes nginx apache2-utils certbot python3-certbot-nginx
sudo htpasswd -c /etc/nginx/samberi.htpasswd operator
sudo chmod 0640 /etc/nginx/samberi.htpasswd
sudo chown root:www-data /etc/nginx/samberi.htpasswd
```

Команда `htpasswd` запросит пароль интерактивно; не помещайте его в команду или конфигурацию открытым текстом.

Создайте `/etc/nginx/conf.d/streamlit-websocket.conf`:

```nginx
map $http_upgrade $connection_upgrade {
    default upgrade;
    ''      close;
}
```

Сначала создайте webroot и временную конфигурацию, которая обслуживает только
ACME challenge. На этом этапе приложение и формы входа по HTTP не публикуются:

```bash
sudo install -d -o www-data -g www-data -m 0755 /var/www/certbot/.well-known/acme-challenge
```

Создайте `/etc/nginx/sites-available/samberi-monitor`:

```nginx
server {
    listen 80;
    listen [::]:80;
    server_name monitor.example.ru;

    location ^~ /.well-known/acme-challenge/ {
        root /var/www/certbot;
        default_type text/plain;
    }

    location / {
        return 404;
    }
}
```

Включите конфигурацию и выпустите сертификат через webroot:

```bash
sudo ln -s /etc/nginx/sites-available/samberi-monitor /etc/nginx/sites-enabled/samberi-monitor
sudo nginx -t
sudo systemctl reload nginx
sudo certbot certonly --webroot --webroot-path /var/www/certbot -d monitor.example.ru
```

Только после успешного выпуска сертификата замените site-конфигурацию на
финальную. HTTP теперь только обслуживает продление ACME и перенаправляет на
HTTPS; пароль никогда не передаётся по открытому соединению:

```nginx
server {
    listen 80;
    listen [::]:80;
    server_name monitor.example.ru;

    location ^~ /.well-known/acme-challenge/ {
        root /var/www/certbot;
        default_type text/plain;
    }

    location / {
        return 301 https://$host$request_uri;
    }
}

server {
    listen 443 ssl;
    listen [::]:443 ssl;
    server_name monitor.example.ru;

    ssl_certificate /etc/letsencrypt/live/monitor.example.ru/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/monitor.example.ru/privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;
    add_header Strict-Transport-Security "max-age=31536000" always;

    client_max_body_size 100m;
    auth_basic "Restricted";
    auth_basic_user_file /etc/nginx/samberi.htpasswd;

    location / {
        proxy_pass http://127.0.0.1:8501;
        proxy_http_version 1.1;
        proxy_buffering off;
        proxy_read_timeout 600s;

        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto https;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection $connection_upgrade;
    }
}
```

Проверьте и примените финальную конфигурацию, затем проверьте автообновление:

```bash
sudo nginx -t
sudo systemctl reload nginx
sudo certbot renew --dry-run
```

Открывайте во внешнем firewall/security group только SSH и TCP 80/443. Порт 8501 не открывайте. Перед включением UFW убедитесь, что текущий доступ по SSH-ключу работает:

```bash
sudo ufw allow OpenSSH
sudo ufw allow 'Nginx Full'
sudo ufw deny 8501/tcp
sudo ufw enable
```

После выпуска TLS проверьте доступ без передачи пароля в командной строке:

```bash
curl --fail --user operator https://monitor.example.ru/_stcore/health
```

`curl` запросит пароль интерактивно.

### 5. Обновление и откат

Обновляйте только до проверенного коммита или тега:

```bash
cd /opt/samberi-monitor
sudo -u samberi git pull --ff-only
sudo systemctl restart samberi-monitor.service
curl --fail http://127.0.0.1:8501/_stcore/health
```

Перед обновлением зафиксируйте текущий commit SHA. Для отката переключите checkout на ранее проверенный тег/commit и перезапустите сервис. Не разворачивайте сохранённые ZIP-копии: они быстро устаревают и могут содержать секреты.

Логи ограничены ротацией Docker Compose. Просмотр без вывода секретов:

```bash
sudo docker compose \
  --env-file /etc/samberi-monitor.env \
  --file /opt/samberi-monitor/deploy/docker-compose.yml \
  logs --tail=200 samberi-monitor
```

## Вариант B: Streamlit Cloud

1. Используйте очищенный приватный репозиторий без секретов и старых архивов.
2. Укажите entrypoint `app.py` и файл зависимостей `requirements.txt`.
3. В настройках Secrets приложения добавьте:

   ```toml
   GEMINI_API_KEY = "новый_ключ"
   APP_PASSWORD = "длинный_случайный_пароль"
   ```

4. Включите приватный доступ рабочей области/SSO. Если тариф или платформа не позволяют ограничить доступ, не публикуйте приложение с общим оплачиваемым API-ключом.
5. TLS для домена должен завершаться на инфраструктуре платформы или на одобренном компанией access proxy.

Не добавляйте `.streamlit/secrets.toml` в Git. После изменения доступа или подозрения на утечку немедленно ротируйте API-ключ.

## Эксплуатационный чек-лист

- `docker compose config --quiet` проходит с защищённым environment-файлом.
- Контейнер имеет статус `healthy` и работает не от UID 0.
- Снаружи доступны только 80/443; `8501` привязан к loopback.
- HTTPS перенаправление работает, а страница требует аутентификацию.
- Секретов нет в Git, Docker history, образе, логах и артефактах сборки.
- Настроены квоты API и оповещения о расходах у Vision-провайдера.
- Установлены лимиты загрузок и подтверждена политика хранения фотографий.
- Регулярно проверяются обновления базового образа и Python-зависимостей.

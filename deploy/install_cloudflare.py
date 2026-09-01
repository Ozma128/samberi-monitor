"""
Скрипт установки Cloudflare Tunnel на сервере.
Создает защищенный белый публичный HTTPS-адрес, доступный в РФ без VPN.
"""

import paramiko
import time
import re

ip = "77.110.121.103"
pwd = "4PCqwHq6A8pe"

print(f"[*] Подключение к серверу {ip}...")
ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())

try:
    ssh.connect(ip, username="root", password=pwd, timeout=15)
    print("[+] Успешное SSH подключение!")
except Exception as e:
    print(f"[!] Ошибка подключения: {e}")
    sys.exit(1)

commands = [
    "curl -L --output /tmp/cloudflared.deb https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64.deb",
    "dpkg -i /tmp/cloudflared.deb || apt-get install -f -y",
    """cat << 'EOF' > /etc/systemd/system/cloudflare-tunnel.service
[Unit]
Description=Cloudflare Tunnel for Samberi Monitor
After=network.target

[Service]
Type=simple
User=root
ExecStart=/usr/local/bin/cloudflared tunnel --url http://127.0.0.1:8501
Restart=always
RestartSec=5
StandardOutput=append:/var/log/cloudflared.log
StandardError=append:/var/log/cloudflared.log

[Install]
WantedBy=multi-user.target
EOF""",
    "systemctl daemon-reload",
    "systemctl enable cloudflare-tunnel",
    "systemctl restart cloudflare-tunnel"
]

for cmd in commands:
    print(f"[*] Выполнение: {cmd[:50]}...")
    stdin, stdout, stderr = ssh.exec_command(cmd)
    stdout.channel.recv_exit_status()

print("[*] Ожидание генерации публичного Cloudflare HTTPS-адреса (5 секунд)...")
time.sleep(5)

stdin, stdout, stderr = ssh.exec_command("cat /var/log/cloudflared.log | grep -o 'https://[-a-zA-Z0-9]*\\.trycloudflare\\.com' | tail -n 1")
url = stdout.read().decode('utf-8', errors='replace').strip()

ssh.close()

if url:
    print("\n" + "="*60)
    print(f"🎉 БЕЛЫЙ ПУБЛИЧНЫЙ АДРЕС БЕЗ VPN:")
    print(f"👉 {url}")
    print("="*60)
else:
    print("[!] Не удалось автоматически извлечь URL, проверьте логи cloudflared.")

"""
Скрипт установки и настройки Nginx на сервере Aeza.
Перенаправляет стандартный порт 80 на Streamlit (порт 8501),
чтобы сайт открывался просто по IP-адресу без указания портов.
"""

import paramiko

ip = "77.110.121.103"
pwd = "4PCqwHq6A8pe"

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect(ip, username="root", password=pwd, timeout=10)

nginx_conf = """server {
    listen 80 default_server;
    listen [::]:80 default_server;

    server_name _;

    client_max_body_size 100M;

    location / {
        proxy_pass http://127.0.0.1:8501;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 86400;
    }
}
"""

commands = [
    "apt update -y && apt install -y nginx",
    "cat << 'EOF' > /etc/nginx/sites-available/default\n" + nginx_conf + "\nEOF",
    "nginx -t",
    "systemctl restart nginx",
    "systemctl enable nginx",
    "ufw allow 80/tcp",
    "ufw allow 443/tcp",
    "ufw allow 8501/tcp",
    "systemctl restart samberi-monitor"
]

for cmd in commands:
    print(f"[*] Executing: {cmd[:40]}...")
    stdin, stdout, stderr = ssh.exec_command(cmd)
    stdout.channel.recv_exit_status()
    out = stdout.read().decode('utf-8', errors='replace')
    err = stderr.read().decode('utf-8', errors='replace')
    if "successful" in out or "syntax is ok" in err or not err:
        print("[+] OK")
    else:
        print(f"[!] {err[:200]}")

ssh.close()
print("\n[SUCCESS] Nginx настроен! Теперь сайт доступен прямо по http://77.110.121.103")

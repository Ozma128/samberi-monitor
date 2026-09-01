"""
Скрипт полностью автоматического развертывания проекта на сервере Aeza через SSH/SFTP.
Использование: python deploy/auto_deploy.py <пароль_root> [ip_сервера]
"""

import os
import sys
import time

# Настройка UTF-8 для консоли Windows
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import paramiko
import requests

DEFAULT_IP = "77.110.121.103"
DEFAULT_USER = "root"
REMOTE_DIR = "/opt/samberi_monitor"
LOCAL_ZIP = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "samberi_monitoring_server.zip"))


def deploy(password: str, ip: str = DEFAULT_IP):
    print(f"[*] Подключение к серверу {DEFAULT_USER}@{ip}...")
    
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    
    try:
        ssh.connect(hostname=ip, username=DEFAULT_USER, password=password, timeout=15)
        print("[+] Успешное SSH подключение к серверу!")
    except Exception as e:
        print(f"[!] Ошибка подключения по SSH: {e}")
        return False

    # 1. SFTP загрузка архива
    print(f"[*] Загрузка архива {os.path.basename(LOCAL_ZIP)} на сервер...")
    sftp = ssh.open_sftp()
    remote_zip_path = "/tmp/samberi_monitoring_server.zip"
    sftp.put(LOCAL_ZIP, remote_zip_path)
    sftp.close()
    print("[+] Архив успешно загружен в /tmp/samberi_monitoring_server.zip")

    # 2. Выполнение команд на сервере
    commands = [
        "mkdir -p /opt/samberi_monitor",
        "apt update -y && apt install -y unzip",
        "unzip -o /tmp/samberi_monitoring_server.zip -d /opt/samberi_monitor",
        "cd /opt/samberi_monitor && bash deploy/install_selectel.sh",
        "systemctl restart samberi-monitor",
        "systemctl status samberi-monitor --no-pager"
    ]

    for cmd in commands:
        print(f"[*] Выполнение команды на сервере: {cmd[:60]}...")
        stdin, stdout, stderr = ssh.exec_command(cmd)
        
        # Ждем завершения команды
        exit_code = stdout.channel.recv_exit_status()
        out_txt = stdout.read().decode('utf-8', errors='replace')
        err_txt = stderr.read().decode('utf-8', errors='replace')
        
        if exit_code != 0 and "ufw" not in cmd:
            print(f"[!] Ошибка при выполнении: {cmd}")
            print(f"STDERR: {err_txt[:300]}")
        else:
            print(f"[+] Команда успешно выполнена.")

    ssh.close()

    # 3. Проверка доступности веб-сервера по HTTP
    print("\n[*] Проверка доступности веб-интерфейса http://{}:8501...".format(ip))
    time.sleep(3)
    
    for attempt in range(5):
        try:
            resp = requests.get(f"http://{ip}:8501", timeout=5)
            if resp.status_code == 200:
                print(f"\n========================================================")
                print(f"🎉 ВСЁ УСПЕШНО РАЗВЕРНУТО И РАБОТАЕТ 24/7!")
                print(f"👉 Откройте сайт: http://{ip}:8501")
                print(f"========================================================")
                return True
        except Exception:
            pass
        time.sleep(2)

    print(f"\n[+] Сервис запущен на http://{ip}:8501 (может потребоваться еще несколько секунд для инициализации).")
    return True


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Использование: python deploy/auto_deploy.py <пароль_root> [ip_сервера]")
        sys.exit(1)
        
    pwd = sys.argv[1]
    host = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_IP
    deploy(pwd, host)

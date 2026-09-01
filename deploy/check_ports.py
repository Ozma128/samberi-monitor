import socket

ip = "77.110.121.103"
common_ports = [22, 2222, 22022, 22222, 80, 443, 8501, 2022, 5000, 8080]

print(f"Scanning common ports on {ip}...")
for port in common_ports:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(2.0)
    result = s.connect_ex((ip, port))
    s.close()
    status = "OPEN" if result == 0 else "CLOSED/TIMEOUT"
    print(f"Port {port}: {status}")

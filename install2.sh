#!/bin/bash
# Alpine to Debian 13 (Fixed Extraction & Non-Destructive Version)
# Auto extract network config -> Static IP install -> Prevent disconnect after reboot
# -------------------------------------------------------------

# --- 1. Install dependencies ---
if [ ! -f /bin/bash ]; then
    echo "Installing bash..."
    apk update >/dev/null 2>&1
    apk add bash iproute2 grep gawk ipcalc curl wget >/dev/null 2>&1
fi

# Ensure we are running in bash even when piped via sh
if [ -z "${BASH_VERSION:-}" ]; then
    if [ -t 0 ]; then
        exec /bin/bash "$0" "$@"
    else
        exec /bin/bash -s -- "$@"
    fi
fi

set -e

# --- 2. Root check ---
if [ "$(id -u)" != "0" ]; then
    echo "Error: Must run as root."
    exit 1
fi

# --- 3. Interactive config ---
clear
echo "=== Alpine to Debian 13 Auto Install Script ==="
echo "Script will extract current IP config for static IP install."
echo ""

if [ -z "$PORT" ]; then
    if [ -t 0 ] || [ -t 1 ]; then
        read -r -p "SSH Port [default 22]: " PORT </dev/tty || true
    fi
    PORT=${PORT:-22}
fi
if [ -z "$PASSWORD" ]; then
    if [ -t 0 ] || [ -t 1 ]; then
        read -r -p "Root Password (NO special chars) [default yiwan123]: " PASSWORD </dev/tty || true
    fi
    PASSWORD=${PASSWORD:-yiwan123}
fi

echo ""
echo "Config confirmed: Port $PORT / Password $PASSWORD"
echo "Starting auto install in 5 seconds..."
sleep 5

# --- 4. Main logic ---
echo "[1/3] Extracting and cleaning network config..."
# 获取主网卡并强制去除所有空格/换行
MAIN_IFace=$(ip route show default | awk '{print $5}' | head -n1 | tr -d '[:space:]')

# 获取 IP 并清理
MAIN_IP=$(ip -4 addr show "$MAIN_IFace" | awk '/inet / {print $2}' | cut -d/ -f1 | head -n1 | tr -d '[:space:]')

# 获取网关并清理
MAIN_GATE=$(ip route show default | awk '/default/ {print $3}' | head -n1 | tr -d '[:space:]')

# 获取 CIDR 掩码位数并清理
CIDR_NUM=$(ip -4 addr show "$MAIN_IFace" | awk '/inet / {print $2}' | cut -d/ -f2 | head -n1 | tr -d '[:space:]')

echo "Cleaned network config:"
echo "IP: [$MAIN_IP]"
echo "Gateway: [$MAIN_GATE]"
echo "CIDR: [/$CIDR_NUM]"

if [ -z "$MAIN_IP" ] || [ -z "$MAIN_GATE" ] || [ -z "$CIDR_NUM" ]; then
    echo "Error: Failed to extract valid network config!"
    exit 1
fi

echo "[2/3] Downloading install script..."
wget --no-check-certificate -qO InstallNET.sh 'https://raw.githubusercontent.com/leitbogioro/Tools/master/Linux_reinstall/InstallNET.sh' && chmod a+x InstallNET.sh

echo "[3/3] Starting Debian 13 installer (Static IP mode)..."
echo "System will reboot automatically. Please wait 10-15 minutes then login."

# Run InstallNET - Debian 13 模式，无危险 dd 操作
bash InstallNET.sh \
    -debian 13 \
    -port "${PORT}" \
    -pwd "${PASSWORD}" \
    -mirror "http://deb.debian.org/debian/" \
    --ip-addr "${MAIN_IP}" \
    --ip-gate "${MAIN_GATE}" \
    --ip-mask "${CIDR_NUM}" \
    -swap "512" \
    --cloudkernel "0" \
    --bbr \
    --motd

# 脚本执行完毕后 InstallNET 会自动接管并安全重启，无需手动干预

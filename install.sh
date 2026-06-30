#!/bin/bash

# 定义颜色
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m'

# GitHub 仓库 raw 地址
REPO_URL="https://raw.githubusercontent.com/10000ge10000/aliyun_monitor/main/src"
TARGET_DIR="/opt/scripts"
VENV_DIR="${TARGET_DIR}/venv"
CONFIG_FILE="${TARGET_DIR}/config.json"
BOT_SERVICE_NAME="aliyun-ecs-bot.service"
BOT_SERVICE_FILE="/etc/systemd/system/${BOT_SERVICE_NAME}"

# 全局变量，用于在函数间传递生成的 JSON 数据
CURRENT_USER_JSON=""
ADMIN_USERS_JSON="[]"
ENABLE_BOT="n"

echo -e "${BLUE}=============================================================${NC}"
echo -e "${BLUE}    阿里云 CDT 流量监控 & 日报 一键部署/管理脚本 (修复增强版)  ${NC}"
echo -e "${BLUE}=============================================================${NC}"

if [ "$EUID" -ne 0 ]; then
  echo -e "${RED}请使用 root 权限运行 (sudo -i)${NC}"
  exit 1
fi

# ================= 核心功能函数 =================

# 收集单个用户信息的函数
function get_single_user_json() {
    local AK="" SK="" REGION="" INSTANCE="" NAME="" LIMIT="" BILL_ENDPOINT="" CURRENCY=""

    echo -e "\n${BLUE}>> 配置阿里云账号/实例信息${NC}"
    read -p "请输入备注名 (例如 HK-Server): " NAME
    
    echo -e "${CYAN}💡 提示: AccessKey 在 RAM 用户详情页 -> 创建 AccessKey${NC}"
    read -p "AccessKey ID: " AK
    read -p "AccessKey Secret: " SK
    
    # --- 按实例区分国内外账单体系 ---
    echo -e "\n${CYAN}💡 提示: 请选择该账号所属的阿里云类型 (决定账单查询节点与货币单位)${NC}"
    echo "  1) 国内区 (阿里云中国站，人民币 ￥ 结算)"
    echo "  2) 国际区 (阿里云国际站，美元 $ 结算)"
    read -p "请选择 (1-2, 默认 1): " ACC_TYPE_OPT
    if [ "$ACC_TYPE_OPT" == "2" ]; then
        BILL_ENDPOINT="business.ap-southeast-1.aliyuncs.com"
        CURRENCY="$"
    else
        BILL_ENDPOINT="business.aliyuncs.com"
        CURRENCY="¥"
    fi
    echo -e "${GREEN}已设置为: 账单节点=$BILL_ENDPOINT | 货币=$CURRENCY${NC}\n"
    # --------------------------------------

    echo -e "${CYAN}💡 提示: 请选择 ECS 实例所在的区域 (输入数字)${NC}"
    echo "  1) 香港 (cn-hongkong)"
    echo "  2) 新加坡 (ap-southeast-1)"
    echo "  3) 日本-东京 (ap-northeast-1)"
    echo "  4) 美国-硅谷 (us-west-1)"
    echo "  5) 美国-弗吉尼亚 (us-east-1)"
    echo "  6) 德国-法兰克福 (eu-central-1)"
    echo "  7) 英国-伦敦 (eu-west-1)"
    echo "  8) 手动输入其他区域代码"
    read -p "请选择 (1-8): " REGION_OPT

    case $REGION_OPT in
        1) REGION="cn-hongkong" ;;
        2) REGION="ap-southeast-1" ;;
        3) REGION="ap-northeast-1" ;;
        4) REGION="us-west-1" ;;
        5) REGION="us-east-1" ;;
        6) REGION="eu-central-1" ;;
        7) REGION="eu-west-1" ;;
        *) read -p "请输入 Region ID (如 cn-shanghai): " REGION ;;
    esac

    echo -e "${CYAN}💡 提示: 请前往 ECS 控制台 -> 实例列表 -> 实例 ID 列 (以 i- 开头)${NC}"
    read -p "ECS 实例 ID: " INSTANCE
    
    read -p "关机阈值 (GB, 默认180): " LIMIT
    LIMIT=${LIMIT:-180}

    # 将构建好的 JSON 字符串赋值给全局变量 (去除了 resgroup，加入了 bill_endpoint 和 currency)
    CURRENT_USER_JSON="{\"name\": \"$NAME\", \"ak\": \"$AK\", \"sk\": \"$SK\", \"region\": \"$REGION\", \"instance_id\": \"$INSTANCE\", \"traffic_limit\": $LIMIT, \"quota\": 200, \"bill_endpoint\": \"$BILL_ENDPOINT\", \"currency\": \"$CURRENCY\", \"paused\": false}"
}

function ensure_python_env() {
    if [ ! -d "$VENV_DIR" ]; then
        python3 -m venv "$VENV_DIR"
        echo -e "${GREEN}虚拟环境创建完成。${NC}"
    fi

    echo -e "${YELLOW}>> 安装 Python 依赖库...${NC}"
    "$VENV_DIR/bin/pip" install \
        requests \
        aliyun-python-sdk-core \
        aliyun-python-sdk-ecs \
        aliyun-python-sdk-bssopenapi \
        'python-telegram-bot[job-queue]' \
        --upgrade >/dev/null 2>&1
}

function download_runtime_scripts() {
    echo -e "${YELLOW}>> 从 GitHub 下载最新脚本...${NC}"
    wget -q -O "${TARGET_DIR}/monitor.py" "${REPO_URL}/monitor.py"
    wget -q -O "${TARGET_DIR}/report.py" "${REPO_URL}/report.py"
    wget -q -O "${TARGET_DIR}/ecs_bot.py" "${REPO_URL}/ecs_bot.py"

    if [ ! -s "${TARGET_DIR}/monitor.py" ] || [ ! -s "${TARGET_DIR}/report.py" ] || [ ! -s "${TARGET_DIR}/ecs_bot.py" ]; then
        echo -e "${RED}下载失败！请检查网络或 GitHub 地址是否正确。${NC}"
        exit 1
    fi
}

function collect_admin_users_json() {
    local ADMIN_IDS=""

    echo -e "${CYAN}机器人控制权限需要 Telegram 用户 ID，不是群组 Chat ID。${NC}"
    echo -e "${CYAN}可通过 @userinfobot 获取自己的 Telegram 用户 ID。${NC}"
    while true; do
        read -p "请输入允许控制 ECS 的 Telegram 用户 ID，多个用英文逗号分隔: " ADMIN_IDS
        ADMIN_USERS_JSON=$(ADMIN_IDS="$ADMIN_IDS" python3 - <<'PY'
import json
import os
import re
import sys

raw = os.environ.get("ADMIN_IDS", "").replace("，", ",")
ids = []
for item in re.split(r"[,\s]+", raw):
    if not item:
        continue
    try:
        ids.append(int(item))
    except ValueError:
        print("[]")
        sys.exit(2)

ids = list(dict.fromkeys(ids))
if not ids:
    print("[]")
    sys.exit(3)
print(json.dumps(ids, ensure_ascii=False))
PY
)
        if [ "$ADMIN_USERS_JSON" != "[]" ]; then
            break
        fi
        echo -e "${RED}管理员用户 ID 不能为空，且必须是数字。${NC}"
    done
}

function update_admin_users_config() {
    collect_admin_users_json
    ADMIN_USERS_JSON="$ADMIN_USERS_JSON" CONFIG_FILE="$CONFIG_FILE" python3 - <<'PY'
import json
import os

config_file = os.environ["CONFIG_FILE"]
admin_users = json.loads(os.environ["ADMIN_USERS_JSON"])
with open(config_file, "r", encoding="utf-8") as file:
    data = json.load(file)
data["admin_users"] = admin_users
with open(config_file, "w", encoding="utf-8") as file:
    json.dump(data, file, ensure_ascii=False, indent=4)
PY
    echo -e "${GREEN}管理员用户 ID 已更新。${NC}"
}

function systemd_available() {
    command -v systemctl >/dev/null 2>&1 && [ -d /run/systemd/system ]
}

function create_bot_service() {
    if ! systemd_available; then
        echo -e "${YELLOW}当前系统未检测到 systemd，已跳过后台服务创建。${NC}"
        echo -e "可手动运行：${YELLOW}${VENV_DIR}/bin/python ${TARGET_DIR}/ecs_bot.py${NC}"
        return 1
    fi

    cat > "$BOT_SERVICE_FILE" <<EOF
[Unit]
Description=Aliyun ECS Telegram Bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=${TARGET_DIR}
ExecStart=${VENV_DIR}/bin/python ${TARGET_DIR}/ecs_bot.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

    systemctl daemon-reload
    systemctl enable --now "$BOT_SERVICE_NAME"
    echo -e "${GREEN}Telegram 控制机器人已启用：${BOT_SERVICE_NAME}${NC}"
}

function stop_bot_service() {
    if command -v systemctl >/dev/null 2>&1; then
        systemctl disable --now "$BOT_SERVICE_NAME" >/dev/null 2>&1 || true
        rm -f "$BOT_SERVICE_FILE"
        systemctl daemon-reload >/dev/null 2>&1 || true
        echo -e "${GREEN}Telegram 控制机器人已停用。${NC}"
    else
        echo -e "${YELLOW}当前系统没有 systemctl，请手动停止 ecs_bot.py 进程。${NC}"
    fi
}

function show_bot_status() {
    if command -v systemctl >/dev/null 2>&1 && [ -f "$BOT_SERVICE_FILE" ]; then
        systemctl status "$BOT_SERVICE_NAME" --no-pager -l || true
    else
        echo -e "${YELLOW}未检测到 ${BOT_SERVICE_NAME} 服务。${NC}"
        echo -e "手动运行命令：${YELLOW}${VENV_DIR}/bin/python ${TARGET_DIR}/ecs_bot.py${NC}"
    fi
}

function enable_bot_service() {
    if [ ! -d "$TARGET_DIR" ]; then
        mkdir -p "$TARGET_DIR"
    fi
    ensure_python_env
    download_runtime_scripts

    if [ ! -f "$CONFIG_FILE" ]; then
        echo -e "${RED}配置文件不存在，无法启用机器人。请先完成首次安装。${NC}"
        return 1
    fi

    update_admin_users_config
    create_bot_service || true
}

# 完整安装流程 (首次运行)
function run_full_install() {
    # 1. 目录准备
    if [ ! -d "$TARGET_DIR" ]; then
        mkdir -p "$TARGET_DIR"
        echo -e "${GREEN}创建目录: ${TARGET_DIR}${NC}"
    fi

    # 2. 安装依赖
    echo -e "${YELLOW}>> 安装系统依赖...${NC}"
    if [ -f /etc/debian_version ]; then
        apt-get update -y && apt-get install -y python3 python3-venv python3-pip cron wget
    elif [ -f /etc/redhat-release ]; then
        yum install -y python3 python3-pip cronie wget
        systemctl enable crond && systemctl start crond
    fi

    # 3. 虚拟环境与源码
    ensure_python_env
    download_runtime_scripts

    # 6. 交互式配置 Telegram
    echo -e "\n${BLUE}### 配置 Telegram ###${NC}"
    echo -e "1. 联系 ${CYAN}@BotFather${NC} -> 创建机器人获取 Token"
    echo -e "2. 联系 ${CYAN}@userinfobot${NC} -> 获取您的 Chat ID"
    read -p "请输入 Telegram Bot Token: " TG_TOKEN
    read -p "请输入 Telegram Chat ID: " TG_ID

    echo ""
    read -p "是否启用 Telegram 控制机器人？可远程开机/关机/重启 ECS (y/n, 默认 n): " ENABLE_BOT
    ENABLE_BOT=${ENABLE_BOT:-n}
    if [[ "$ENABLE_BOT" =~ ^[Yy]$ ]]; then
        collect_admin_users_json
    else
        ADMIN_USERS_JSON="[]"
    fi

    # 7. 配置阿里云对象
    USERS_JSON=""
    while true; do
        get_single_user_json
        
        if [ -z "$USERS_JSON" ]; then
            USERS_JSON="$CURRENT_USER_JSON"
        else
            USERS_JSON="$USERS_JSON, $CURRENT_USER_JSON"
        fi

        echo ""
        read -p "是否继续添加第二个账号/实例? (y/n): " CONTIN
        if [[ ! "$CONTIN" =~ ^[Yy]$ ]]; then
            break
        fi
    done

    # 8. 生成配置文件
    cat > "$CONFIG_FILE" <<EOF
{
    "telegram": {
        "bot_token": "$TG_TOKEN",
        "chat_id": "$TG_ID"
    },
    "admin_users": $ADMIN_USERS_JSON,
    "users": [
        $USERS_JSON
    ]
}
EOF
    echo -e "${GREEN}配置文件已生成: ${CONFIG_FILE}${NC}"

    # 9. 设置 Crontab
    echo -e "${YELLOW}>> 配置定时任务...${NC}"
    crontab -l > /tmp/cron_bk 2>/dev/null
    grep -v "aliyun_monitor" /tmp/cron_bk > /tmp/cron_clean
    echo "*/5 * * * * ${VENV_DIR}/bin/python ${TARGET_DIR}/monitor.py >> ${TARGET_DIR}/monitor.log 2>&1 #aliyun_monitor" >> /tmp/cron_clean
    echo "0 9 * * * ${VENV_DIR}/bin/python ${TARGET_DIR}/report.py >> ${TARGET_DIR}/report.log 2>&1 #aliyun_monitor" >> /tmp/cron_clean
    crontab /tmp/cron_clean
    rm /tmp/cron_bk /tmp/cron_clean

    if [[ "$ENABLE_BOT" =~ ^[Yy]$ ]]; then
        create_bot_service || true
    fi

    echo -e "\n${GREEN}🎉 安装与配置完成！${NC}"
    echo -e "您可以使用以下命令手动测试日报发送："
    echo -e "${YELLOW}${VENV_DIR}/bin/python ${TARGET_DIR}/report.py${NC}"
}

function run_bot_manage_menu() {
    while true; do
        echo -e "\n${GREEN}========== Telegram 控制机器人 ==========${NC}"
        echo "1) 启用/重启机器人服务"
        echo "2) 停用机器人服务"
        echo "3) 查看机器人服务状态"
        echo "4) 修改管理员用户 ID"
        echo "5) 返回上级菜单"
        echo -e "${GREEN}=========================================${NC}"
        read -p "请输入序号 (1-5): " BOT_OPT

        case $BOT_OPT in
            1)
                enable_bot_service
                ;;
            2)
                stop_bot_service
                ;;
            3)
                show_bot_status
                ;;
            4)
                if [ ! -f "$CONFIG_FILE" ]; then
                    echo -e "${RED}配置文件不存在，无法修改管理员用户 ID。${NC}"
                else
                    update_admin_users_config
                    if command -v systemctl >/dev/null 2>&1 && systemctl is-active --quiet "$BOT_SERVICE_NAME"; then
                        systemctl restart "$BOT_SERVICE_NAME"
                        echo -e "${GREEN}机器人服务已重启并加载新管理员配置。${NC}"
                    fi
                fi
                ;;
            5)
                return
                ;;
            *)
                echo -e "${RED}输入无效，请重新选择。${NC}"
                ;;
        esac
    done
}

# 管理菜单 (二次运行)
function run_manage_menu() {
    while true; do
        echo -e "\n${GREEN}=====================================${NC}"
        echo -e "${YELLOW}已检测到存在配置文件，请选择管理操作：${NC}"
        echo "1) 添加新的监控实例 (Add)"
        echo "2) 删除已有监控实例 (Delete)"
        echo "3) 暂停/恢复监控实例 (Pause/Resume)"
        echo "4) 更新脚本并重置所有配置 (Update & Reset)"
        echo "5) Telegram 控制机器人管理"
        echo "6) 退出脚本 (Exit)"
        echo -e "${GREEN}=====================================${NC}"
        read -p "请输入序号 (1-6): " MENU_OPT

        case $MENU_OPT in
            1)
                get_single_user_json
                python3 -c "
import json
with open('$CONFIG_FILE', 'r') as f:
    data = json.load(f)
data['users'].append(json.loads('''$CURRENT_USER_JSON'''))
with open('$CONFIG_FILE', 'w') as f:
    json.dump(data, f, indent=4)
"
                echo -e "${GREEN}✅ 实例添加成功！配置文件已更新。${NC}"
                ;;
            2)
                echo -e "\n${BLUE}当前监控的实例列表：${NC}"
                python3 -c "
import json
with open('$CONFIG_FILE', 'r') as f:
    users = json.load(f).get('users', [])
if not users:
    print('当前没有配置任何监控实例。')
else:
    for i, u in enumerate(users):
        print(f' [{i}] 备注名: {u.get(\"name\")} | 实例ID: {u.get(\"instance_id\")} | 区域: {u.get(\"region\")}')
"
                echo ""
                read -p "请输入要删除的实例序号 (输入 q 取消): " DEL_IDX
                if [[ "$DEL_IDX" == "q" || -z "$DEL_IDX" ]]; then
                    continue
                fi
                python3 -c "
import json, sys
idx = int('$DEL_IDX')
with open('$CONFIG_FILE', 'r') as f:
    data = json.load(f)
try:
    removed = data['users'].pop(idx)
    with open('$CONFIG_FILE', 'w') as f:
        json.dump(data, f, indent=4)
    print(f'\n\033[0;32m✅ 成功删除实例: {removed.get(\"name\")} ({removed.get(\"instance_id\")})\033[0m')
except Exception as e:
    print(f'\n\033[0;31m❌ 删除失败: 无效的序号 {idx}\033[0m')
"
                ;;
            3)
                echo -e "\n${BLUE}当前监控的实例列表：${NC}"
                python3 -c "
import json
with open('$CONFIG_FILE', 'r') as f:
    users = json.load(f).get('users', [])
if not users:
    print('当前没有配置任何监控实例。')
else:
    for i, u in enumerate(users):
        paused = '已暂停' if u.get('paused') or u.get('disabled') else '运行中'
        print(f' [{i}] 备注名: {u.get(\"name\")} | 实例ID: {u.get(\"instance_id\")} | 状态: {paused}')
"
                echo ""
                read -p "请输入要切换暂停/恢复的实例序号 (输入 q 取消): " TOGGLE_IDX
                if [[ "$TOGGLE_IDX" == "q" || -z "$TOGGLE_IDX" ]]; then
                    continue
                fi
                python3 -c "
import json
idx = int('$TOGGLE_IDX')
with open('$CONFIG_FILE', 'r') as f:
    data = json.load(f)
try:
    user = data['users'][idx]
    paused = bool(user.get('paused') or user.get('disabled'))
    user['paused'] = not paused
    user.pop('disabled', None)
    with open('$CONFIG_FILE', 'w') as f:
        json.dump(data, f, indent=4)
    state = '已暂停' if user['paused'] else '已恢复'
    print(f'\n\033[0;32m✅ 成功切换实例: {user.get(\"name\")} ({user.get(\"instance_id\")}) -> {state}\033[0m')
except Exception:
    print(f'\n\033[0;31m❌ 操作失败: 无效的序号 {idx}\033[0m')
"
                ;;
            4)
                echo -e "${RED}⚠️ 此操作将更新代码并覆盖现有的 config.json！${NC}"
                read -p "确认要更新并重置配置吗？(y/n): " CONFIRM_REINSTALL
                if [[ "$CONFIRM_REINSTALL" =~ ^[Yy]$ ]]; then
                    run_full_install
                    exit 0
                fi
                ;;
            5)
                run_bot_manage_menu
                ;;
            6)
                echo -e "${GREEN}退出脚本。${NC}"
                exit 0
                ;;
            *)
                echo -e "${RED}输入无效，请重新选择。${NC}"
                ;;
        esac
    done
}

# ================= 脚本入口 =================

if [ -f "$CONFIG_FILE" ]; then
    # 如果检测到 config.json 已存在，进入管理菜单
    run_manage_menu
else
    # 首次安装
    run_full_install
fi

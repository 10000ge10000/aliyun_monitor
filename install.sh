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

echo -e "${BLUE}=============================================================${NC}"
echo -e "${BLUE}           阿里云 CDT 流量监控 & 日报 一键部署脚本            ${NC}"
echo -e "${BLUE}=============================================================${NC}"

if [ "$EUID" -ne 0 ]; then
  echo -e "${RED}请使用 root 权限运行 (sudo -i)${NC}"
  exit 1
fi

# 1. 目录准备
TARGET_DIR="/opt/scripts"
mkdir -p "$TARGET_DIR"

# 2. 安装依赖
echo -e "${YELLOW}>> 安装系统依赖...${NC}"
if [ -f /etc/alpine-release ]; then
    apk update && apk add bash python3 py3-pip curl wget ca-certificates
elif [ -f /etc/debian_version ]; then
    apt-get update -y && apt-get install -y python3 python3-venv python3-pip cron wget
elif [ -f /etc/redhat-release ]; then
    yum install -y python3 python3-pip cronie wget
    systemctl enable crond && systemctl start crond
fi

# 3. 虚拟环境
VENV_DIR="${TARGET_DIR}/venv"
if [ ! -d "$VENV_DIR" ]; then
    python3 -m venv "$VENV_DIR"
    echo -e "${GREEN}虚拟环境创建完成。${NC}"
fi

# 4. 安装 Python 依赖库
echo -e "${YELLOW}>> 安装 Python 依赖库...${NC}"
"$VENV_DIR/bin/pip" install --upgrade pip >/dev/null 2>&1
"$VENV_DIR/bin/pip" install requests aliyun-python-sdk-core >/dev/null 2>&1

# 5. 下载源码
echo -e "${YELLOW}>> 从 GitHub 下载最新脚本...${NC}"
wget -O "${TARGET_DIR}/monitor.py" "${REPO_URL}/monitor.py"
wget -O "${TARGET_DIR}/report.py" "${REPO_URL}/report.py"

if [ ! -s "${TARGET_DIR}/monitor.py" ]; then
    echo -e "${RED}下载失败！请检查网络或 GitHub 地址是否正确。${NC}"
    exit 1
fi

# 6. 交互式配置
echo -e "\n${BLUE}### 配置 Telegram ###${NC}"
echo -e "1. 联系 ${CYAN}@BotFather${NC} -> 创建机器人获取 Token"
echo -e "2. 联系 ${CYAN}@userinfobot${NC} -> 获取您的 Chat ID"
read -p "请输入 Telegram Bot Token: " TG_TOKEN
read -p "请输入 Telegram Chat ID: " TG_ID

echo -e "\n${BLUE}### 配置阿里云 RAM ###${NC}"
echo -e "请前往阿里云 RAM 控制台创建用户："
echo -e "🔗 地址: ${YELLOW}https://ram.console.alibabacloud.com/users${NC}"
echo -e "⚠️  权限要求: AliyunECSFullAccess, AliyunCDTFullAccess, AliyunBSSReadOnlyAccess"

USERS_JSON=""

while true; do
    # 变量初始化
    NAME=""
    AK=""
    SK=""
    REGION=""
    RESGROUP=""
    INSTANCE=""
    
    echo -e "\n${BLUE}>> 添加一个阿里云账号${NC}"
    
    echo -e "${CYAN}💡 提示: AccessKey 在 RAM 用户详情页 -> 创建 AccessKey${NC}"
    read -p "AccessKey ID: " AK
    read -p "AccessKey Secret: " SK
    
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

    echo -e "${CYAN}💡 提示: 如RAM用户授权到资源组，请输入资源组ID，否则留空${NC}"
    read -p "资源组 ID: " RESGROUP

    echo -e "${CYAN}💡 提示: 请前往 ECS 控制台 -> 实例列表 -> 实例 ID 列 (以 i- 开头)${NC}"
    read -p "ECS 实例 ID: " INSTANCE
    
    # 去空格处理
    AK=$(echo "$AK" | tr -d '[:space:]')
    SK=$(echo "$SK" | tr -d '[:space:]')
    REGION=$(echo "$REGION" | tr -d '[:space:]')
    RESGROUP=$(echo "$RESGROUP" | tr -d '[:space:]')
    INSTANCE=$(echo "$INSTANCE" | tr -d '[:space:]')
    
    # [修复点] 备注名处理逻辑优化
    read -p "请输入备注名 (留空则使用实例ID): " NAME
    
    # 1. 先去空格 (解决用户输入空格导致误判的问题)
    NAME=$(echo "$NAME" | tr -d '[:space:]')
    
    # 2. 如果去空后是空的，则使用 Instance ID
    if [ -z "$NAME" ]; then
        NAME="$INSTANCE"
    fi
    
    # 3. 如果 Instance ID 也是空的 (极少见)，给个默认名
    if [ -z "$NAME" ]; then
        NAME="Unamed_Server"
    fi

    # 阈值
    read -p "关机阈值 (GB, 默认180): " LIMIT
    LIMIT=${LIMIT:-180}
    
    read -p "账单报警阈值 ($美元, 默认1.0): " BILL_LIMIT
    BILL_LIMIT=${BILL_LIMIT:-1.0}

    # 构建 JSON
    USER_OBJ="{\"name\": \"$NAME\", \"ak\": \"$AK\", \"sk\": \"$SK\", \"region\": \"$REGION\", \"resgroup\": \"$RESGROUP\", \"instance_id\": \"$INSTANCE\", \"traffic_limit\": $LIMIT, \"bill_threshold\": $BILL_LIMIT, \"quota\": 200}"

    if [ -z "$USERS_JSON" ]; then
        USERS_JSON="$USER_OBJ"
    else
        USERS_JSON="$USERS_JSON, $USER_OBJ"
    fi

    echo -e "${GREEN}✅ 已添加账号: ${NAME}${NC}"
    echo ""
    read -p "是否继续添加下一个账号? (y/N): " CONTIN
    if [[ ! "$CONTIN" =~ ^[Yy]$ ]]; then
        break
    fi
done

# 生成配置
cat > "${TARGET_DIR}/config.json" <<EOF
{
    "telegram": {
        "bot_token": "$TG_TOKEN",
        "chat_id": "$TG_ID"
    },
    "users": [
        $USERS_JSON
    ]
}
EOF
echo -e "${GREEN}配置文件已生成: ${TARGET_DIR}/config.json${NC}"

# 设置 Crontab
echo -e "${YELLOW}>> 配置定时任务...${NC}"
crontab -l > /tmp/cron_bk 2>/dev/null
grep -v "aliyun_monitor" /tmp/cron_bk > /tmp/cron_clean

echo "* * * * * PYTHONWARNINGS=ignore ${VENV_DIR}/bin/python ${TARGET_DIR}/monitor.py >> ${TARGET_DIR}/monitor.log 2>&1 #aliyun_monitor" >> /tmp/cron_clean
echo "0 9 * * * PYTHONWARNINGS=ignore ${VENV_DIR}/bin/python ${TARGET_DIR}/report.py >> ${TARGET_DIR}/report.log 2>&1 #aliyun_monitor" >> /tmp/cron_clean

crontab /tmp/cron_clean
rm /tmp/cron_bk /tmp/cron_clean

echo -e "\n${GREEN}🎉 安装完成！${NC}"
echo -e "您可以使用以下命令手动测试日报发送："
echo -e "${YELLOW}PYTHONWARNINGS=ignore ${VENV_DIR}/bin/python ${TARGET_DIR}/report.py${NC}"

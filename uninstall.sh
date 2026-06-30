#!/bin/bash

# 定义颜色
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

TARGET_DIR="/opt/scripts"
BOT_SERVICE_NAME="aliyun-ecs-bot.service"
BOT_SERVICE_FILE="/etc/systemd/system/${BOT_SERVICE_NAME}"

clear
echo -e "${BLUE}=============================================================${NC}"
echo -e "${BLUE}       阿里云 CDT 监控 - 彻底卸载与清理工具 (Safe Mode)      ${NC}"
echo -e "${BLUE}=============================================================${NC}"

# 1. 权限检查
if [ "$EUID" -ne 0 ]; then
  echo -e "${RED}错误：请使用 root 权限运行 (sudo -i)${NC}"
  exit 1
fi

echo -e "${RED}警告：此操作将执行以下清理：${NC}"
echo -e "  1. 停止并移除 Telegram 控制机器人 systemd 服务（如已启用）"
echo -e "  2. 停止并移除相关的 Crontab 定时任务"
echo -e "  3. 永久删除目录: ${TARGET_DIR} (包含配置、日志、虚拟环境)"
echo -e "  4. 清理安装时产生的临时文件"
echo ""

read -p "确认要执行卸载吗？(y/n): " CONFIRM
if [[ ! "$CONFIRM" =~ ^[Yy]$ ]]; then
    echo -e "${YELLOW}已取消操作。${NC}"
    exit 0
fi

echo ""

# 2. 清理 Bot systemd 服务
echo -e "${YELLOW}>> [1/4] 正在清理 Telegram 控制机器人服务...${NC}"
if command -v systemctl >/dev/null 2>&1; then
    systemctl disable --now "$BOT_SERVICE_NAME" >/dev/null 2>&1 || true
    if [ -f "$BOT_SERVICE_FILE" ]; then
        rm -f "$BOT_SERVICE_FILE"
        systemctl daemon-reload >/dev/null 2>&1 || true
        echo -e "${GREEN}✓ 已移除 ${BOT_SERVICE_NAME}。${NC}"
    else
        echo -e "${GREEN}✓ 未发现 ${BOT_SERVICE_NAME} 服务文件，跳过。${NC}"
    fi
else
    echo -e "${GREEN}✓ 当前系统无 systemctl，跳过 systemd 服务清理。${NC}"
fi

# 3. 清理 Crontab (安全模式)
echo -e "${YELLOW}>> [2/4] 正在清理定时任务...${NC}"
# 导出当前任务
if crontab -l > /tmp/cron_backup 2>/dev/null; then
    # 检查是否存在相关任务
    if grep -q "aliyun_monitor" /tmp/cron_backup; then
        # 反向匹配：保留不包含 aliyun_monitor 的行
        grep -v "aliyun_monitor" /tmp/cron_backup > /tmp/cron_clean
        
        # 恢复清理后的任务列表
        crontab /tmp/cron_clean
        rm -f /tmp/cron_clean
        echo -e "${GREEN}✓ 已移除监控相关的 Crontab 任务。${NC}"
    else
        echo -e "${GREEN}✓ 未发现相关的 Crontab 任务，跳过。${NC}"
    fi
    rm -f /tmp/cron_backup
else
    echo -e "${GREEN}✓ 当前用户无 Crontab 任务，跳过。${NC}"
fi

# 4. 删除文件目录
echo -e "${YELLOW}>> [3/4] 正在删除程序文件与数据...${NC}"
if [ -d "$TARGET_DIR" ]; then
    # 彻底删除目录
    rm -rf "$TARGET_DIR"
    if [ ! -d "$TARGET_DIR" ]; then
        echo -e "${GREEN}✓ 已彻底移除目录: ${TARGET_DIR}${NC}"
    else
        echo -e "${RED}❌ 目录删除失败，请手动检查: ${TARGET_DIR}${NC}"
    fi
else
    echo -e "${GREEN}✓ 目录不存在，跳过。${NC}"
fi

# 5. 清除下载痕迹 (可选)
echo -e "${YELLOW}>> [4/4] 正在清理下载痕迹...${NC}"

# 尝试删除当前目录下的 install.sh (如果存在)
if [ -f "./install.sh" ]; then
    rm -f "./install.sh"
    echo -e "${GREEN}✓ 已删除当前目录下的 install.sh${NC}"
fi

echo -e "\n${GREEN}🎉 卸载完成！系统已恢复干净。${NC}"

# 5. 自我毁灭 (阅后即焚)
echo ""
read -p "是否删除此卸载脚本 (uninstall.sh) 以完全清除痕迹？(y/n): " SELF_DEL
if [[ "$SELF_DEL" =~ ^[Yy]$ ]]; then
    rm -- "$0"
    echo -e "${GREEN}✓ 卸载脚本已自毁。Bye!${NC}"
fi

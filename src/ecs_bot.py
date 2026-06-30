# -*- coding: utf-8 -*-
"""
Telegram bot for Aliyun ECS operations.

The bot is intentionally opt-in from install.sh because it can start, stop and
reboot ECS instances. Authorization is always checked against admin_users in
/opt/scripts/config.json.
"""

import asyncio
import json
import logging
import os
import socket
import sys
import time as time_module
import warnings
from datetime import datetime, time as dt_time
from logging.handlers import TimedRotatingFileHandler
from typing import Dict, List, Optional, Tuple

from telegram import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from aliyunsdkbssopenapi.request.v20171214 import DescribeInstanceBillRequest
from aliyunsdkcore.acs_exception.exceptions import ClientException, ServerException
from aliyunsdkcore.client import AcsClient
from aliyunsdkcore.request import CommonRequest
from aliyunsdkecs.request.v20140526.DescribeInstancesRequest import DescribeInstancesRequest
from aliyunsdkecs.request.v20140526.RebootInstanceRequest import RebootInstanceRequest
from aliyunsdkecs.request.v20140526.StartInstanceRequest import StartInstanceRequest
from aliyunsdkecs.request.v20140526.StopInstanceRequest import StopInstanceRequest

try:
    from aliyunsdkcore.vendored.requests.packages.urllib3.util import ssl_

    ssl_.HAS_SNI = True
except Exception:
    pass

_orig_getaddrinfo = socket.getaddrinfo


def _getaddrinfo_ipv4_only(host, port, family=0, type=0, proto=0, flags=0):
    result = _orig_getaddrinfo(host, port, family, type, proto, flags)
    ipv4_result = [item for item in result if item[0] == socket.AF_INET]
    return ipv4_result if ipv4_result else result


socket.getaddrinfo = _getaddrinfo_ipv4_only
warnings.filterwarnings("ignore")

CONFIG_FILE = "/opt/scripts/config.json"
STATE_FILE = "/opt/scripts/bot_state.json"
LOG_FILE = "/opt/scripts/bot.log"
TIMEZONE_OFFSET_HOURS = 8

SELECTING_INSTANCE, SELECTING_OPERATION, SELECTING_TIMER_TYPE, SET_TIMER_TIME, CONFIRM_TIMER = range(5)

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")

logger = logging.getLogger("aliyun_ecs_bot")
logger.setLevel(logging.INFO)
if not logger.handlers:
    os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
    file_handler = TimedRotatingFileHandler(LOG_FILE, when="D", interval=1, backupCount=7, encoding="utf-8")
    file_handler.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
    logger.addHandler(file_handler)
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
    logger.addHandler(console_handler)


def load_config() -> Dict:
    if not os.path.exists(CONFIG_FILE):
        logger.error("配置文件不存在: %s", CONFIG_FILE)
        sys.exit(1)
    with open(CONFIG_FILE, "r", encoding="utf-8") as file:
        config = json.load(file)
    token = get_bot_token(config)
    if not token:
        logger.error("Telegram Bot Token 未配置")
        sys.exit(1)
    return config


def get_bot_token(config: Dict) -> str:
    return TELEGRAM_BOT_TOKEN or config.get("telegram", {}).get("bot_token", "")


def load_state() -> Dict:
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as file:
                state = json.load(file)
            if isinstance(state, dict):
                state.setdefault("timers", {})
                return state
        except Exception as error:
            logger.warning("加载机器人状态失败: %s", error)
    return {"timers": {}}


def save_state(state: Dict) -> None:
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as file:
            json.dump(state, file, ensure_ascii=False, indent=2)
    except Exception as error:
        logger.error("保存机器人状态失败: %s", error)


def load_user_configs() -> List[Dict]:
    return load_config().get("users", [])


def get_user_friendly_name(user_config: Dict) -> str:
    return user_config.get("name") or user_config.get("instance_id") or "Unknown"


def find_user_config(identifier: str) -> Optional[Dict]:
    for user_config in load_user_configs():
        if user_config.get("name") == identifier or user_config.get("instance_id") == identifier:
            return user_config
    return None


def admin_user_ids(config: Dict) -> List[int]:
    result = []
    for value in config.get("admin_users", []):
        try:
            result.append(int(value))
        except (TypeError, ValueError):
            logger.warning("忽略无效 Telegram 管理员 ID: %r", value)
    return result


def is_authorized(update: Update) -> bool:
    user = update.effective_user
    if not user:
        return False
    return user.id in admin_user_ids(load_config())


async def reject_unauthorized(update: Update) -> None:
    message = update.effective_message
    if message:
        await message.reply_text("无权限。请确认 config.json 中 admin_users 已配置你的 Telegram 用户 ID。")
    elif update.callback_query:
        await update.callback_query.answer("无权限", show_alert=True)


def require_selected_config(context: ContextTypes.DEFAULT_TYPE) -> Optional[Dict]:
    selected = context.user_data.get("selected_config")
    return selected if isinstance(selected, dict) else None


class AliCloudManager:
    def __init__(self, ak: str, sk: str, region: str):
        self.ak = ak
        self.sk = sk
        self.region = region
        self.client = AcsClient(ak, sk, region)

    def _do_request(self, request, retries: int = 3) -> Optional[Dict]:
        for attempt in range(1, retries + 1):
            try:
                response = self.client.do_action_with_exception(request)
                return json.loads(response.decode("utf-8"))
            except (ClientException, ServerException, Exception) as error:
                if attempt < retries:
                    time_module.sleep(2 * attempt)
                    continue
                logger.error("Aliyun API 请求失败: %s", error)
                return None
        return None

    def get_instance_detail(self, instance_id: str) -> Optional[Dict]:
        request = DescribeInstancesRequest()
        request.set_InstanceIds(json.dumps([instance_id]))
        data = self._do_request(request)
        instances = (data or {}).get("Instances", {}).get("Instance", [])
        if not instances:
            return None

        instance = instances[0]
        public_ips = instance.get("PublicIpAddress", {}).get("IpAddress", [])
        eip = instance.get("EipAddress", {}).get("IpAddress", "")
        ip_address = eip or (public_ips[0] if public_ips else "无公网IP")
        memory_mb = instance.get("Memory", 0)
        memory = str(int(memory_mb / 1024)) if memory_mb > 0 and memory_mb % 1024 == 0 else f"{memory_mb / 1024:.1f}"

        return {
            "instance_id": instance_id,
            "name": instance.get("InstanceName", instance_id),
            "status": instance.get("Status", "Unknown"),
            "ip": ip_address,
            "spec": f"{instance.get('Cpu', 0)}C{memory}G",
        }

    def start_instance(self, instance_id: str) -> Tuple[bool, str]:
        try:
            request = StartInstanceRequest()
            request.set_InstanceId(instance_id)
            self.client.do_action_with_exception(request)
            return True, "启动命令已发送"
        except Exception as error:
            return False, f"启动失败: {error}"

    def stop_instance(self, instance_id: str, force: bool = False) -> Tuple[bool, str]:
        try:
            request = StopInstanceRequest()
            request.set_InstanceId(instance_id)
            request.set_ForceStop(force)
            self.client.do_action_with_exception(request)
            return True, "停止命令已发送"
        except Exception as error:
            return False, f"停止失败: {error}"

    def reboot_instance(self, instance_id: str, force: bool = False) -> Tuple[bool, str]:
        try:
            request = RebootInstanceRequest()
            request.set_InstanceId(instance_id)
            request.set_ForceStop(force)
            self.client.do_action_with_exception(request)
            return True, "重启命令已发送"
        except Exception as error:
            return False, f"重启失败: {error}"

    def get_current_traffic(self) -> Optional[float]:
        try:
            client = AcsClient(self.ak, self.sk, "cn-hangzhou")
            request = CommonRequest()
            request.set_domain("cdt.aliyuncs.com")
            request.set_version("2021-08-13")
            request.set_action_name("ListCdtInternetTraffic")
            request.set_method("POST")
            request.set_connect_timeout(5000)
            request.set_read_timeout(15000)
            response = client.do_action_with_exception(request)
            data = json.loads(response.decode("utf-8"))
            total_bytes = sum(item.get("Traffic", 0) for item in data.get("TrafficDetails", []))
            return total_bytes / (1024**3)
        except Exception as error:
            logger.warning("查询 CDT 流量失败: %s", error)
            return None

    def get_current_bill(self, instance_id: str) -> Optional[float]:
        try:
            request = DescribeInstanceBillRequest.DescribeInstanceBillRequest()
            request.set_BillingCycle(datetime.now().strftime("%Y-%m"))
            request.set_InstanceID(instance_id)
            request.set_ProductCode("ecs")
            response = self.client.do_action_with_exception(request)
            data = json.loads(response.decode("utf-8"))
            items = data.get("Data", {}).get("Items", [])
            return sum(float(item.get("PretaxAmount", 0)) for item in items)
        except Exception as error:
            logger.warning("查询实例账单失败: %s", error)
            return None


def build_manager(user_config: Dict) -> AliCloudManager:
    return AliCloudManager(user_config["ak"], user_config["sk"], user_config["region"])


def format_status_message(user_config: Dict, detail: Dict, traffic: Optional[float], bill: Optional[float]) -> str:
    name = get_user_friendly_name(user_config)
    quota = user_config.get("traffic_limit", 180)
    bill_threshold = user_config.get("bill_threshold", 1.0)
    currency = user_config.get("currency", "¥")
    status_icons = {"Running": "🟢", "Stopped": "⚫", "Starting": "🟡", "Stopping": "🟡"}
    status_icon = status_icons.get(detail.get("status"), "❓")

    if traffic is None:
        traffic_text = "⚠️ 查询失败"
    else:
        percent = (traffic / quota) * 100 if quota > 0 else 0
        traffic_text = f"{traffic:.2f} GB ({percent:.1f}%)"

    if bill is None:
        bill_text = "⚠️ 查询失败"
        evaluation = "⚠️"
    else:
        bill_text = f"{currency}{bill:.2f}"
        evaluation = "💸 扣费预警" if bill > bill_threshold else "✅"

    return (
        f"📅 时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n"
        f"👤 实例: *{name}* ({detail.get('spec')})\n"
        f"🖥️ 状态: {status_icon} {detail.get('status')}\n"
        f"🌐 IP: `{detail.get('ip')}`\n"
        f"📉 流量: {traffic_text}\n"
        f"💰 账单: {bill_text}\n"
        f"📝 评价: {evaluation}"
    )


def operation_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("开机", callback_data="op_start")],
            [InlineKeyboardButton("关机", callback_data="op_stop")],
            [InlineKeyboardButton("重启", callback_data="op_reboot")],
            [InlineKeyboardButton("状态", callback_data="op_status")],
            [InlineKeyboardButton("定时", callback_data="op_timer_menu")],
            [InlineKeyboardButton("返回", callback_data="back_list")],
        ]
    )


def timer_job_name(instance_id: str, action: str, time_text: str) -> str:
    return f"{instance_id}_{action}_{time_text.replace(':', '_')}"


def register_timer_job(application: Application, user_config: Dict, action: str, time_text: str) -> None:
    if not application.job_queue:
        logger.warning("JobQueue 不可用，无法注册定时任务")
        return

    hour_text, minute_text = time_text.split(":", 1)
    hour = int(hour_text)
    minute = int(minute_text)
    utc_hour = (hour - TIMEZONE_OFFSET_HOURS) % 24
    target_time = dt_time(hour=utc_hour, minute=minute)
    instance_id = user_config["instance_id"]
    job_data = {
        "inst_id": instance_id,
        "ak": user_config["ak"],
        "sk": user_config["sk"],
        "region": user_config["region"],
        "name": get_user_friendly_name(user_config),
    }
    callback = start_timer_callback if action == "start" else stop_timer_callback
    application.job_queue.run_daily(
        callback,
        time=target_time,
        days=tuple(range(7)),
        data=job_data,
        name=timer_job_name(instance_id, action, time_text),
    )
    logger.info("注册定时任务: %s %s %s (UTC %02d:%02d)", instance_id, action, time_text, utc_hour, minute)


async def start_timer_callback(context: ContextTypes.DEFAULT_TYPE) -> None:
    await run_timer_action(context, "start")


async def stop_timer_callback(context: ContextTypes.DEFAULT_TYPE) -> None:
    await run_timer_action(context, "stop")


async def run_timer_action(context: ContextTypes.DEFAULT_TYPE, action: str) -> None:
    data = context.job.data
    manager = AliCloudManager(data["ak"], data["sk"], data["region"])
    name = data.get("name", data["inst_id"])
    ok, message = (
        manager.start_instance(data["inst_id"])
        if action == "start"
        else manager.stop_instance(data["inst_id"], force=False)
    )
    logger.info("定时%s %s: %s, %s", "开机" if action == "start" else "关机", data["inst_id"], ok, message)

    config = load_config()
    chat_id = config.get("telegram", {}).get("chat_id")
    if chat_id:
        action_text = "开机" if action == "start" else "关机"
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"⏰ 定时任务执行\n实例: `{name}`\n操作: {action_text}\n结果: {message}",
            parse_mode="Markdown",
        )


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update):
        await reject_unauthorized(update)
        return
    await update.message.reply_text("阿里云 ECS 控制机器人已就绪。使用 /menu 打开交互菜单。")


async def menu_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        await reject_unauthorized(update)
        return ConversationHandler.END

    users = load_user_configs()
    if not users:
        await update.message.reply_text("没有配置实例。")
        return ConversationHandler.END

    context.user_data["user_configs"] = users
    keyboard = [
        [InlineKeyboardButton(get_user_friendly_name(user_config), callback_data=f"inst_{index}")]
        for index, user_config in enumerate(users)
    ]
    await update.message.reply_text("请选择实例:", reply_markup=InlineKeyboardMarkup(keyboard))
    return SELECTING_INSTANCE


async def instance_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        await reject_unauthorized(update)
        return ConversationHandler.END

    query = update.callback_query
    await query.answer()
    try:
        index = int(query.data.split("_", 1)[1])
    except (IndexError, ValueError):
        await query.edit_message_text("无效的实例选择。")
        return SELECTING_INSTANCE

    users = context.user_data.get("user_configs") or load_user_configs()
    if index < 0 or index >= len(users):
        await query.edit_message_text("实例序号无效。")
        return SELECTING_INSTANCE

    selected = users[index]
    context.user_data["selected_config"] = selected
    await query.edit_message_text(
        f"已选择: *{get_user_friendly_name(selected)}*",
        reply_markup=operation_keyboard(),
        parse_mode="Markdown",
    )
    return SELECTING_OPERATION


async def operation_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        await reject_unauthorized(update)
        return ConversationHandler.END

    query = update.callback_query
    await query.answer()
    callback_data = query.data
    selected = require_selected_config(context)
    if not selected:
        await query.edit_message_text("会话状态已失效，请重新执行 /menu。")
        return ConversationHandler.END

    name = get_user_friendly_name(selected)
    instance_id = selected["instance_id"]
    manager = build_manager(selected)

    if callback_data == "op_start":
        await query.edit_message_text(f"正在启动 {name}...")
        ok, message = manager.start_instance(instance_id)
        await query.edit_message_text(f"{'成功' if ok else '失败'}: {name}\n{message}")
    elif callback_data == "op_stop":
        keyboard = InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("确认关机", callback_data="confirm_stop")],
                [InlineKeyboardButton("取消", callback_data="cancel_operation")],
            ]
        )
        await query.edit_message_text(f"确认停止 {name}？", reply_markup=keyboard)
        return SELECTING_OPERATION
    elif callback_data == "confirm_stop":
        await query.edit_message_text(f"正在停止 {name}...")
        ok, message = manager.stop_instance(instance_id)
        await query.edit_message_text(f"{'成功' if ok else '失败'}: {name}\n{message}")
    elif callback_data == "op_reboot":
        keyboard = InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("确认重启", callback_data="confirm_reboot")],
                [InlineKeyboardButton("取消", callback_data="cancel_operation")],
            ]
        )
        await query.edit_message_text(f"确认重启 {name}？", reply_markup=keyboard)
        return SELECTING_OPERATION
    elif callback_data == "confirm_reboot":
        await query.edit_message_text(f"正在重启 {name}...")
        ok, message = manager.reboot_instance(instance_id)
        await query.edit_message_text(f"{'成功' if ok else '失败'}: {name}\n{message}")
    elif callback_data == "op_status":
        await query.edit_message_text(f"正在查询 `{name}` 状态...", parse_mode="Markdown")
        detail = manager.get_instance_detail(instance_id)
        if not detail:
            await query.edit_message_text(f"查询实例 `{name}` 失败。", parse_mode="Markdown")
        else:
            message = format_status_message(
                selected,
                detail,
                manager.get_current_traffic(),
                manager.get_current_bill(instance_id),
            )
            await query.edit_message_text(message, parse_mode="Markdown")
    elif callback_data == "op_timer_menu":
        keyboard = InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("定时开机", callback_data="timer_start")],
                [InlineKeyboardButton("定时关机", callback_data="timer_stop")],
                [InlineKeyboardButton("删除定时", callback_data="timer_delete")],
                [InlineKeyboardButton("返回", callback_data="back_to_operation")],
            ]
        )
        await query.edit_message_text(f"定时任务 - {name}", reply_markup=keyboard)
        return SELECTING_TIMER_TYPE
    elif callback_data == "back_list":
        users = load_user_configs()
        context.user_data["user_configs"] = users
        keyboard = [
            [InlineKeyboardButton(get_user_friendly_name(user_config), callback_data=f"inst_{index}")]
            for index, user_config in enumerate(users)
        ]
        await query.edit_message_text("请选择实例:", reply_markup=InlineKeyboardMarkup(keyboard))
        return SELECTING_INSTANCE
    elif callback_data == "back_to_operation":
        await query.edit_message_text(f"已选择: *{name}*", reply_markup=operation_keyboard(), parse_mode="Markdown")
        return SELECTING_OPERATION
    elif callback_data == "cancel_operation":
        await query.edit_message_text("已取消。")
    elif callback_data.startswith("del_timer_"):
        await delete_timer(query, context, selected, callback_data)
        return SELECTING_OPERATION

    return ConversationHandler.END


async def timer_type_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        await reject_unauthorized(update)
        return ConversationHandler.END

    query = update.callback_query
    await query.answer()
    callback_data = query.data
    selected = require_selected_config(context)
    if not selected:
        await query.edit_message_text("会话状态已失效，请重新执行 /menu。")
        return ConversationHandler.END

    if callback_data in ("timer_start", "timer_stop"):
        context.user_data["timer_action"] = "start" if callback_data == "timer_start" else "stop"
        await query.edit_message_text("请输入北京时间，格式 HH:MM，例如 08:30。")
        return SET_TIMER_TIME

    if callback_data == "timer_delete":
        timers = load_state().get("timers", {}).get(selected["instance_id"], [])
        if not timers:
            await query.edit_message_text("当前实例没有定时任务。")
            return SELECTING_OPERATION
        keyboard = [
            [
                InlineKeyboardButton(
                    f"{'开机' if timer['action'] == 'start' else '关机'} @ {timer['time']}",
                    callback_data=f"del_timer_{timer['time']}_{timer['action']}",
                )
            ]
            for timer in timers
        ]
        keyboard.append([InlineKeyboardButton("返回", callback_data="back_to_operation")])
        await query.edit_message_text("请选择要删除的定时任务:", reply_markup=InlineKeyboardMarkup(keyboard))
        return SELECTING_OPERATION

    if callback_data == "back_to_operation":
        await query.edit_message_text(
            f"已选择: *{get_user_friendly_name(selected)}*",
            reply_markup=operation_keyboard(),
            parse_mode="Markdown",
        )
        return SELECTING_OPERATION

    return ConversationHandler.END


async def receive_timer_time(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        await reject_unauthorized(update)
        return ConversationHandler.END

    text = update.message.text.strip()
    try:
        hour_text, minute_text = text.split(":", 1)
        hour = int(hour_text)
        minute = int(minute_text)
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError
    except ValueError:
        await update.message.reply_text("时间格式无效，请输入 HH:MM，例如 08:30。")
        return SET_TIMER_TIME

    context.user_data["timer_time"] = f"{hour:02d}:{minute:02d}"
    action_text = "开机" if context.user_data.get("timer_action") == "start" else "关机"
    selected = require_selected_config(context)
    keyboard = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("确认", callback_data="confirm_timer")],
            [InlineKeyboardButton("取消", callback_data="cancel_timer")],
        ]
    )
    await update.message.reply_text(
        f"确认添加定时任务？\n实例: {get_user_friendly_name(selected)}\n操作: {action_text}\n北京时间: {hour:02d}:{minute:02d}",
        reply_markup=keyboard,
    )
    return CONFIRM_TIMER


async def confirm_timer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        await reject_unauthorized(update)
        return ConversationHandler.END

    query = update.callback_query
    await query.answer()
    selected = require_selected_config(context)
    action = context.user_data.get("timer_action")
    time_text = context.user_data.get("timer_time")
    if not selected or action not in ("start", "stop") or not time_text:
        await query.edit_message_text("定时任务状态丢失，请重新设置。")
        return ConversationHandler.END

    state = load_state()
    timers = state.setdefault("timers", {}).setdefault(selected["instance_id"], [])
    if any(timer.get("action") == action and timer.get("time") == time_text for timer in timers):
        await query.edit_message_text("该定时任务已存在。")
        return ConversationHandler.END

    timers.append({"action": action, "time": time_text, "created_at": datetime.now().isoformat()})
    save_state(state)
    register_timer_job(context.application, selected, action, time_text)

    await query.edit_message_text(f"已添加定时{'开机' if action == 'start' else '关机'}: {time_text}")
    return ConversationHandler.END


async def cancel_timer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("已取消。")
    return ConversationHandler.END


async def delete_timer(query, context: ContextTypes.DEFAULT_TYPE, selected: Dict, callback_data: str) -> None:
    parts = callback_data.split("_", 3)
    if len(parts) != 4:
        await query.edit_message_text("删除失败: 定时任务格式无效。")
        return

    time_text = parts[2]
    action = parts[3]
    instance_id = selected["instance_id"]
    state = load_state()
    timers = state.get("timers", {}).get(instance_id, [])
    state.setdefault("timers", {})[instance_id] = [
        timer for timer in timers if not (timer.get("time") == time_text and timer.get("action") == action)
    ]
    save_state(state)

    if context.application.job_queue:
        name = timer_job_name(instance_id, action, time_text)
        for job in context.application.job_queue.jobs():
            if job.name == name:
                job.schedule_removal()
                logger.info("删除定时任务: %s", name)

    await query.edit_message_text(
        f"已删除定时任务: {'开机' if action == 'start' else '关机'} @ {time_text}",
        reply_markup=operation_keyboard(),
    )


async def list_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update):
        await reject_unauthorized(update)
        return
    users = load_user_configs()
    if not users:
        await update.message.reply_text("没有配置实例。")
        return
    lines = ["实例列表:"]
    for index, user_config in enumerate(users, 1):
        paused = "，已暂停" if user_config.get("paused") or user_config.get("disabled") else ""
        lines.append(f"{index}. {get_user_friendly_name(user_config)} ({user_config.get('region')}{paused})")
    await update.message.reply_text("\n".join(lines))


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update):
        await reject_unauthorized(update)
        return
    if not context.args:
        await update.message.reply_text("用法: /status <实例名或ID>")
        return
    selected = find_user_config(" ".join(context.args))
    if not selected:
        await update.message.reply_text("未找到该实例。")
        return
    await send_status_for_config(update, selected)


async def send_status_for_config(update: Update, selected: Dict) -> None:
    name = get_user_friendly_name(selected)
    manager = build_manager(selected)
    await update.message.reply_text(f"正在查询 `{name}` 状态...", parse_mode="Markdown")
    detail = manager.get_instance_detail(selected["instance_id"])
    if not detail:
        await update.message.reply_text(f"查询实例 `{name}` 失败。", parse_mode="Markdown")
        return
    await update.message.reply_text(
        format_status_message(selected, detail, manager.get_current_traffic(), manager.get_current_bill(selected["instance_id"])),
        parse_mode="Markdown",
    )


async def start_instance_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await run_instance_command(update, context, "start")


async def stop_instance_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await run_instance_command(update, context, "stop")


async def reboot_instance_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await run_instance_command(update, context, "reboot")


async def run_instance_command(update: Update, context: ContextTypes.DEFAULT_TYPE, action: str) -> None:
    if not is_authorized(update):
        await reject_unauthorized(update)
        return
    if not context.args:
        command = {"start": "/start_instance", "stop": "/stop", "reboot": "/reboot"}[action]
        await update.message.reply_text(f"用法: {command} <实例名或ID>")
        return

    selected = find_user_config(" ".join(context.args))
    if not selected:
        await update.message.reply_text("未找到该实例。")
        return

    manager = build_manager(selected)
    instance_id = selected["instance_id"]
    name = get_user_friendly_name(selected)
    if action == "start":
        await update.message.reply_text(f"正在启动 {name}...")
        ok, message = manager.start_instance(instance_id)
    elif action in ("stop", "reboot"):
        context.user_data["direct_operation"] = {"action": action, "instance_id": instance_id}
        action_text = "关机" if action == "stop" else "重启"
        keyboard = InlineKeyboardMarkup(
            [
                [InlineKeyboardButton(f"确认{action_text}", callback_data="direct_confirm_operation")],
                [InlineKeyboardButton("取消", callback_data="direct_cancel_operation")],
            ]
        )
        await update.message.reply_text(f"确认对 {name} 执行{action_text}？", reply_markup=keyboard)
        return
    else:
        await update.message.reply_text("不支持的操作。")
        return
    await update.message.reply_text(f"{'成功' if ok else '失败'}: {name}\n{message}")


async def direct_operation_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update):
        await reject_unauthorized(update)
        return

    query = update.callback_query
    await query.answer()
    if query.data == "direct_cancel_operation":
        context.user_data.pop("direct_operation", None)
        await query.edit_message_text("已取消。")
        return

    pending = context.user_data.get("direct_operation")
    if not pending:
        await query.edit_message_text("操作已过期，请重新发送命令。")
        return

    selected = find_user_config(pending.get("instance_id", ""))
    if not selected:
        await query.edit_message_text("未找到该实例。")
        return

    manager = build_manager(selected)
    name = get_user_friendly_name(selected)
    if pending.get("action") == "stop":
        await query.edit_message_text(f"正在停止 {name}...")
        ok, message = manager.stop_instance(selected["instance_id"])
    elif pending.get("action") == "reboot":
        await query.edit_message_text(f"正在重启 {name}...")
        ok, message = manager.reboot_instance(selected["instance_id"])
    else:
        ok, message = False, "无效操作"
    context.user_data.pop("direct_operation", None)
    await query.edit_message_text(f"{'成功' if ok else '失败'}: {name}\n{message}")


async def timers_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update):
        await reject_unauthorized(update)
        return
    state = load_state()
    timers = state.get("timers", {})
    if not timers:
        await update.message.reply_text("当前没有定时任务。")
        return

    user_map = {user_config["instance_id"]: get_user_friendly_name(user_config) for user_config in load_user_configs()}
    lines = ["当前定时任务:"]
    for instance_id, instance_timers in timers.items():
        for timer in instance_timers:
            action_text = "开机" if timer.get("action") == "start" else "关机"
            lines.append(f"- {user_map.get(instance_id, instance_id)}: {action_text} @ {timer.get('time')}")
    await update.message.reply_text("\n".join(lines))


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "/start - 检查机器人\n"
        "/menu - 交互菜单\n"
        "/list - 实例列表\n"
        "/status <实例名或ID> - 查询状态\n"
        "/start_instance <实例名或ID> - 开机\n"
        "/stop <实例名或ID> - 关机\n"
        "/reboot <实例名或ID> - 重启\n"
        "/timers - 查看定时任务"
    )


async def cancel_conversation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("已取消。")
    return ConversationHandler.END


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error("Bot 内部错误: %s", context.error, exc_info=context.error)
    if isinstance(update, Update) and update.effective_message:
        await update.effective_message.reply_text("内部错误，请查看 bot.log。")


async def reload_timers_from_state(application: Application) -> None:
    await asyncio.sleep(1)
    state = load_state()
    timers = state.get("timers", {})
    users = {user_config["instance_id"]: user_config for user_config in load_user_configs() if "instance_id" in user_config}
    for instance_id, instance_timers in timers.items():
        user_config = users.get(instance_id)
        if not user_config:
            logger.warning("实例 %s 不在配置中，跳过恢复定时任务", instance_id)
            continue
        for timer in instance_timers:
            action = timer.get("action")
            time_text = timer.get("time")
            if action in ("start", "stop") and isinstance(time_text, str) and ":" in time_text:
                register_timer_job(application, user_config, action, time_text)


async def set_bot_commands(application: Application) -> None:
    commands = [
        BotCommand("start", "检查机器人"),
        BotCommand("menu", "交互菜单"),
        BotCommand("list", "实例列表"),
        BotCommand("status", "状态查询"),
        BotCommand("start_instance", "启动实例"),
        BotCommand("stop", "停止实例"),
        BotCommand("reboot", "重启实例"),
        BotCommand("timers", "定时任务"),
        BotCommand("help", "帮助"),
    ]
    await application.bot.set_my_commands(commands)


async def post_init(application: Application) -> None:
    await set_bot_commands(application)
    asyncio.create_task(reload_timers_from_state(application))


def main() -> None:
    config = load_config()
    application = Application.builder().token(get_bot_token(config)).post_init(post_init).build()
    conversation = ConversationHandler(
        entry_points=[CommandHandler("menu", menu_command)],
        states={
            SELECTING_INSTANCE: [CallbackQueryHandler(instance_selection, pattern=r"^inst_")],
            SELECTING_OPERATION: [
                CallbackQueryHandler(
                    operation_handler,
                    pattern=r"^(op_|confirm_stop|confirm_reboot|cancel_operation|back_|del_timer_)",
                )
            ],
            SELECTING_TIMER_TYPE: [CallbackQueryHandler(timer_type_handler, pattern=r"^(timer_|back_to_operation)")],
            SET_TIMER_TIME: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_timer_time)],
            CONFIRM_TIMER: [
                CallbackQueryHandler(confirm_timer, pattern=r"^confirm_timer$"),
                CallbackQueryHandler(cancel_timer, pattern=r"^cancel_timer$"),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel_conversation)],
        allow_reentry=True,
    )
    application.add_handler(conversation)
    application.add_handler(CallbackQueryHandler(direct_operation_confirm, pattern=r"^direct_(confirm|cancel)_operation$"))
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("list", list_command))
    application.add_handler(CommandHandler("status", status_command))
    application.add_handler(CommandHandler("start_instance", start_instance_command))
    application.add_handler(CommandHandler("stop", stop_instance_command))
    application.add_handler(CommandHandler("reboot", reboot_instance_command))
    application.add_handler(CommandHandler("timers", timers_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_error_handler(error_handler)

    logger.info("阿里云 ECS Telegram Bot 启动")
    application.run_polling()


if __name__ == "__main__":
    main()

# -*- coding: utf-8 -*-
"""
阿里云 ECS Telegram 机器人
功能：远程管理 ECS 实例，支持开机、关机、重启、状态查询、定时开关机、删除定时任务
"""

import sys
import os
import json
import logging
import time as time_module
import asyncio
import warnings
from logging.handlers import TimedRotatingFileHandler
from datetime import datetime, time as dt_time, timezone, timedelta
from typing import Dict, Any, List, Optional, Tuple

import socket

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ConversationHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from aliyunsdkcore.client import AcsClient
from aliyunsdkcore.acs_exception.exceptions import ClientException, ServerException
from aliyunsdkcore.request import CommonRequest
from aliyunsdkecs.request.v20140526.StartInstanceRequest import StartInstanceRequest
from aliyunsdkecs.request.v20140526.StopInstanceRequest import StopInstanceRequest
from aliyunsdkecs.request.v20140526.RebootInstanceRequest import RebootInstanceRequest
from aliyunsdkecs.request.v20140526.DescribeInstancesRequest import DescribeInstancesRequest
from aliyunsdkbssopenapi.request.v20171214 import DescribeInstanceBillRequest

# 修正 IPv6 和 SNI 问题
try:
    from aliyunsdkcore.vendored.requests.packages.urllib3.util import ssl_
    ssl_.HAS_SNI = True
except Exception:
    pass

_orig_getaddrinfo = socket.getaddrinfo
def _getaddrinfo_ipv4_only(host, port, family=0, type=0, proto=0, flags=0):
    res = _orig_getaddrinfo(host, port, family, type, proto, flags)
    ipv4_res = [r for r in res if r[0] == socket.AF_INET]
    return ipv4_res if ipv4_res else res
socket.getaddrinfo = _getaddrinfo_ipv4_only
warnings.filterwarnings("ignore")

CONFIG_FILE = '/opt/scripts/config.json'
STATE_FILE = '/opt/scripts/bot_state.json'
LOG_FILE = '/opt/scripts/bot.log'

(SELECTING_INSTANCE, SELECTING_OPERATION, SET_TIMER_HOUR,
 SET_TIMER_MINUTE, CONFIRM_TIMER, SELECTING_TIMER_TYPE) = range(6)

TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN', None)

# 时区偏移（中国为 UTC+8）
TIMEZONE_OFFSET_HOURS = 8

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
if not logger.handlers:
    log_dir = os.path.dirname(LOG_FILE)
    if log_dir and not os.path.exists(log_dir):
        os.makedirs(log_dir, exist_ok=True)
    fh = TimedRotatingFileHandler(LOG_FILE, when='D', interval=1, backupCount=7, encoding='utf-8')
    fh.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
    logger.addHandler(fh)
    ch = logging.StreamHandler(sys.stdout)
    ch.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
    logger.addHandler(ch)

# -------------------- 配置 --------------------
def load_config() -> Dict:
    if not os.path.exists(CONFIG_FILE):
        logger.error(f"配置文件不存在: {CONFIG_FILE}")
        sys.exit(1)
    with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
        cfg = json.load(f)
    if not TELEGRAM_BOT_TOKEN and not cfg.get('telegram', {}).get('bot_token'):
        logger.error("Telegram Bot Token 未配置")
        sys.exit(1)
    return cfg

def load_state() -> Dict:
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            logger.warning(f"加载状态失败: {e}")
    return {"timers": {}}

def save_state(state: Dict):
    try:
        with open(STATE_FILE, 'w', encoding='utf-8') as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"保存状态失败: {e}")

# -------------------- 阿里云客户端 --------------------
class AliCloudManager:
    def __init__(self, ak: str, sk: str, region: str):
        self.ak = ak
        self.sk = sk
        self.region = region
        self.client = AcsClient(ak, sk, region)

    def _do_request(self, request, retries=3):
        for attempt in range(1, retries+1):
            try:
                resp = self.client.do_action_with_exception(request)
                return json.loads(resp.decode('utf-8'))
            except (ClientException, ServerException) as e:
                if attempt < retries:
                    time_module.sleep(2*attempt)
                else:
                    logger.error(f"API失败: {e}")
                    return None
        return None

    def get_instance_detail(self, instance_id: str) -> Optional[Dict]:
        req = DescribeInstancesRequest()
        req.set_InstanceIds(json.dumps([instance_id]))
        data = self._do_request(req)
        if not data:
            return None
        instances = data.get("Instances", {}).get("Instance", [])
        if not instances:
            return None
        inst = instances[0]
        pub = inst.get('PublicIpAddress', {}).get('IpAddress', [])
        eip = inst.get('EipAddress', {}).get('IpAddress', "")
        ip = eip if eip else (pub[0] if pub else "无公网IP")
        cpu = inst.get('Cpu', 0)
        mem_mb = inst.get('Memory', 0)
        if mem_mb > 0 and mem_mb % 1024 == 0:
            mem_str = f"{int(mem_mb/1024)}"
        else:
            mem_str = f"{mem_mb/1024:.1f}"
        spec = f"{cpu}C{mem_str}G"
        name = inst.get('InstanceName', instance_id)
        return {
            "status": inst.get('Status', 'Unknown'),
            "ip": ip,
            "spec": spec,
            "name": name,
            "instance_id": instance_id
        }

    def start_instance(self, instance_id: str) -> Tuple[bool, str]:
        try:
            req = StartInstanceRequest()
            req.set_InstanceId(instance_id)
            self.client.do_action_with_exception(req)
            return True, "启动命令已发送"
        except Exception as e:
            return False, f"启动失败: {e}"

    def stop_instance(self, instance_id: str, force=False) -> Tuple[bool, str]:
        try:
            req = StopInstanceRequest()
            req.set_InstanceId(instance_id)
            req.set_ForceStop(force)
            self.client.do_action_with_exception(req)
            return True, "停止命令已发送"
        except Exception as e:
            return False, f"停止失败: {e}"

    def reboot_instance(self, instance_id: str, force=False) -> Tuple[bool, str]:
        try:
            req = RebootInstanceRequest()
            req.set_InstanceId(instance_id)
            req.set_ForceStop(force)
            self.client.do_action_with_exception(req)
            return True, "重启命令已发送"
        except Exception as e:
            return False, f"重启失败: {e}"

    def get_curr_traffic(self, region_hint: str = "cn-hangzhou") -> Optional[float]:
        """获取 CDT 公网流量 (GB)"""
        try:
            cdt_client = AcsClient(self.ak, self.sk, region_hint)
            req_traffic = CommonRequest()
            req_traffic.set_domain('cdt.aliyuncs.com')
            req_traffic.set_version('2021-08-13')
            req_traffic.set_action_name('ListCdtInternetTraffic')
            req_traffic.set_method('POST')
            req_traffic.set_connect_timeout(5000)
            req_traffic.set_read_timeout(15000)
            resp_traffic = cdt_client.do_action_with_exception(req_traffic)
            data_traffic = json.loads(resp_traffic.decode('utf-8'))
            total_bytes = sum(d.get('Traffic', 0) for d in data_traffic.get('TrafficDetails', []))
            return total_bytes / (1024 ** 3)
        except Exception as e:
            logger.warning(f"获取流量数据失败: {e}")
            return None

    def get_curr_bill(self, instance_id: str, billing_cycle: str = None) -> Optional[float]:
        """获取指定实例本月账单金额（人民币）"""
        if billing_cycle is None:
            billing_cycle = datetime.now().strftime("%Y-%m")
        try:
            req = DescribeInstanceBillRequest.DescribeInstanceBillRequest()
            req.set_BillingCycle(billing_cycle)
            req.set_InstanceID(instance_id)
            req.set_ProductCode('ecs')
            resp = self.client.do_action_with_exception(req)
            data = json.loads(resp.decode('utf-8'))
            items = data.get('Data', {}).get('Items', [])
            total = sum(float(item.get('PretaxAmount', 0)) for item in items)
            return total
        except Exception as e:
            logger.warning(f"获取账单失败: {e}")
            return None

# -------------------- 辅助函数 --------------------
def get_bot_token(config):
    return TELEGRAM_BOT_TOKEN or config.get('telegram', {}).get('bot_token', '')

def is_authorized(update: Update) -> bool:
    cfg = load_config()
    admins = cfg.get('admin_users', [])
    return update.effective_user.id in admins

def get_user_friendly_name(u):
    return u.get('name', u.get('instance_id', 'Unknown'))

def load_user_configs():
    return load_config().get('users', [])

def format_full_status(detail: Dict, traffic: Optional[float], bill: Optional[float],
                       quota: float, bill_threshold: float, currency: str, display_name: str = None) -> str:
    """格式化状态消息"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    status_icons = {"Running": "🟢", "Stopped": "⚫", "Starting": "🟡", "Stopping": "🟡"}
    icon = status_icons.get(detail.get('status', 'NotFound'), '❓')
    
    # 使用传入的显示名称，如果没有则使用 API 返回的名称
    name = display_name if display_name else detail.get('name', '未知')
    
    # 流量部分...
    if traffic is not None and traffic >= 0:
        percent = (traffic / quota) * 100 if quota > 0 else 0
        traffic_str = f"{traffic:.2f} GB ({percent:.1f}%)"
    else:
        traffic_str = "⚠️ 查询失败"

    # 账单部分...
    if bill is not None and bill >= 0:
        bill_str = f"{currency}{bill:.2f}"
        if bill > bill_threshold:
            status_icon = "💸 扣费预警"
        else:
            status_icon = "✅"
    else:
        bill_str = "查询失败"
        status_icon = "⚠️"

    msg = (
        f"   📅 时间: {now}\n"
        f"   👤 实例： *{name}* ({detail.get('spec')})\n"   # 使用自定义名称
        f"   🖥️ 状态: {icon} {detail.get('status')}\n"
        f"   🌐 IP: `{detail.get('ip')}`\n"
        f"   📉 流量: {traffic_str}\n"
        f"   💰 账单: {bill_str}\n"
        f"   📝 评价: {status_icon}\n"
    )
    return msg

# -------------------- 定时任务回调 --------------------
async def start_timer_callback(context: ContextTypes.DEFAULT_TYPE):
    data = context.job.data
    inst_id = data['inst_id']
    ak = data['ak']
    sk = data['sk']
    region = data['region']
    name = data.get('name', inst_id)
    logger.info(f"定时任务触发: 启动实例 {inst_id}")
    try:
        manager = AliCloudManager(ak, sk, region)
        success, msg = manager.start_instance(inst_id)
        if success:
            cfg = load_config()
            chat_id = cfg.get('telegram', {}).get('chat_id')
            if chat_id:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=f"⏰ *定时任务执行*\n🖥️ `{name}`\n🟢 启动\n{msg}",
                    parse_mode='Markdown'
                )
        logger.info(f"定时启动 {inst_id}: {success}, {msg}")
    except Exception as e:
        logger.error(f"定时启动失败 {inst_id}: {e}", exc_info=True)

async def stop_timer_callback(context: ContextTypes.DEFAULT_TYPE):
    data = context.job.data
    inst_id = data['inst_id']
    ak = data['ak']
    sk = data['sk']
    region = data['region']
    name = data.get('name', inst_id)
    logger.info(f"定时任务触发: 停止实例 {inst_id}")
    try:
        manager = AliCloudManager(ak, sk, region)
        success, msg = manager.stop_instance(inst_id, force=False)
        if success:
            cfg = load_config()
            chat_id = cfg.get('telegram', {}).get('chat_id')
            if chat_id:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=f"⏰ *定时任务执行*\n🖥️ `{name}`\n🔴 停止\n{msg}",
                    parse_mode='Markdown'
                )
        logger.info(f"定时停止 {inst_id}: {success}, {msg}")
    except Exception as e:
        logger.error(f"定时停止失败 {inst_id}: {e}", exc_info=True)

# -------------------- 命令处理 --------------------
async def start_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        await update.message.reply_text("❌ 无权限")
        return
    await update.message.reply_text("👋 欢迎使用阿里云 ECS Bot，使用 /menu 开始")

async def menu_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        await update.message.reply_text("❌ 无权限")
        return
    users = load_user_configs()
    if not users:
        await update.message.reply_text("❌ 没有配置实例")
        return
    ctx.user_data['user_configs'] = users
    kb = [[InlineKeyboardButton(f"🖥️ {get_user_friendly_name(u)}", callback_data=f"inst_{idx}")] for idx, u in enumerate(users)]
    await update.message.reply_text("📋 选择实例:", reply_markup=InlineKeyboardMarkup(kb))
    return SELECTING_INSTANCE

async def instance_selection(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    if not data.startswith('inst_'):
        return
    idx = int(data.split('_')[1])
    users = ctx.user_data.get('user_configs', [])
    if idx >= len(users):
        await query.edit_message_text("❌ 无效")
        return SELECTING_INSTANCE
    cfg = users[idx]
    ctx.user_data['selected_config'] = cfg
    name = get_user_friendly_name(cfg)
    kb = [
        [InlineKeyboardButton("🟢 开机", callback_data="op_start")],
        [InlineKeyboardButton("🔴 关机", callback_data="op_stop")],
        [InlineKeyboardButton("🔄 重启", callback_data="op_reboot")],
        [InlineKeyboardButton("📊 状态", callback_data="op_status")],
        [InlineKeyboardButton("⏰ 定时", callback_data="op_timer_menu")],
        [InlineKeyboardButton("◀️ 返回", callback_data="back_list")]
    ]
    await query.edit_message_text(f"✅ 已选: *{name}*", reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown')
    return SELECTING_OPERATION

async def operation_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    cb = query.data
    sel = ctx.user_data.get('selected_config')
    if not sel:
        await query.edit_message_text("⚠️ 状态丢失")
        return SELECTING_INSTANCE

    inst_id = sel['instance_id']
    name = get_user_friendly_name(sel)
    mgr = AliCloudManager(sel['ak'], sel['sk'], sel['region'])

    # 删除定时任务
    if cb.startswith("del_timer_"):
        parts = cb.split("_", 3)
        if len(parts) == 4:
            time_str = parts[2]
            action = parts[3]
            state = load_state()
            timers = state.get('timers', {}).get(inst_id, [])
            new_timers = [t for t in timers if not (t['time'] == time_str and t['action'] == action)]
            state['timers'][inst_id] = new_timers
            save_state(state)
            # 从 JobQueue 移除
            job_queue = ctx.application.job_queue
            if job_queue:
                job_name = f"{inst_id}_{action}_{time_str.replace(':', '_')}"
                for job in job_queue.jobs():
                    if job.name == job_name:
                        job.schedule_removal()
                        logger.info(f"删除任务: {job_name}")
            await query.edit_message_text(f"✅ 已删除定时任务 {action} @ {time_str}")
            # 刷新操作菜单
            kb = [
                [InlineKeyboardButton("🟢 开机", callback_data="op_start")],
                [InlineKeyboardButton("🔴 关机", callback_data="op_stop")],
                [InlineKeyboardButton("🔄 重启", callback_data="op_reboot")],
                [InlineKeyboardButton("📊 状态", callback_data="op_status")],
                [InlineKeyboardButton("⏰ 定时", callback_data="op_timer_menu")],
                [InlineKeyboardButton("◀️ 返回", callback_data="back_list")]
            ]
            await query.edit_message_text(f"✅ 已选: *{name}*", reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown')
            return SELECTING_OPERATION
        else:
            await query.edit_message_text("❌ 删除失败，格式错误")
            return SELECTING_OPERATION

    # 操作处理
    if cb == "op_start":
        await query.edit_message_text(f"🟢 启动 {name}...")
        ok, msg = mgr.start_instance(inst_id)
        await query.edit_message_text(f"{'✅' if ok else '❌'} {name}\n{msg}", parse_mode='Markdown')
    elif cb == "op_stop":
        kb = [[InlineKeyboardButton("✅ 确认", callback_data="confirm_stop")], [InlineKeyboardButton("❌ 取消", callback_data="cancel_operation")]]
        await query.edit_message_text(f"⚠️ 确认停止 {name}？", reply_markup=InlineKeyboardMarkup(kb))
        ctx.user_data['pending_stop'] = (inst_id, name, mgr)
        return SELECTING_OPERATION
    elif cb == "confirm_stop":
        inst_id, name, mgr = ctx.user_data.get('pending_stop', (None, None, None))
        if not inst_id:
            await query.edit_message_text("超时")
            return SELECTING_INSTANCE
        await query.edit_message_text(f"🔴 停止 {name}...")
        ok, msg = mgr.stop_instance(inst_id)
        await query.edit_message_text(f"{'✅' if ok else '❌'} {name}\n{msg}", parse_mode='Markdown')
        ctx.user_data.pop('pending_stop', None)
    elif cb == "op_reboot":
        await query.edit_message_text(f"🔄 重启 {name}...")
        ok, msg = mgr.reboot_instance(inst_id)
        await query.edit_message_text(f"{'✅' if ok else '❌'} {name}\n{msg}", parse_mode='Markdown')
    elif cb == "op_status":
        await query.edit_message_text(f"📊 正在查询 `{name}` 状态...", parse_mode='Markdown')
        detail = mgr.get_instance_detail(inst_id)
        traffic = mgr.get_curr_traffic()
        bill = mgr.get_curr_bill(inst_id)
        if detail:
            quota = sel.get('traffic_limit', 180)
            bill_threshold = sel.get('bill_threshold', 1.0)
            currency = sel.get('currency', '¥')
            msg = format_full_status(detail, traffic, bill, quota, bill_threshold, currency, display_name=name)
            await query.edit_message_text(msg, parse_mode='Markdown')
            logger.info(f"用户 {update.effective_user.id} 查询实例 {inst_id} 状态")
        else:
            await query.edit_message_text(f"❌ 查询实例 `{name}` 失败", parse_mode='Markdown')
    elif cb == "op_timer_menu":
        kb = [
            [InlineKeyboardButton("🟢 定时开机", callback_data="timer_start")],
            [InlineKeyboardButton("🔴 定时关机", callback_data="timer_stop")],
            [InlineKeyboardButton("🗑️ 删除定时", callback_data="timer_delete")],
            [InlineKeyboardButton("◀️ 返回", callback_data="back_to_operation")]
        ]
        await query.edit_message_text(f"⏰ 定时任务 - {name}", reply_markup=InlineKeyboardMarkup(kb))
        return SELECTING_TIMER_TYPE
    elif cb == "cancel_operation":
        await query.edit_message_text("✅ 取消")
    elif cb == "back_list":
        users = load_user_configs()
        ctx.user_data['user_configs'] = users
        kb = [[InlineKeyboardButton(f"🖥️ {get_user_friendly_name(u)}", callback_data=f"inst_{idx}")] for idx, u in enumerate(users)]
        await query.edit_message_text("📋 实例列表", reply_markup=InlineKeyboardMarkup(kb))
        return SELECTING_INSTANCE
    elif cb == "back_to_operation":
        name = get_user_friendly_name(sel)
        kb = [
            [InlineKeyboardButton("🟢 开机", callback_data="op_start")],
            [InlineKeyboardButton("🔴 关机", callback_data="op_stop")],
            [InlineKeyboardButton("🔄 重启", callback_data="op_reboot")],
            [InlineKeyboardButton("📊 状态", callback_data="op_status")],
            [InlineKeyboardButton("⏰ 定时", callback_data="op_timer_menu")],
            [InlineKeyboardButton("◀️ 返回", callback_data="back_list")]
        ]
        await query.edit_message_text(f"✅ {name}", reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown')
        return SELECTING_OPERATION
    return SELECTING_INSTANCE

async def timer_type_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    cb = query.data
    sel = ctx.user_data.get('selected_config')
    if not sel:
        await query.edit_message_text("错误")
        return ConversationHandler.END
    if cb == "timer_start":
        ctx.user_data['timer_action'] = "start"
        await query.edit_message_text("🟢 请输入时间 (HH:MM，24h)")
        return SET_TIMER_HOUR
    elif cb == "timer_stop":
        ctx.user_data['timer_action'] = "stop"
        await query.edit_message_text("🔴 请输入时间 (HH:MM)")
        return SET_TIMER_HOUR
    elif cb == "timer_delete":
        state = load_state()
        inst_id = sel['instance_id']
        timers = state.get('timers', {}).get(inst_id, [])
        if not timers:
            await query.edit_message_text("⏰ 当前没有定时任务")
            return SELECTING_OPERATION
        keyboard = []
        for t in timers:
            action_icon = "🟢开机" if t['action'] == 'start' else "🔴关机"
            keyboard.append([InlineKeyboardButton(
                f"{action_icon} @ {t['time']}",
                callback_data=f"del_timer_{t['time']}_{t['action']}"
            )])
        keyboard.append([InlineKeyboardButton("◀️ 返回", callback_data="back_to_operation")])
        await query.edit_message_text("🗑️ 选择要删除的任务", reply_markup=InlineKeyboardMarkup(keyboard))
        return SELECTING_OPERATION
    return ConversationHandler.END

async def receive_time(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if ':' not in text:
        await update.message.reply_text("格式 HH:MM")
        return SET_TIMER_HOUR
    parts = text.split(':')
    try:
        h = int(parts[0]); m = int(parts[1])
        if not (0 <= h <= 23 and 0 <= m <= 59):
            raise ValueError
    except:
        await update.message.reply_text("无效时间")
        return SET_TIMER_HOUR
    ctx.user_data['timer_hour'] = h
    ctx.user_data['timer_minute'] = m
    act = ctx.user_data.get('timer_action')
    act_text = "开机" if act == "start" else "关机"
    sel = ctx.user_data.get('selected_config')
    name = get_user_friendly_name(sel) if sel else "实例"
    kb = [[InlineKeyboardButton("✅确认", callback_data="confirm_timer")], [InlineKeyboardButton("❌取消", callback_data="cancel_timer")]]
    await update.message.reply_text(
        f"📋 确认\n实例: {name}\n操作: {act_text}\n时间: {h:02d}:{m:02d}\n确认添加？",
        reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown'
    )
    return CONFIRM_TIMER

async def confirm_timer(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    sel = ctx.user_data.get('selected_config')
    if not sel:
        await query.edit_message_text("状态丢失")
        return ConversationHandler.END
    inst_id = sel['instance_id']
    name = get_user_friendly_name(sel)
    action = ctx.user_data.get('timer_action')
    hour = ctx.user_data.get('timer_hour')
    minute = ctx.user_data.get('timer_minute')
    if None in (hour, minute):
        await query.edit_message_text("时间丢失")
        return ConversationHandler.END
    time_str = f"{hour:02d}:{minute:02d}"
    state = load_state()
    if inst_id not in state.get('timers', {}):
        state.setdefault('timers', {})[inst_id] = []
    for t in state['timers'][inst_id]:
        if t['action'] == action and t['time'] == time_str:
            await query.edit_message_text("该定时任务已存在")
            return ConversationHandler.END
    state['timers'][inst_id].append({
        "action": action,
        "time": time_str,
        "created_at": datetime.now().isoformat()
    })
    save_state(state)

    job_queue = ctx.application.job_queue
    if job_queue:
        # 北京时间转 UTC
        utc_hour = (hour - TIMEZONE_OFFSET_HOURS) % 24
        target_time = dt_time(hour=utc_hour, minute=minute)
        job_name = f"{inst_id}_{action}_{time_str.replace(':', '_')}"
        if action == "start":
            job_queue.run_daily(
                start_timer_callback,
                time=target_time,
                days=tuple(range(7)),
                data={
                    "inst_id": inst_id,
                    "ak": sel['ak'],
                    "sk": sel['sk'],
                    "region": sel['region'],
                    "name": name
                },
                name=job_name
            )
        else:
            job_queue.run_daily(
                stop_timer_callback,
                time=target_time,
                days=tuple(range(7)),
                data={
                    "inst_id": inst_id,
                    "ak": sel['ak'],
                    "sk": sel['sk'],
                    "region": sel['region'],
                    "name": name
                },
                name=job_name
            )
        logger.info(f"添加定时任务: {name} {action} 每天 {time_str} (UTC {utc_hour:02d}:{minute:02d})")
    await query.edit_message_text(f"✅ 已添加定时{('开机' if action=='start' else '关机')} {time_str}")
    return ConversationHandler.END

async def cancel_timer(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("已取消")
    return ConversationHandler.END

async def list_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        return
    users = load_user_configs()
    if not users:
        await update.message.reply_text("无实例")
        return
    lines = ["📋 实例列表:"]
    for idx, u in enumerate(users, 1):
        lines.append(f"{idx}. {u.get('name', u.get('instance_id'))} ({u.get('region')})")
    await update.message.reply_text("\n".join(lines))

async def status_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        return
    args = ctx.args
    if not args:
        await update.message.reply_text("用法: /status <实例名或ID>")
        return
    identifier = ' '.join(args)
    users = load_user_configs()
    target = None
    for u in users:
        if u.get('name') == identifier or u.get('instance_id') == identifier:
            target = u
            break
    if not target:
        await update.message.reply_text(f"未找到: {identifier}")
        return
    name = get_user_friendly_name(target)   # 这是自定义名称
    mgr = AliCloudManager(target['ak'], target['sk'], target['region'])
    await update.message.reply_text(f"📊 正在查询 `{name}` 状态...", parse_mode='Markdown')
    detail = mgr.get_instance_detail(target['instance_id'])
    traffic = mgr.get_curr_traffic()
    bill = mgr.get_curr_bill(target['instance_id'])
    if detail:
        quota = target.get('traffic_limit', 180)
        bill_threshold = target.get('bill_threshold', 1.0)
        currency = target.get('currency', '¥')
        # 传入自定义名称
        msg = format_full_status(detail, traffic, bill, quota, bill_threshold, currency, display_name=name)
        await update.message.reply_text(msg, parse_mode='Markdown')
        logger.info(f"用户 {update.effective_user.id} 查询实例 {target['instance_id']} 状态")
    else:
        await update.message.reply_text(f"❌ 查询实例 `{name}` 失败", parse_mode='Markdown')

async def start_instance_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        return
    args = ctx.args
    if not args:
        await update.message.reply_text("用法: /start_instance <实例名>")
        return
    identifier = ' '.join(args)
    users = load_user_configs()
    target = None
    for u in users:
        if u.get('name') == identifier or u.get('instance_id') == identifier:
            target = u
            break
    if not target:
        await update.message.reply_text(f"未找到: {identifier}")
        return
    name = get_user_friendly_name(target)
    mgr = AliCloudManager(target['ak'], target['sk'], target['region'])
    await update.message.reply_text(f"🟢 启动 {name}...")
    ok, msg = mgr.start_instance(target['instance_id'])
    await update.message.reply_text(f"{'✅' if ok else '❌'} {name}\n{msg}", parse_mode='Markdown')

async def stop_instance_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        return
    args = ctx.args
    if not args:
        await update.message.reply_text("用法: /stop <实例名>")
        return
    identifier = ' '.join(args)
    users = load_user_configs()
    target = None
    for u in users:
        if u.get('name') == identifier or u.get('instance_id') == identifier:
            target = u
            break
    if not target:
        await update.message.reply_text(f"未找到: {identifier}")
        return
    name = get_user_friendly_name(target)
    mgr = AliCloudManager(target['ak'], target['sk'], target['region'])
    await update.message.reply_text(f"🔴 停止 {name}...")
    ok, msg = mgr.stop_instance(target['instance_id'])
    await update.message.reply_text(f"{'✅' if ok else '❌'} {name}\n{msg}", parse_mode='Markdown')

async def reboot_instance_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        return
    args = ctx.args
    if not args:
        await update.message.reply_text("用法: /reboot <实例名>")
        return
    identifier = ' '.join(args)
    users = load_user_configs()
    target = None
    for u in users:
        if u.get('name') == identifier or u.get('instance_id') == identifier:
            target = u
            break
    if not target:
        await update.message.reply_text(f"未找到: {identifier}")
        return
    name = get_user_friendly_name(target)
    mgr = AliCloudManager(target['ak'], target['sk'], target['region'])
    await update.message.reply_text(f"🔄 重启 {name}...")
    ok, msg = mgr.reboot_instance(target['instance_id'])
    await update.message.reply_text(f"{'✅' if ok else '❌'} {name}\n{msg}", parse_mode='Markdown')

async def timers_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        return
    state = load_state()
    timers = state.get('timers', {})
    if not timers:
        await update.message.reply_text("⏰ 当前没有定时任务")
        return
    lines = ["⏰ *当前定时任务:*\n"]
    user_configs = load_user_configs()
    name_map = {cfg['instance_id']: cfg.get('name', cfg['instance_id']) for cfg in user_configs}
    for inst_id, lst in timers.items():
        name = name_map.get(inst_id, inst_id)
        for t in lst:
            act = "开机" if t['action'] == 'start' else "关机"
            lines.append(f"• `{name}` - {act} @ {t['time']}")
    await update.message.reply_text("\n".join(lines), parse_mode='Markdown')

async def help_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("/start, /menu, /list, /status, /start_instance, /stop, /reboot, /timers")

async def cancel_conversation(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("取消")
    return ConversationHandler.END

async def error_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Bot错误: {ctx.error}", exc_info=ctx.error)
    if update and update.effective_message:
        await update.effective_message.reply_text("内部错误")

def set_bot_commands(app: Application):
    cmds = [
        BotCommand("start", "欢迎"),
        BotCommand("menu", "交互菜单"),
        BotCommand("list", "实例列表"),
        BotCommand("status", "状态查询"),
        BotCommand("start_instance", "启动"),
        BotCommand("stop", "停止"),
        BotCommand("reboot", "重启"),
        BotCommand("timers", "定时任务"),
        BotCommand("help", "帮助"),
    ]
    app.bot.set_my_commands(cmds)

# ---------- 恢复定时任务 ----------
async def reload_timers_from_state(app: Application):
    await asyncio.sleep(2)
    state = load_state()
    timers = state.get('timers', {})
    config = load_config()
    users_map = {u['instance_id']: u for u in config.get('users', []) if 'instance_id' in u}
    for inst_id, timer_list in timers.items():
        user_cfg = users_map.get(inst_id)
        if not user_cfg:
            logger.warning(f"实例 {inst_id} 不在配置中，跳过恢复")
            continue
        for t in timer_list:
            action = t['action']
            time_str = t['time']
            if ':' not in time_str:
                continue
            hour, minute = map(int, time_str.split(':'))
            utc_hour = (hour - TIMEZONE_OFFSET_HOURS) % 24
            target_time = dt_time(hour=utc_hour, minute=minute)
            job_name = f"{inst_id}_{action}_{time_str.replace(':', '_')}"
            if action == "start":
                app.job_queue.run_daily(
                    start_timer_callback,
                    time=target_time,
                    days=tuple(range(7)),
                    data={
                        "inst_id": inst_id,
                        "ak": user_cfg['ak'],
                        "sk": user_cfg['sk'],
                        "region": user_cfg['region'],
                        "name": user_cfg.get('name', inst_id)
                    },
                    name=job_name
                )
            elif action == "stop":
                app.job_queue.run_daily(
                    stop_timer_callback,
                    time=target_time,
                    days=tuple(range(7)),
                    data={
                        "inst_id": inst_id,
                        "ak": user_cfg['ak'],
                        "sk": user_cfg['sk'],
                        "region": user_cfg['region'],
                        "name": user_cfg.get('name', inst_id)
                    },
                    name=job_name
                )
            logger.info(f"恢复定时任务: {inst_id} {action} 每天 {time_str} (UTC {utc_hour:02d}:{minute:02d})")

# -------------------- 主函数 --------------------
def main():
    config = load_config()
    token = get_bot_token(config)
    if not token:
        sys.exit(1)
    app = Application.builder().token(token).build()
    conv = ConversationHandler(
        entry_points=[CommandHandler('menu', menu_command)],
        states={
            SELECTING_INSTANCE: [CallbackQueryHandler(instance_selection, pattern='^inst_')],
            SELECTING_OPERATION: [CallbackQueryHandler(operation_handler, pattern='^(op_|confirm_stop|cancel_operation|back_|del_timer_)')],
            SELECTING_TIMER_TYPE: [CallbackQueryHandler(timer_type_handler, pattern='^timer_')],
            SET_TIMER_HOUR: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_time)],
            CONFIRM_TIMER: [CallbackQueryHandler(confirm_timer, pattern='^confirm_timer$'), CallbackQueryHandler(cancel_timer, pattern='^cancel_timer$')],
        },
        fallbacks=[CommandHandler('cancel', cancel_conversation)],
        allow_reentry=True
    )
    app.add_handler(conv)
    app.add_handler(CommandHandler('start', start_command))
    app.add_handler(CommandHandler('list', list_command))
    app.add_handler(CommandHandler('status', status_command))
    app.add_handler(CommandHandler('start_instance', start_instance_command))
    app.add_handler(CommandHandler('stop', stop_instance_command))
    app.add_handler(CommandHandler('reboot', reboot_instance_command))
    app.add_handler(CommandHandler('timers', timers_command))
    app.add_handler(CommandHandler('help', help_command))
    app.add_error_handler(error_handler)
    set_bot_commands(app)

    loop = asyncio.get_event_loop()
    loop.create_task(reload_timers_from_state(app))

    logger.info("Bot 启动成功")
    app.run_polling()

if __name__ == "__main__":
    main()
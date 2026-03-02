# -*- coding: utf-8 -*-
import json
import sys
import logging
import os
import time
import requests
from logging.handlers import TimedRotatingFileHandler
from aliyunsdkcore.client import AcsClient
from aliyunsdkcore.request import CommonRequest
from aliyunsdkecs.request.v20140526.StartInstanceRequest import StartInstanceRequest
from aliyunsdkecs.request.v20140526.StopInstanceRequest import StopInstanceRequest
from aliyunsdkecs.request.v20140526.DescribeInstancesRequest import DescribeInstancesRequest

# 配置文件路径
CONFIG_FILE = '/opt/scripts/config.json'
LOG_FILE    = '/opt/scripts/monitor.log'
# 状态缓存文件：记录每个实例上次发送通知的时间戳 / 启动失败次数
STATE_FILE  = '/opt/scripts/monitor_state.json'

# 通用事件通知冷却时间（秒）：1 小时内不重复发送
NOTIFY_COOLDOWN = 3600
# 流量超标提醒冷却时间（秒）：24 小时只提醒一次
OVERLIMIT_COOLDOWN = 86400
# 等待实例启动：轮询超时 / 间隔（秒）
START_WAIT_TIMEOUT  = 180
START_POLL_INTERVAL = 10
# 连续启动失败超过此次数后，降低重试频率（每 30 分钟重试一次，而非每 5 分钟）
MAX_START_FAILURES = 3
# 资源不足时的重试冷却时间（秒）：30 分钟重试一次，而不是彻底放弃
RESOURCE_RETRY_COOLDOWN = 1800

# 初始化日志
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
if not logger.handlers:
    handler = TimedRotatingFileHandler(LOG_FILE, when='D', interval=1, backupCount=7, encoding='utf-8')
    handler.setFormatter(logging.Formatter("%(asctime)s - %(message)s"))
    logger.addHandler(handler)

# ---------- 配置加载 ----------

def load_config():
    if not os.path.exists(CONFIG_FILE):
        logger.error("配置文件 config.json 不存在")
        sys.exit(1)
    with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
        return json.load(f)

# ---------- 状态缓存（防抖 / 失败计数） ----------

def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            pass
    return {}

def save_state(state):
    try:
        with open(STATE_FILE, 'w', encoding='utf-8') as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"保存状态文件失败: {e}")

def can_notify(state, instance_id, event_key, cooldown=None):
    """判断某事件是否已过冷却期，可以再次发送通知"""
    if cooldown is None:
        cooldown = NOTIFY_COOLDOWN
    last_ts = state.get(instance_id, {}).get(event_key, 0)
    return (time.time() - last_ts) >= cooldown

def mark_notified(state, instance_id, event_key):
    state.setdefault(instance_id, {})[event_key] = time.time()

def get_start_failures(state, instance_id):
    return state.get(instance_id, {}).get('start_failures', 0)

def set_start_failures(state, instance_id, count):
    state.setdefault(instance_id, {})['start_failures'] = count

def reset_start_failures(state, instance_id):
    state.setdefault(instance_id, {})['start_failures'] = 0

# ---------- TG 通知 ----------

def send_tg_alert(tg_conf, title, message, color_status):
    if not tg_conf.get('bot_token') or not tg_conf.get('chat_id'):
        return
    icon = "\u2705" if color_status == "green" else "\U0001f6a8"
    try:
        url = f"https://api.telegram.org/bot{tg_conf['bot_token']}/sendMessage"
        text = f"{icon} *[{title}]*\n\n{message}"
        data = {"chat_id": tg_conf['chat_id'], "text": text, "parse_mode": "Markdown"}
        requests.post(url, json=data, timeout=5)
    except Exception as e:
        logger.error(f"TG发送失败: {e}")

# ---------- 查询实例状态 ----------

def get_instance_status(client, instance_id):
    req_ecs = DescribeInstancesRequest()
    req_ecs.set_InstanceIds(json.dumps([instance_id]))
    resp_ecs = client.do_action_with_exception(req_ecs)
    data_ecs = json.loads(resp_ecs.decode('utf-8'))
    instances = data_ecs.get("Instances", {}).get("Instance", [])
    if not instances:
        return None
    return instances[0].get("Status")

# ---------- 核心逻辑 ----------

def check_and_act(user, tg_conf, state):
    instance_id = user['instance_id']
    name        = user['name']
    try:
        client = AcsClient(user['ak'], user['sk'], user['region'])

        # 1. 获取流量
        req_traffic = CommonRequest()
        req_traffic.set_domain('cdt.aliyuncs.com')
        req_traffic.set_version('2021-08-13')
        req_traffic.set_action_name('ListCdtInternetTraffic')
        req_traffic.set_method('POST')
        resp_traffic = client.do_action_with_exception(req_traffic)
        data_traffic = json.loads(resp_traffic.decode('utf-8'))
        total_bytes = sum(d.get('Traffic', 0) for d in data_traffic.get('TrafficDetails', []))
        curr_gb = total_bytes / (1024 ** 3)

        # 2. 获取实例当前状态
        status = get_instance_status(client, instance_id)
        if status is None:
            logger.error(f"[{name}] 未找到实例: {instance_id}")
            return

        # 3. 决策
        limit = user.get('traffic_limit', 180)

        if curr_gb < limit:
            # ---- 流量安全 ----
            if status == "Stopped":
                failures = get_start_failures(state, instance_id)

                # 即使多次失败也不放弃，只是降低重试频率
                if failures >= MAX_START_FAILURES:
                    last_retry = state.get(instance_id, {}).get('last_retry_ts', 0)
                    elapsed = time.time() - last_retry
                    if elapsed < RESOURCE_RETRY_COOLDOWN:
                        remaining = int(RESOURCE_RETRY_COOLDOWN - elapsed)
                        logger.info(f"[{name}] 已连续 {failures} 次启动失败，"
                                    f"距下次重试还需 {remaining}s，本轮跳过")
                        return
                    # 超过冷却时间，继续重试
                    logger.info(f"[{name}] 已连续 {failures} 次启动失败，"
                                f"冷却期已过，再次尝试启动...")

                # 记录本次重试时间
                state.setdefault(instance_id, {})['last_retry_ts'] = time.time()

                logger.info(f"[{name}] 流量安全({curr_gb:.2f}GB)，尝试启动实例...")

                # --- 调用启动 API（单独 try-except 防止异常跳过计数） ---
                try:
                    start_req = StartInstanceRequest()
                    start_req.set_InstanceId(instance_id)
                    client.do_action_with_exception(start_req)
                    logger.info(f"[{name}] StartInstance API 调用成功，等待实例进入 Running...")
                except Exception as api_err:
                    err_msg = str(api_err)
                    new_failures = failures + 1
                    set_start_failures(state, instance_id, new_failures)
                    logger.warning(f"[{name}] StartInstance API 调用失败: {err_msg}，"
                                   f"累计失败 {new_failures} 次")
                    if can_notify(state, instance_id, 'start_failed'):
                        msg = (f"机器: {name}\n当前流量: {curr_gb:.2f}GB\n"
                               f"⚠️ 启动 API 调用失败: {err_msg}\n"
                               f"累计失败 {new_failures} 次，"
                               f"脚本将每 {RESOURCE_RETRY_COOLDOWN//60} 分钟自动重试。")
                        send_tg_alert(tg_conf, "启动失败告警", msg, "red")
                        mark_notified(state, instance_id, 'start_failed')
                    return

                # --- 轮询等待实例真正进入 Running 状态 ---
                started = False
                waited  = 0
                while waited < START_WAIT_TIMEOUT:
                    time.sleep(START_POLL_INTERVAL)
                    waited += START_POLL_INTERVAL
                    try:
                        real_status = get_instance_status(client, instance_id)
                    except Exception:
                        real_status = "Unknown"
                    logger.info(f"[{name}] 等待启动... 当前状态: {real_status} ({waited}s)")
                    if real_status == "Running":
                        started = True
                        break
                    elif real_status == "Stopped":
                        # 已经回落到 Stopped，说明启动被拒绝（资源不足等）
                        logger.warning(f"[{name}] 实例已回落到 Stopped 状态，启动被拒绝")
                        break

                if started:
                    # 启动成功，重置失败计数
                    reset_start_failures(state, instance_id)
                    state.setdefault(instance_id, {}).pop('no_resource', None)
                    state.setdefault(instance_id, {}).pop('last_retry_ts', None)
                    logger.info(f"[{name}] 实例已恢复运行 ✅")
                    if can_notify(state, instance_id, 'resumed'):
                        msg = f"机器: {name}\n当前流量: {curr_gb:.2f}GB\n动作: 恢复运行 ✅"
                        send_tg_alert(tg_conf, "恢复监控", msg, "green")
                        mark_notified(state, instance_id, 'resumed')
                else:
                    # 超时未启动或回落到 Stopped，计为一次失败
                    new_failures = failures + 1
                    set_start_failures(state, instance_id, new_failures)
                    logger.warning(f"[{name}] 启动超时或被拒绝，累计失败 {new_failures} 次")
                    if can_notify(state, instance_id, 'start_failed'):
                        msg = (f"机器: {name}\n当前流量: {curr_gb:.2f}GB\n"
                               f"⚠️ 尝试启动但 {START_WAIT_TIMEOUT}s 内未变为 Running 状态，"
                               f"累计失败 {new_failures} 次。\n"
                               f"脚本将每 {RESOURCE_RETRY_COOLDOWN//60} 分钟自动重试，无需手动干预。")
                        send_tg_alert(tg_conf, "启动失败告警", msg, "red")
                        mark_notified(state, instance_id, 'start_failed')

            elif status == "Running":
                # 正常运行，重置计数
                reset_start_failures(state, instance_id)
                logger.info(f"[{name}] 流量安全({curr_gb:.2f}GB)，实例运行中")
            else:
                # Starting / Stopping 等中间态，不干预
                logger.info(f"[{name}] 实例处于中间态: {status}，不干预")

        else:
            # ---- 流量超标 ----
            if status == "Running":
                logger.info(f"[{name}] 流量超标({curr_gb:.2f}GB >= {limit}GB)，正在停止...")
                stop_req = StopInstanceRequest()
                stop_req.set_InstanceId(instance_id)
                client.do_action_with_exception(stop_req)
                if can_notify(state, instance_id, 'overlimit', OVERLIMIT_COOLDOWN):
                    msg = f"机器: {name}\n当前流量: {curr_gb:.2f}GB\n动作: 已触发止损关机 \U0001f6d1"
                    send_tg_alert(tg_conf, "流量预警", msg, "red")
                    mark_notified(state, instance_id, 'overlimit')
            else:
                # 已处于停止状态，每天提醒一次
                logger.info(f"[{name}] 已停止止损 - {curr_gb:.2f}GB")
                if can_notify(state, instance_id, 'overlimit', OVERLIMIT_COOLDOWN):
                    msg = f"机器: {name}\n当前流量: {curr_gb:.2f}GB\n状态: 流量超标，已保持关机 \U0001f6d1"
                    send_tg_alert(tg_conf, "流量超标提醒", msg, "red")
                    mark_notified(state, instance_id, 'overlimit')

    except Exception as e:
        logger.error(f"[{name}] 检查出错: {e}")

def main():
    config = load_config()
    state  = load_state()
    for user in config.get('users', []):
        check_and_act(user, config.get('telegram', {}), state)
    save_state(state)

if __name__ == "__main__":
    main()

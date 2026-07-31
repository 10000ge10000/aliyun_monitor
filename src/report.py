# -*- coding: utf-8 -*-
import sys
import warnings
import os
import json
import datetime
import requests
import logging
from logging.handlers import TimedRotatingFileHandler

# 修正 urllib3 在 Python 3.12 下引发的 SNI 丢失问题
try:
    from aliyunsdkcore.vendored.requests.packages.urllib3.util import ssl_
    ssl_.HAS_SNI = True
except Exception:
    pass

import socket
# 强制使用 IPv4 避免 IPv6 黑洞
_orig_getaddrinfo = socket.getaddrinfo
def _getaddrinfo_ipv4_only(host, port, family=0, type=0, proto=0, flags=0):
    res = _orig_getaddrinfo(host, port, family, type, proto, flags)
    ipv4_res = [r for r in res if r[0] == socket.AF_INET]
    return ipv4_res if ipv4_res else res
socket.getaddrinfo = _getaddrinfo_ipv4_only

warnings.filterwarnings("ignore")

try:
    from aliyunsdkcore.client import AcsClient
    from aliyunsdkcore.request import CommonRequest
except ImportError:
    sys.exit(1)

CONFIG_FILE = '/opt/scripts/config.json'
LOG_FILE = '/opt/scripts/report.log'

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
if not logger.handlers:
    handler = TimedRotatingFileHandler(LOG_FILE, when='D', interval=1, backupCount=7, encoding='utf-8')
    handler.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
    logger.addHandler(handler)
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
    logger.addHandler(console_handler)

def load_config():
    if not os.path.exists(CONFIG_FILE):
        logger.error("配置文件不存在: %s", CONFIG_FILE)
        sys.exit(1)
    with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
        return json.load(f)

def sanitize_markdown(text):
    """将 legacy Markdown 特殊字符替换为空格，避免备注名/错误信息中的特殊字符导致整条日报发送失败"""
    text = str(text)
    for ch in ('_', '*', '`', '['):
        text = text.replace(ch, ' ')
    return text.strip()

# Telegram 单条消息上限 4096 字符，留出余量按行分片
TG_MESSAGE_LIMIT = 4000

def split_message(message, limit=TG_MESSAGE_LIMIT):
    """按行边界将超长消息切分为多段，保证每段不超过 Telegram 上限"""
    chunks = []
    current = ""
    for line in message.split("\n"):
        # 单行本身也可能超过限制（例如异常堆栈或超长实例名），需要硬切分。
        while len(line) > limit:
            if current:
                chunks.append(current)
                current = ""
            chunks.append(line[:limit])
            line = line[limit:]
        candidate = line if not current else current + "\n" + line
        if len(candidate) > limit and current:
            chunks.append(current)
            current = line
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks

def send_tg_report(tg_conf, message):
    if not tg_conf.get('bot_token') or not tg_conf.get('chat_id'):
        logger.warning("Telegram 配置不完整，跳过日报发送")
        return
    url = f"https://api.telegram.org/bot{tg_conf['bot_token']}/sendMessage"
    chunks = split_message(message)
    for index, chunk in enumerate(chunks, 1):
        payloads = [
            {"chat_id": tg_conf['chat_id'], "text": chunk, "parse_mode": "Markdown"},
            # Markdown 解析失败或网络抖动时，退化为纯文本再试一次，保证日报必达
            {"chat_id": tg_conf['chat_id'], "text": chunk},
        ]
        sent = False
        for data in payloads:
            try:
                response = requests.post(url, json=data, timeout=10)
                if response.status_code == 200:
                    sent = True
                    break
                logger.error("Telegram 日报发送失败: HTTP %s, %s", response.status_code, response.text)
            except Exception as e:
                logger.exception("Telegram 日报发送异常: %s", e)
        if sent:
            logger.info("Telegram 日报发送成功 (%s/%s)", index, len(chunks))

def do_common_request(client, domain, version, action, params=None, method='POST', timeout=30, retries=3):
    for attempt in range(1, retries + 1):
        try:
            request = CommonRequest()
            request.set_domain(domain)
            request.set_version(version)
            request.set_action_name(action)
            request.set_method(method)
            request.set_protocol_type('https')
            request.set_connect_timeout(5000)   # 连接 5 秒内必须成功，避免黑洞 IP 卡死
            request.set_read_timeout(15000)      # 读取 15 秒
            if params:
                for k, v in params.items():
                    request.add_query_param(k, v)
            response = client.do_action_with_exception(request)
            return json.loads(response.decode('utf-8'))
        except Exception as e:
            logger.warning("请求 %s 失败 (尝试 %s/%s): %s", action, attempt, retries, e)
            if attempt < retries:
                import time
                time.sleep(2 * attempt)
                continue
            logger.error("请求 %s 最终失败，已重试 %s 次", action, retries)
            return None

BALANCE_ENDPOINTS = ('business.aliyuncs.com', 'business.ap-southeast-1.aliyuncs.com')

def currency_symbol(code, default='$'):
    return {'CNY': '¥', 'USD': '$'}.get(code, default)

def get_account_balance(client, bill_endpoint):
    """查询账户可用余额 (QueryAccountBalance)。返回 (金额, 货币代码)；查询失败返回 (None, None)。"""
    # 优先使用该账号配置的账单节点，失败后尝试另一节点，兼容国内/国际站配置错误的情况
    endpoints = [bill_endpoint] + [ep for ep in BALANCE_ENDPOINTS if ep != bill_endpoint]
    for endpoint in endpoints:
        data = do_common_request(client, endpoint, '2017-12-14', 'QueryAccountBalance', retries=1)
        if not data or not data.get('Success'):
            continue
        info = data.get('Data') or {}
        raw_amount = info.get('AvailableAmount')
        if raw_amount is None:
            continue
        try:
            # 金额可能带千分位逗号，如 "1,234.56"
            amount = float(str(raw_amount).replace(',', ''))
        except ValueError:
            continue
        return amount, info.get('Currency') or ''
    return None, None

def main():
    try:
        config = load_config()
    except Exception as e:
        logger.exception("加载配置失败: %s", e)
        sys.exit(1)

    users = config.get('users', [])
    tg_conf = config.get('telegram', {})
    
    report_lines = []
    balance_cache = {}  # 同一账号(AK)的余额只查询一次
    today = datetime.datetime.now().strftime("%Y-%m-%d")
    report_lines.append(f"📊 *[阿里云多账号 - 每日财报]*")
    report_lines.append(f"📅 日期: {today}\n")

    for user in users:
        try:
            target_id = user.get('instance_id', '').strip()
            target_region = user.get('region', '').strip()
            resgroup = user.get('resgroup', '').strip()
            bill_endpoint = (user.get('bill_endpoint') or 'business.ap-southeast-1.aliyuncs.com').strip()
            if user.get('paused') or user.get('disabled'):
                user_name = user.get('name', '').strip() or target_id or "Unknown_Device"
                logger.info("[%s] 监控已暂停，日报仅标注暂停状态", user_name)
                report_lines.append(
                    f"👤 *{sanitize_markdown(user_name)}* (暂停)\n"
                    f"   ⏸️ 监控: 已暂停\n"
                )
                continue

            # [名字显示修复] 优先使用备注，没有则用ID，再没有则用Unknown
            user_name = user.get('name', '').strip()
            if not user_name:
                user_name = target_id if target_id else "Unknown_Device"
            
            client = AcsClient(user['ak'].strip(), user['sk'].strip(), target_region)
            
            # 1. CDT 流量
            traffic_data = do_common_request(AcsClient(user['ak'].strip(), user['sk'].strip(), 'cn-hangzhou'), 'cdt.aliyuncs.com', '2021-08-13', 'ListCdtInternetTraffic')
            traffic_gb = -1  # -1 表示查询失败
            if traffic_data:
                traffic_gb = sum(d.get('Traffic', 0) for d in traffic_data.get('TrafficDetails', [])) / (1024**3)

            # 2. BSS 账单 (兼容国际站/国内站: 优先 DescribeInstanceBill，失败回退 QueryBillOverview)
            bill_amount = -1
            bill_currency = 'USD'

            # 尝试1: DescribeInstanceBill (精确到实例)
            bill_params = {
                'BillingCycle': datetime.datetime.now().strftime("%Y-%m"),
                'InstanceID': target_id
            }
            bill_data = do_common_request(client, 'business.aliyuncs.com', '2017-12-14', 'DescribeInstanceBill', bill_params, retries=1)
            if bill_data and bill_data.get('Success'):
                items = bill_data.get('Data', {}).get('Items', [])
                bill_amount = sum(float(item.get('PretaxAmount', 0)) for item in items)
                if items:
                    bill_currency = items[0].get('Currency', 'USD')

            # 尝试2: 回退到 QueryBillOverview (国际站兼容)
            if bill_amount == -1:
                bill_params2 = {'BillingCycle': datetime.datetime.now().strftime("%Y-%m")}
                bill_data2 = do_common_request(client, bill_endpoint, '2017-12-14', 'QueryBillOverview', bill_params2)
                if bill_data2:
                    items2 = bill_data2.get('Data', {}).get('Items', {}).get('Item', [])
                    bill_amount = sum(float(item.get('PretaxAmount', 0)) for item in items2)
                    if items2:
                        bill_currency = items2[0].get('Currency', 'USD')

            # 2.5 账户可用余额 (同账号多实例复用缓存，避免重复请求)
            ak_key = user['ak'].strip()
            if ak_key in balance_cache:
                balance_amount, balance_currency = balance_cache[ak_key]
            else:
                balance_amount, balance_currency = get_account_balance(client, bill_endpoint)
                balance_cache[ak_key] = (balance_amount, balance_currency)

            # 3. ECS 状态
            ecs_params = {'PageSize': 50, 'RegionId': target_region}
            if resgroup:
                ecs_params['ResourceGroupId'] = resgroup
            ecs_data = do_common_request(client, 'ecs.aliyuncs.com', '2014-05-26', 'DescribeInstances', ecs_params)
            
            status, ip, spec = "NotFound", "N/A", "N/A"
            
            if ecs_data and 'Instances' in ecs_data:
                for inst in ecs_data['Instances'].get('Instance', []):
                    if inst['InstanceId'] == target_id:
                        status = inst.get('Status', 'Unknown')
                        # IP
                        pub = inst.get('PublicIpAddress', {}).get('IpAddress', [])
                        eip = inst.get('EipAddress', {}).get('IpAddress', "")
                        ip = eip if eip else (pub[0] if pub else "无公网IP")
                        
                        # Spec (0.5G 内存修复)
                        cpu = inst.get('Cpu', 0)
                        mem_mb = inst.get('Memory', 0)
                        if mem_mb > 0 and mem_mb % 1024 == 0:
                            mem_str = f"{int(mem_mb/1024)}"
                        else:
                            mem_str = f"{mem_mb/1024:.1f}"
                        
                        spec = f"{cpu}C{mem_str}G"
                        break 

            # 4. 判定
            quota = user.get('traffic_limit', 180)
            bill_limit = user.get('bill_threshold', 1.0)
            
            if traffic_gb >= 0:
                percent = (traffic_gb / quota) * 100 if quota > 0 else 0
                traffic_str = f"{traffic_gb:.2f} GB ({percent:.1f}%)"
            else:
                percent = 0
                traffic_str = "⚠️ 查询失败"
            
            bill_str = f"${bill_amount:.2f}" if bill_amount != -1 else "Fail"
            if bill_amount != -1 and bill_currency == 'CNY':
                bill_str = f"¥{bill_amount:.2f}"
                # USD 阈值换算为 CNY，汇率可通过配置项 usd_cny_rate 覆盖（默认 7.0）
                bill_limit = bill_limit * float(user.get('usd_cny_rate', 7.0))
            elif bill_amount != -1:
                # 覆盖货币符号（支持根据配置动态显示）
                bill_str = f"{user.get('currency', '$')}{bill_amount:.2f}"

            if balance_amount is not None:
                balance_str = f"{currency_symbol(balance_currency, user.get('currency') or '$')}{balance_amount:.2f}"
                if balance_amount < 0:
                    balance_str += " ⚠️ 欠费"
            else:
                balance_str = "⚠️ 查询失败"

            status_icon = "✅"
            if bill_amount == -1: status_icon = "⚠️ 账单查询异常"
            if traffic_gb >= 0 and traffic_gb > quota: status_icon = "⚠️ 流量超标"
            if bill_amount > bill_limit: status_icon = "💸 扣费预警"
            if traffic_gb < 0: status_icon = "⚠️ 流量查询异常"
            
            run_icon = "🟢" if status == "Running" else "🔴"
            if status == "Stopped": run_icon = "⚫"
            if status == "NotFound": run_icon = "❓"

            user_report = (
                f"👤 *{sanitize_markdown(user_name)}* ({spec})\n"
                f"   🖥️ 状态: {run_icon} {status}\n"
                f"   🌐 IP: `{ip}`\n"
                f"   📉 流量: {traffic_str}\n"
                f"   💰 账单: *{bill_str}*\n"
                f"   💳 余额: *{balance_str}*\n"
                f"   📝 评价: {status_icon}\n"
            )
            logger.info("用户 [%s] 日报详情:\n%s", user_name, user_report)
            report_lines.append(user_report)

        except Exception as e:
            logger.exception("处理用户 %s 时出错: %s", user.get('name', 'Unknown'), e)
            report_lines.append(f"❌ *{sanitize_markdown(user.get('name', 'Unknown'))}* Error: {sanitize_markdown(e)}\n")

    final_msg = "\n".join(report_lines)
    send_tg_report(tg_conf, final_msg)

if __name__ == "__main__":
    main()

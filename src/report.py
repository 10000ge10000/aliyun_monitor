# -*- coding: utf-8 -*-
import json
import requests
import datetime
import os
import sys
import warnings

warnings.filterwarnings("ignore")

try:
    from aliyunsdkcore.client import AcsClient
    from aliyunsdkcore.request import CommonRequest
except ImportError:
    sys.exit(1)

CONFIG_FILE = '/opt/scripts/config.json'

def load_config():
    if not os.path.exists(CONFIG_FILE):
        sys.exit(1)
    with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
        return json.load(f)

def send_tg_report(tg_conf, message):
    if not tg_conf.get('bot_token') or not tg_conf.get('chat_id'):
        return
    try:
        url = f"https://api.telegram.org/bot{tg_conf['bot_token']}/sendMessage"
        data = {"chat_id": tg_conf['chat_id'], "text": message, "parse_mode": "Markdown"}
        requests.post(url, json=data, timeout=10)
    except:
        pass

def do_common_request(client, domain, version, action, params=None, method='POST', timeout=30, retries=3):
    for attempt in range(1, retries + 1):
        try:
            request = CommonRequest()
            request.set_domain(domain)
            request.set_version(version)
            request.set_action_name(action)
            request.set_method(method)
            request.set_protocol_type('https')
            request.set_connect_timeout(timeout * 1000)   # 毫秒
            request.set_read_timeout(timeout * 1000)       # 毫秒
            if params:
                for k, v in params.items():
                    request.add_query_param(k, v)
            response = client.do_action_with_exception(request)
            return json.loads(response.decode('utf-8'))
        except Exception as e:
            if attempt < retries:
                import time
                time.sleep(2 * attempt)
                continue
            return None

def main():
    config = load_config()
    users = config.get('users', [])
    tg_conf = config.get('telegram', {})
    
    report_lines = []
    today = datetime.datetime.now().strftime("%Y-%m-%d")
    report_lines.append(f"📊 *[阿里云多账号 - 每日财报]*")
    report_lines.append(f"📅 日期: {today}\n")

    for user in users:
        try:
            target_id = user.get('instance_id', '').strip()
            target_region = user.get('region', '').strip()
            
            # [名字显示修复] 优先使用备注，没有则用ID，再没有则用Unknown
            user_name = user.get('name', '').strip()
            if not user_name:
                user_name = target_id if target_id else "Unknown_Device"
            
            client = AcsClient(user['ak'].strip(), user['sk'].strip(), target_region)
            
            # 1. CDT 流量
            traffic_data = do_common_request(client, 'cdt.aliyuncs.com', '2021-08-13', 'ListCdtInternetTraffic')
            traffic_gb = -1  # -1 表示查询失败
            if traffic_data:
                traffic_gb = sum(d.get('Traffic', 0) for d in traffic_data.get('TrafficDetails', [])) / (1024**3)

            # 2. BSS 账单
            bill_params = {'BillingCycle': datetime.datetime.now().strftime("%Y-%m")}
            bill_data = do_common_request(client, 'business.ap-southeast-1.aliyuncs.com', '2017-12-14', 'QueryBillOverview', bill_params)
            bill_amount = -1
            if bill_data:
                items = bill_data.get('Data', {}).get('Items', {}).get('Item', [])
                bill_amount = sum(item.get('PretaxAmount', 0) for item in items)

            # 3. ECS 状态
            ecs_params = {'PageSize': 50, 'RegionId': target_region}
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
                percent = (traffic_gb / quota) * 100
                traffic_str = f"{traffic_gb:.2f} GB ({percent:.1f}%)"
            else:
                percent = 0
                traffic_str = "⚠️ 查询失败"
            
            bill_str = f"${bill_amount:.2f}" if bill_amount != -1 else "Fail"
            status_icon = "✅"
            if traffic_gb >= 0 and traffic_gb > quota: status_icon = "⚠️ 流量超标"
            if bill_amount > bill_limit: status_icon = "💸 扣费预警"
            if traffic_gb < 0: status_icon = "⚠️ 流量查询异常"
            
            run_icon = "🟢" if status == "Running" else "🔴"
            if status == "Stopped": run_icon = "⚫"
            if status == "NotFound": run_icon = "❓"

            user_report = (
                f"👤 *{user_name}* ({spec})\n"
                f"   🖥️ 状态: {run_icon} {status}\n"
                f"   🌐 IP: `{ip}`\n"
                f"   📉 流量: {traffic_str}\n"
                f"   💰 账单: *{bill_str}*\n"
                f"   📝 评价: {status_icon}\n"
            )
            report_lines.append(user_report)

        except Exception as e:
            report_lines.append(f"❌ *{user.get('name', 'Unknown')}* Error: {str(e)}\n")

    final_msg = "\n".join(report_lines)
    send_tg_report(tg_conf, final_msg)

if __name__ == "__main__":
    main()

# 阿里云 CDT 流量监控 & 自动止损脚本 (支持国内/国际双版本)

![OS](https://img.shields.io/badge/OS-Linux-blue?logo=linux)
![Python](https://img.shields.io/badge/Python-3.x-yellow?logo=python)
![Alibaba Cloud](https://img.shields.io/badge/Alibaba%20Cloud-Domestic%20%26%20International-orange?logo=alibabacloud)

一个不仅为自定义 **Alpine** 系统准备的，更全面支持 **阿里云国内版（人民币结算）** 与 **阿里云国际版（美元结算）** 的 **CDT 公网流量监控 + 自动止损工具**。  
在流量或账单即将失控前 **强制关机**，全面适配多节点区域及 Python 3.12 兼容性问题，真正帮你守住钱包 💰。

---

## ✨ 核心特性

- 🌍 **双轨支持**：完美支持中国内地账单系统（￥）与国际账单系统（$）。
- 🛡️ **流量熔断**：每分钟检测 CDT 使用量，超过阈值立即关机止损。
- 💵 **底层双端兼容**：绕过 API 限制，动态适配业务节点读取当月实时账单余额。
- 🚀 **防黑洞卡死机制**：内置 SNI 与 IPv6 黑洞自动绕过补丁，保障常驻任务在高延迟或 Python 3.12+ 环境下稳定运行。
- 🔄 **自动恢复**：次月流量重置后自动开机恢复业务。
- 📊 **多账号多地域**：同时监控任意组合（不同账号、不同区域、不同内外版实例）。
- 📩 **Telegram 通知**：异常监控告警 + 每日图文并茂的汇总日报。

---

## ⭐ 运行截图

<div align="center">
  <img src="https://github.com/user-attachments/assets/381e346d-604b-47c7-9970-e4e29c87bfb0" width="320" alt="运行截图" />
  <br>
  <p><i>运行效果预览</i></p>
</div>

---

## 🛠️ 前置准备

### 1️⃣ Telegram 通知参数
- 创建机器人并获取 Token：[@BotFather](https://t.me/BotFather)
- 获取您接收消息的 Chat ID：[@userinfobot](https://t.me/userinfobot)

### 2️⃣ 阿里云 RAM 权限设置
为了安全起见，**强烈建议不要使用主账号**。请前往 [RAM 访问控制](https://ram.console.alibabacloud.com/users) 创建子用户并授予以下系统权限：
- `AliyunECSFullAccess`（含开关机与查询权限）
- `AliyunCDTReadOnlyAccess` 或 `AliyunCDTFullAccess`（查询流量）
- `AliyunBSSReadOnlyAccess`（查询财务与账单模块）

*(若需要了解详细的创建与使用流程，请查阅本项目内的 [实例开通指南](实例开通.md))*

---

## （一） Alpine Linux（VNC）初始化（可选，针对底层系统玩家）

> ⚠️ **如果您是普通的 Linux (如 Ubuntu/Debian) 用户，请直接跳过本节至 "(三) 一键安装"，本节仅适用于脱水版 Alpine 系统。**

1. 登录阿里云实例的 **VNC 控制台**
2. 复制本项目中 `vnc.sh` 的全量内容。
3. 将代码 **完整粘贴到 VNC 界面并回车执行**。
4. 初始完毕后即可按以下默认信息 SSH 远程登录：
   - **用户名**：`root`
   - **初始化密码**：`yiwan123`

## （二） Alpine 修复 GRUB 引导并重装 Debian 13 (可选扩展)

> 适用于 **系统无法启动 / GRUB 损坏 / Debian 无法进入** 等进阶场景，使用 `install2.sh`。详见源码。

---

## （三） 一键安装与配置监控 (所有适用者推荐)

使用 **root 用户** 在任意连通互联网的 Linux 服务器或所监控的 ECS 本机上执行：

```bash
wget -qO- https://raw.githubusercontent.com/10000ge10000/aliyun_monitor/main/install.sh | sh
```

脚本将提供丝滑的交互式配置，自动：
* 检测并修齐 Python 运行微环境与 Pip 依赖。
* 拉取已深度解除底层网关 Bug 的执行组件。
* 引导您录入 Telegram 配置、选择站别类型（人民币或美元账单）、输入并配置多个待监控账号。
* 设置系统计划任务（Cron），按 **5分钟/次** 及每天早 9 点执行巡检与汇报。

> 提示：如果日后需要增加、删除机器或刷新底层组件配置，只需再次运行该脚本命令即可进入智能管理面板。

---

## 🗑️ 卸载

```bash
wget -qO- https://raw.githubusercontent.com/10000ge10000/aliyun_monitor/main/uninstall.sh | sh
```

---

## ⚠️ 免责声明

1. 本项目仅供学习与技术交流使用。
2. 虽然我们尽力适配和兜底了绝大部分的系统、网络、API 阻断与连接层 BUG，但**作者不对因脚本异常、API 变更、依赖挂除或配置错误导致的任何流量流失及费用直接负责。**
3. **强烈建议同时在阿里云费用中心后台设置「预算告警 / 垫底限额」作为最后的防线。**

---

## ⭐ 欢迎 Star 支持

如果这个项目帮您梳理了多节点的部署或者成功避免了一次“破产”，欢迎点个 ⭐！你的支持是我们持续维护的动力 🙏

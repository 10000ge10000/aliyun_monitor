# 阿里云 CDT 流量监控与自动止损脚本

![OS](https://img.shields.io/badge/OS-Linux-blue?logo=linux)
![Python](https://img.shields.io/badge/Python-3.x-yellow?logo=python)
![Alibaba Cloud](https://img.shields.io/badge/Alibaba%20Cloud-Domestic%20%26%20International-orange?logo=alibabacloud)

一个同时支持 **阿里云国内站（人民币结算）** 和 **阿里云国际站（美元结算）** 的 ECS 流量监控、账单查询和自动止损工具。

脚本会定时查询 CDT / ECS / 账单接口，在流量或费用接近风险阈值时自动关机，并通过 Telegram 发送告警和日报。也可以选择启用 Telegram 控制机器人，远程查询实例状态、开机、关机、重启和设置定时任务。

> 注意：本项目可以帮你降低误用流量导致的损失，但不能替代阿里云费用中心的预算告警、余额提醒和人工复核。请优先使用 RAM 子账号和最小权限。

## 快速开始

使用 **root 用户** 在任意能访问公网的 Linux 服务器，或被监控的 ECS 本机执行：

```bash
wget -qO- https://raw.githubusercontent.com/10000ge10000/aliyun_monitor/main/install.sh | sh
```

首次安装按这个顺序准备：

1. 准备阿里云账号和 ECS 实例。国内站已有 ECS 可以直接用；国际站低成本 CDT 玩法见下方折叠教程。
2. 创建 RAM 子用户，不要使用主账号 AccessKey。
3. 准备 Telegram Bot Token、接收通知的 Chat ID；如需控制机器人，再准备管理员 Telegram 用户 ID。
4. 运行安装脚本，按提示选择国内站或国际站，录入实例、流量阈值、账单阈值和通知参数。

安装脚本会自动：

- 安装 Python 运行环境和依赖。
- 下载 `monitor.py`、`report.py`、可选的 `ecs_bot.py`。
- 配置每 5 分钟一次的流量监控。
- 配置每天 9 点发送日报。
- 可选创建 `aliyun-ecs-bot.service`，启用 Telegram 控制机器人。

如果日后需要新增实例、修改阈值、暂停监控或管理机器人，重新运行同一条安装命令即可进入管理菜单。

## 项目能力

- 支持阿里云国内站和国际站账号混合监控。
- 支持多个账号、多个地域、多个 ECS 实例。
- 查询 CDT 计费流量和当月账单，超过阈值自动关机止损。
- 次月流量重置后可自动开机恢复。
- Telegram 告警和每日汇总日报。
- 可选 Telegram 控制机器人，支持状态查询、开机、关机、重启和定时任务。
- 兼容 Python 3.12+，内置 IPv4 优先和 SNI 连接修复逻辑。

## 运行截图

<div align="center">
  <img src="https://github.com/user-attachments/assets/381e346d-604b-47c7-9970-e4e29c87bfb0" width="320" alt="运行截图" />
  <br>
  <p><i>运行效果预览</i></p>
</div>

## 视频教程

<div align="center">
  <a href="https://www.bilibili.com/video/BV1b2rfBnEZg/" target="_blank">
    <img width="650" src="https://images.weserv.nl/?url=i2.hdslb.com/bfs/archive/49eb886eab33d88e1cc88c2d3bd624d7eb703d32.jpg" alt="点击观看演示视频" />
  </a>
  <br><br>
  <a href="https://www.bilibili.com/video/BV1b2rfBnEZg/" target="_blank">
    <img src="https://img.shields.io/badge/Bilibili-点击上方封面或此处观看完整视频-FF8EB3?style=for-the-badge&logo=bilibili&logoColor=white" alt="Bilibili Video Tutorial"/>
  </a>
</div>

## 基础准备

### Telegram 通知参数

- Bot Token：在 [@BotFather](https://t.me/BotFather) 创建机器人后获取。
- Chat ID：可通过 [@userinfobot](https://t.me/userinfobot) 获取接收通知的会话 ID。
- 管理员用户 ID：只有启用 Telegram 控制机器人时才需要。注意，Telegram 用户 ID 不等于群组 Chat ID。

### 阿里云 RAM 权限

为了安全起见，强烈建议创建 RAM 子用户，并只授予必要权限。

- 国内站 RAM 控制台：[https://ram.console.aliyun.com/users](https://ram.console.aliyun.com/users)
- 国际站 RAM 控制台：[https://ram.console.alibabacloud.com/users](https://ram.console.alibabacloud.com/users)

建议权限：

- `AliyunECSFullAccess`：查询实例、开机、关机、重启。
- `AliyunCDTReadOnlyAccess` 或 `AliyunCDTFullAccess`：查询 CDT 流量。
- `AliyunBSSReadOnlyAccess`：查询账单。

> 如果你只使用监控和日报，不启用控制机器人，也仍然需要 ECS 查询和关机权限，因为自动止损需要调用 ECS 关机接口。

## 按你的场景选择

<a id="已有国内站-ecs-接入监控"></a>

<details>
<summary><strong>已有阿里云国内站 ECS，只想接入监控</strong></summary>

适合已经在阿里云中国站有 ECS 的用户。国内站不享受国际站 CDT 免费额度，流量通常按人民币实时计费，更需要配置止损。

操作步骤：

1. 登录 [阿里云中国站](https://www.aliyun.com/)。
2. 确认 ECS 公网计费方式。按量用户建议使用“按使用流量计费”，并根据业务需要设置带宽峰值。
3. 创建 RAM 子用户，授予 README 上方列出的 ECS、CDT、BSS 权限。
4. 运行安装脚本。
5. 当脚本询问账号所属类型时，选择 **国内站 / 人民币账单**。
6. 按提示填写实例名称、地域、实例 ID、流量阈值、账单阈值和 Telegram 参数。

国内站账单和国际站账单接口不同，本项目已在监控、日报和 Telegram 控制机器人中分别适配。

</details>

<a id="国际站-cdt-低成本实例教程"></a>

<details>
<summary><strong>国际站 CDT 低成本实例：CDT + OSS 镜像 + 抢占式 ECS + EIP</strong></summary>

这个场景适合想使用阿里云国际站 CDT 免费额度的用户。核心思路是：开通 CDT，使用 OSS 导入轻量 Alpine 镜像，创建抢占式 ECS，不直接分配公网 IPv4，最后绑定 EIP。

### 1. 理解 CDT 计费

CDT（Cloud Data Transfer）是阿里云的统一流量计费模式。国际站账号开通后，通常可获得每月公网流量免费额度，其中非中国内地地域可用额度更高，适合香港、新加坡、日本等区域。

重要规则：

- CDT 计费流量通常按“流入流量”和“流出流量”取最大值计算。
- 下载大文件、拉取备份、BT、测速等流入流量也会消耗额度。
- 本项目查询的是阿里云官方接口返回的计费流量，因此下载导致读数上涨是正常现象。

### 2. 准备国际站账号

1. 注册并登录 [阿里云国际站](https://www.alibabacloud.com/zh/)。
2. 绑定有效支付方式，例如海外信用卡或 PayPal。
3. 在控制台搜索 **CDT**，进入云数据传输控制台。
4. 如果页面显示未开通或未升级，点击开通服务或一键升级。

CDT 入口也可以直接访问：[https://cdt.console.alibabacloud.com/overview](https://cdt.console.alibabacloud.com/overview)

### 3. 开通 OSS 并导入 Alpine 镜像

为了降低资源占用，可以使用 Alpine Linux 镜像：

1. 下载本项目提供的 Alpine 虚拟化镜像：

   [下载 alpine-virt-3.23.2-x86_64.iso](https://github.com/10000ge10000/aliyun_monitor/releases/download/v1.0/alpine-virt-3.23.2-x86_64.iso)

2. 在控制台开通 OSS。
3. 创建 Bucket：
   - 区域必须和后续 ECS 区域一致。
   - 存储类型选择标准存储。
   - 读写权限选择私有。
4. 上传 `.iso` 到该 Bucket。
5. 进入 ECS 控制台的实例与镜像页面，选择导入镜像。
6. 选择刚上传的 `.iso`，操作系统选择 Linux / Other Linux，系统盘大小按后续用途设置。

### 4. 创建抢占式 ECS

1. 进入 ECS 创建实例页面。
2. 付费模式选择抢占式实例或按量付费中的抢占式规格。
3. 地域选择和 OSS、镜像一致的区域。
4. 实例规格选择低配入门规格，例如 2 vCPU / 0.5 GiB 或 1 vCPU / 1 GiB。
5. 镜像选择刚导入的自定义 Alpine 镜像。
6. 系统盘建议至少 3 GB，确保能放下 Python 环境和脚本依赖。
7. 公网 IPv4 地址不要直接勾选分配。
8. 安全组至少开放 SSH 端口，其他端口按业务需要开放。
9. 确认价格后创建实例。

抢占式实例可能被阿里云回收，不建议承载核心生产数据。重要数据请使用 OSS、快照或其他方式备份。

### 5. 创建并绑定 EIP

1. 打开 [弹性公网 IP 控制台](https://vpc.console.alibabacloud.com/eip)。
2. 创建与 ECS 同地域的 EIP。
3. 将 EIP 绑定到刚创建的 ECS。
4. 再运行本项目安装脚本，把实例纳入自动监控。

</details>

<a id="alpine-vnc-初始化和-debian-修复"></a>

<details>
<summary><strong>Alpine / VNC 初始化和 Debian 修复</strong></summary>

普通 Ubuntu、Debian、CentOS 等 Linux 用户可以跳过本节，直接运行安装脚本。本节只适合使用精简 Alpine 镜像或需要修复引导的用户。

### Alpine VNC 初始化

1. 登录阿里云实例的 VNC 控制台。
2. 打开 [vnc.sh](https://raw.githubusercontent.com/10000ge10000/aliyun_monitor/main/vnc.sh)，复制完整脚本内容。
3. 粘贴到 VNC 界面并回车执行。
4. 初始化完成后，可使用以下默认信息 SSH 登录：
   - 用户名：`root`
   - 初始密码：`yiwan123`

### 修复 GRUB 并重装 Debian 13

适用于系统无法启动、GRUB 损坏或 Debian 无法进入等场景。使用 root 登录 Alpine 后执行：

```bash
wget -qO- https://raw.githubusercontent.com/10000ge10000/aliyun_monitor/main/install2.sh | sh
```

</details>

<a id="telegram-控制机器人"></a>

<details>
<summary><strong>Telegram 控制机器人：查询、开机、关机、重启、定时任务</strong></summary>

安装脚本默认不会启用控制机器人。只有在首次安装或管理菜单中明确选择启用后，才会创建 `aliyun-ecs-bot.service`。

启用后支持以下命令：

```text
/menu                       打开交互菜单
/list                       查看实例列表
/status <实例名或ID>         查询实例状态
/start_instance <实例名或ID>  开机
/stop <实例名或ID>           关机，需确认
/reboot <实例名或ID>         重启，需确认
/timers                     查看定时任务
/help                       查看帮助
```

systemd 管理命令：

```bash
systemctl status aliyun-ecs-bot.service
systemctl restart aliyun-ecs-bot.service
systemctl stop aliyun-ecs-bot.service
```

也可以重新运行安装脚本进入管理菜单，选择 **Telegram 控制机器人管理**，进行启用、停用、查看状态或修改管理员 ID。

控制机器人具备远程开机、关机、重启 ECS 的能力。请务必只把可信 Telegram 用户 ID 写入 `admin_users`，不要把群组 Chat ID 当作控制权限。

</details>

<a id="常见问题"></a>

<details>
<summary><strong>常见问题</strong></summary>

**抢占式实例被释放了怎么办？**

这是抢占式实例的正常机制。实例被释放后，需要重新创建实例，并在安装脚本管理菜单中更新或重新添加新的实例 ID。重要数据请提前做快照或外部备份。

**为什么账单里还是会有几分几毛钱？**

CDT 主要抵扣公网流量费用，不抵扣 ECS 计算资源、磁盘、快照、EIP 等基础资源费用。小额账单通常来自这些资源本身。

**下载文件也算流量吗？**

算。CDT 通常采用流入和流出取最大值的计费方式。大量下载、拉取镜像、同步网盘、BT 或测速都可能快速消耗额度。

**脚本会不会影响已经暂停的实例？**

通过安装脚本暂停监控后，`monitor.py` 会跳过该实例的自动巡检和自动开关机，`report.py` 会在日报中标注监控已暂停。

</details>

## 暂停或恢复某台实例的监控

当某台机器处于安全锁定、维护或暂不希望自动开关机时，可以临时暂停监控：

1. 重新运行安装脚本进入管理菜单。
2. 选择 **暂停/恢复监控实例**。
3. 选择目标实例即可切换暂停或恢复状态。

暂停后：

- `monitor.py` 将跳过该机器的巡检与自动开关机。
- `report.py` 会在日报里标注“监控已暂停”。

## 卸载

```bash
wget -qO- https://raw.githubusercontent.com/10000ge10000/aliyun_monitor/main/uninstall.sh | sh
```

卸载脚本会清理监控计划任务、Telegram 控制机器人 systemd 服务和 `/opt/scripts` 下的项目文件。

## 免责声明

1. 本项目仅供学习和技术交流使用。
2. 阿里云计费规则、接口权限和免费额度可能变化，请以阿里云官方页面和账单为准。
3. 作者不对因脚本异常、API 变更、依赖故障、配置错误或用户误操作导致的费用损失负责。
4. 强烈建议同时在阿里云费用中心设置预算告警和余额提醒，作为最后防线。

## 致谢

感谢 Biliup 社区关于国际站 CDT 玩法的分享，给本项目早期方案提供了思路参考：

- [https://bbs.biliup.rs/t/topic/46](https://bbs.biliup.rs/t/topic/46)

感谢 [@alatter](https://github.com/alatter) 在 [PR #6](https://github.com/10000ge10000/aliyun_monitor/pull/6) 中提供 Telegram ECS 控制机器人思路与原型，实现方向包括实例状态查询、远程开关机/重启、定时任务和机器人交互菜单。本项目已在当前 `src/` 结构中选择性吸收并完善相关能力。

## 欢迎 Star

如果这个项目帮你梳理了多节点部署，或者避免了一次流量超额扣费，欢迎点个 Star 支持后续维护。

# MyJobAgent (New Architecture)

> ⚠️ **Disclaimer / 免责声明**
>
> **当前版本仅验证了全链路工作流（Workflow）的连通性。**
> The current version only validates the connectivity of the end-to-end workflow.
>
> 核心功能（数据采集 -> LLM 分析 -> 飞书推送）已跑通，但**输出结果（关键词提取质量）尚不具备稳定性、可解释性或可预测性**。
> The output results (keyword extraction quality) are currently **NOT** stable, explainable, or predictable.
>
> 请勿将其用于生产环境或作为关键决策依据。
> Do not use it for production or critical decision-making.

---

## 🎯 项目简介 (Introduction)

MyJobAgent 是一个基于本地行为数据分析的智能代理原型。它通过采集用户的活动数据（ActivityWatch），利用 LLM（大语言模型）提取职业兴趣关键词，并自动推送到飞书（Feishu）。

## 🏗️ 核心链路 (Workflow)

1.  **数据采集 (Data Collection)**: 
    - 使用 `ActivityWatch` 采集本地窗口和浏览器活动数据。
    - 数据存储于 `core/local_events.db` (SQLite)。
2.  **数据处理 & LLM 分析 (Processing & LLM)**:
    - 脚本 `core/test_llm.py` 读取最近 7 天数据。
    - 对数据进行清洗、聚合和压缩。
    - 调用 LLM (DashScope/Qwen) 提取 10 个职业兴趣关键词。
3.  **结果推送 (Notification)**:
    - 通过飞书 API (App Mode 或 Webhook) 将关键词推送到用户手机/飞书客户端。

## 🚀 快速开始 (Quick Start)

### 1. 环境准备
确保已安装 Python 3.10+ 和必要的依赖：
```bash
pip install -r core/requirements.txt
```

### 2. 配置 .env
复制 `.env.example`（如果有）或直接创建 `.env` 文件，填入以下关键配置：

```ini
# LLM 配置
DASHSCOPE_API_KEY=sk-xxxxxxxxxxxxxxxxxxxxxxxx

# 飞书配置 (二选一)
## 方式 A: 群机器人 (推荐，简单)
FEISHU_WEBHOOK_URL=https://open.feishu.cn/open-apis/bot/v2/hook/xxxx

## 方式 B: 企业自建应用 (支持私聊)
FEISHU_APP_ID=cli_xxxxxxxx
FEISHU_APP_SECRET=xxxxxxxx
FEISHU_MOBILES=13800000000  # 你的手机号，用于接收消息
# 注意：方式 B 需要在飞书后台开通 "contact:user.id:readonly" 权限并发布版本
```

### 3. 运行测试
执行核心测试脚本，验证全流程：
```bash
python core/test_llm.py
```
如果成功，你将在控制台看到 LLM 提取的关键词，并在飞书收到推送。

## 🛠️ 项目结构 (Structure)

- `core/`
  - `collectors/`: 数据采集模块
  - `llm/`: LLM 客户端封装
  - `pusher/`: 飞书推送模块 (`feishu_pusher.py`)
  - `storage/`: 数据库操作
  - `test_llm.py`: **主测试入口** (Workflow Entry)
- `docs/`: 文档 (包含 `feishu_guide.md`)

## ⚠️ 已知限制 (Known Limitations)

1.  **数据依赖**: 依赖本地 ActivityWatch 数据，如果数据为空，LLM 输出可能为幻觉或空。
2.  **LLM 稳定性**: Prompt 尚未经过精细调优，关键词提取结果波动较大。
3.  **Token 限制**: 简单粗暴的数据压缩策略可能导致丢失细节或超出 LLM 上下文限制。

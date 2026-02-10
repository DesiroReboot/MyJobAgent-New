import json
import os
import time
from pathlib import Path
from typing import List, Dict, Optional, Union
from datetime import datetime

import requests

# 自动加载 .env 文件
_env_path = Path(__file__).parent.parent.parent / ".env"
if _env_path.exists():
    from dotenv import load_dotenv
    load_dotenv(_env_path, override=True)


class FeishuPusher:
    """飞书推送器，支持群机器人和应用消息两种模式"""

    # 飞书API地址
    FEISHU_API_BASE = "https://open.feishu.cn/open-apis"
    TOKEN_URL = f"{FEISHU_API_BASE}/auth/v3/tenant_access_token/internal"
    USER_INFO_URL = f"{FEISHU_API_BASE}/user/v1/me"
    MESSAGE_URL = f"{FEISHU_API_BASE}/im/v1/messages"

    def __init__(self, mode: str = "bot", webhook_url: str = None, app_id: str = None, app_secret: str = None, user_id: str = None, email: str = None, mobile: str = None, timeout: int = 20):
        """
        初始化飞书推送器

        Args:
            mode: 推送模式 ("bot"=群机器人, "app"=应用消息)
            webhook_url: 群机器人Webhooks URL（mode="bot"时必填）
            app_id: 飞书应用App ID（mode="app"时必填）
            app_secret: 飞书应用App Secret（mode="app"时必填）
            user_id: 接收消息的用户Open ID（mode="app"时选填，若无则需提供email或mobile）
            email: 接收消息的用户邮箱（mode="app"时选填，用于查找Open ID）
            mobile: 接收消息的用户手机号（mode="app"时选填，用于查找Open ID）
            timeout: 请求超时时间（秒）
        """
        self.mode = mode
        self.timeout = timeout

        # 群机器人模式配置
        self.webhook_url = webhook_url or os.getenv("FEISHU_WEBHOOK_URL")

        # 应用模式配置
        self.app_id = app_id or os.getenv("FEISHU_APP_ID")
        self.app_secret = app_secret or os.getenv("FEISHU_APP_SECRET")
        self.user_id = user_id or os.getenv("FEISHU_OPEN_ID")
        self.email = email or os.getenv("FEISHU_EMAIL")
        self.mobile = mobile or os.getenv("FEISHU_MOBILES") # 注意：env中可能是MOBILES但这里我们处理单个mobile
        
        self._tenant_access_token: Optional[str] = None
        self._token_expires_at: float = 0

    def set_user_id(self, user_id: str) -> None:
        """动态设置用户ID"""
        self.user_id = user_id

    def _get_tenant_access_token(self) -> str:
        """获取应用的访问令牌"""
        # 检查缓存的token是否过期（提前5分钟刷新）
        if self._tenant_access_token and time.time() < self._token_expires_at - 300:
            return self._tenant_access_token

        if not self.app_id or not self.app_secret:
            raise ValueError("APP_ID 和 APP_SECRET 未配置")

        payload = {
            "app_id": self.app_id,
            "app_secret": self.app_secret
        }

        resp = requests.post(self.TOKEN_URL, json=payload, timeout=self.timeout)
        resp.raise_for_status()

        data = resp.json()
        if data.get("code") != 0:
            raise RuntimeError(f"获取TenantAccessToken失败: {data.get('msg')}")

        self._tenant_access_token = data["tenant_access_token"]
        # token有效期2小时，缓存1.5小时
        self._token_expires_at = time.time() + 5400
        return self._tenant_access_token

    def _get_user_id(self) -> str:
        """获取目标用户的open_id"""
        if self.user_id:
            return self.user_id

        if not self.email and not self.mobile:
            raise ValueError("未配置 FEISHU_OPEN_ID, FEISHU_EMAIL 或 FEISHU_MOBILES，无法确定接收用户")

        # 获取访问令牌
        token = self._get_tenant_access_token()
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=utf-8"
        }
        
        # 使用 batch_get_id 接口
        url = f"{self.FEISHU_API_BASE}/contact/v3/users/batch_get_id"
        params = {"user_id_type": "open_id"}
        
        payload = {}
        if self.email:
            payload["emails"] = [self.email]
        elif self.mobile:
            # 飞书API通常要求手机号带国家码，尝试自动补充+86
            mobile = self.mobile
            if mobile.isdigit() and len(mobile) == 11:
                mobile = f"+86{mobile}"
            payload["mobiles"] = [mobile]
        
        resp = requests.post(url, params=params, headers=headers, json=payload, timeout=self.timeout)
        resp.raise_for_status()
        
        data = resp.json()
        if data.get("code") != 0:
            raise RuntimeError(f"查找用户失败: {data.get('msg')}。请确保应用已开通'通讯录:联系人:只读'权限")
            
        user_list = data.get("data", {}).get("user_list", [])
        if not user_list:
            identifier = self.email or self.mobile
            raise ValueError(f"未找到 {identifier} 对应的飞书用户")
            
        user_info = user_list[0]
        if "user_id" not in user_info:
             identifier = self.email or self.mobile
             raise ValueError(f"{identifier} 查找结果无效 (可能用户不存在或应用无权限)")
             
        self.user_id = user_info["user_id"]
        return self.user_id

    def push_keywords(self, keywords: Union[List[Dict], Dict[str, List]]) -> bool:
        """
        Push extracted keywords to Feishu.
        Supports both legacy list format and new structured dict format.
        """
        if not keywords:
            return False

        current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        # Prepare message content based on structure
        if isinstance(keywords, dict) and ("skills_interests" in keywords or "tools_platforms" in keywords):
            # New structured format
            skills = keywords.get("skills_interests", [])
            tools = keywords.get("tools_platforms", [])
            
            content_lines = [
                f"📊 **User Interest Analysis Report**",
                f"🕒 Time: {current_time}",
                "",
                "🎯 **Skills & Interests (The What)**"
            ]
            
            if skills:
                for i, kw in enumerate(skills, 1):
                    content_lines.append(f"{i}. {kw['name']} (Weight: {kw['weight']:.2f})")
            else:
                content_lines.append("No significant skills detected.")
                
            content_lines.append("")
            content_lines.append("🛠️ **Tools & Platforms (The Via)**")
            
            if tools:
                for i, kw in enumerate(tools, 1):
                    content_lines.append(f"{i}. {kw['name']} (Weight: {kw['weight']:.2f})")
            else:
                content_lines.append("No significant tools detected.")
                
            text_content = "\n".join(content_lines)
            
        else:
            # Legacy list format
            content_lines = [
                f"📊 **User Interest Analysis Report**",
                f"🕒 Time: {current_time}",
                "",
                "🔑 **Top Keywords**"
            ]
            
            for i, kw in enumerate(keywords, 1):
                content_lines.append(f"{i}. {kw['name']} (Weight: {kw['weight']:.2f})")
                
            text_content = "\n".join(content_lines)

        if self.mode == "bot":
            return self._push_as_bot(text_content)
        else:
            return self._push_as_app(text_content)

    def _push_as_bot(self, text: str) -> bool:
        """使用群机器人推送消息"""
        if not self.webhook_url:
            raise ValueError("群机器人模式需要配置 webhook_url")

        payload = {
            "msg_type": "text",
            "content": {"text": text},
        }

        resp = requests.post(self.webhook_url, json=payload, timeout=self.timeout)
        resp.raise_for_status()
        return True

    def _push_as_app(self, text: str) -> bool:
        """使用应用消息推送"""
        if not self.app_id or not self.app_secret:
            raise ValueError("应用消息模式需要配置 APP_ID 和 APP_SECRET")

        token = self._get_tenant_access_token()
        user_id = self._get_user_id()

        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=utf-8"
        }

        params = {"receive_id_type": "open_id"}
        payload = {
            "receive_id": user_id,
            "msg_type": "text",
            "content": json.dumps({"text": text})
        }

        resp = requests.post(self.MESSAGE_URL, params=params, headers=headers, json=payload, timeout=self.timeout)
        resp.raise_for_status()
        return True

    @staticmethod
    def _format_keywords(keywords: List[Dict]) -> str:
        if not keywords:
            return "关键词列表为空"

        items = []
        for kw in keywords[:20]:
            name = kw.get("name", "")
            weight = kw.get("weight", kw.get("final_weight", 0.0))
            items.append(f"- {name} ({weight:.2f})")

        return "关键词 (Top 20):\n" + "\n".join(items)


def create_feishu_pusher(mode: str = "bot", **kwargs) -> FeishuPusher:
    """
    工厂函数：创建飞书推送器

    Usage:
        # 群机器人模式
        pusher = create_feishu_pusher(
            mode="bot",
            webhook_url="https://open.feishu.cn/open-apis/bot/v2/hook/xxx"
        )

        # 应用消息模式（从环境变量读取配置）
        pusher = create_feishu_pusher(mode="app")
        pusher.set_user_id("ou_xxx")  # 动态设置用户ID

        # 应用消息模式（直接传入配置）
        pusher = create_feishu_pusher(
            mode="app",
            app_id="cli_xxx",
            app_secret="xxx",
            user_id="ou_xxx"
        )
    """
    return FeishuPusher(mode=mode, **kwargs)

__all__ = ["FeishuInboxService"]

try:
    from .inbox import FeishuInboxService
except Exception:
    FeishuInboxService = None

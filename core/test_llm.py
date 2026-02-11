#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""测试LLM调用和数据检查"""

import os
import sys
from collections import defaultdict

# 设置UTF-8输出
if sys.platform == 'win32':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

from storage.event_store import EventStore
from llm.llm_client import LLMClient
from pusher.feishu_pusher import FeishuPusher
from config import AppConfig
from cleaner.data_cleaner import DataCleaner
from analysis.auditor import annotate_keywords

# 加载配置
config = AppConfig.from_file('config.json')

# 检查命令行参数
import sys
if '-test' in sys.argv:
    print('[INFO] Running in TEST mode (Manual Trigger)')
    # 这里其实已经是手动触发脚本了，所以主要就是个提示
    # 如果未来test_llm.py也加入定时逻辑，这里可以用来跳过

llm_config = config.llm_config()
keyword_min = llm_config.get('keyword_min', 10)
keyword_max = llm_config.get('keyword_max', 10)

print('='*60)
print('Task 2: Check data exists and conforms to standards')
print('='*60)

# 使用脚本所在目录查找数据库，确保在任何目录运行都能找到
base_dir = os.path.dirname(os.path.abspath(__file__))
db_path = os.path.join(base_dir, 'local_events.db')

if os.path.exists(db_path):
    store = EventStore(db_path)
    events = store.read_events(days=7)
    print(f'[OK] Database file exists: {db_path}')
    print(f'[INFO] Total events: {len(events)}')
    
    if events:
        type_counts = {}
        for e in events:
            type_counts[e.event_type] = type_counts.get(e.event_type, 0) + 1
        print(f'[INFO] Event type distribution: {type_counts}')
        print(f'[INFO] Data validation:')
        print(f'   - All have event_type: {all(e.event_type for e in events)}')
        print(f'   - duration>0: {all(e.duration > 0 for e in events)}')
        print(f'   - timestamp valid: {all(e.timestamp for e in events)}')
    else:
        print('[WARNING] Database is empty, no event records')
        events = []
else:
    print(f'[ERROR] Database file not found: {db_path}')
    events = []

print()
print('='*60)
print('Task 3: Verify LLM service is callable')
print('='*60)

# 从.env加载配置
from dotenv import load_dotenv
load_dotenv('../.env', override=True)

api_key = os.getenv('DASHSCOPE_API_KEY')
model = 'qwen-max'
base_url = 'https://dashscope.aliyuncs.com/compatible-mode/v1'

if not api_key:
    # 尝试从Config获取
    key_map = {
        "zhipu": "ZHIPU_API_KEY",
        "doubao": "VOLCANO_API_KEY",
        "openai": "OPENAI_API_KEY",
        "openai_compat": "OPENAI_API_KEY",
        "deepseek": "DEEPSEEK_API_KEY",
        "dashscope": "DASHSCOPE_API_KEY"
    }
    # 从config.llm provider推断，但这里写死了provider='dashscope'
    # 为了保持test_llm的独立性，我们手动尝试获取
    api_key = config.get_env('DASHSCOPE_API_KEY')

print(f'Provider: dashscope')
print(f'Model: {model}')
print(f'API Key: {api_key[:10] if api_key else "(missing)"}...')

if not api_key:
    print('[ERROR] API Key not configured')
else:
    print('[OK] API Key configured')
    
    # 创建LLM客户端
    client = LLMClient(
        provider='dashscope',
        api_key=api_key,
        model=model,
        base_url=base_url
    )
    
    # ========================================
    # 使用真实数据压缩成LLM输入格式
    # ========================================
    print()
    print('='*60)
    print('Compressing real data for LLM input (using DataCleaner)...')
    print('='*60)
    
    # Use DataCleaner to compress data
    # Note: DataCleaner expects objects with timestamp, duration, event_type, url, title, app, status
    # LocalEvent (from EventStore) is compatible with ActivityWatchRecord fields required by DataCleaner
    try:
        compressed_data = DataCleaner.compress_data(events)
        
        # Structure adaptation for LLM Prompt if needed, or just use as is.
        # DataCleaner returns 'web', 'non_web_samples', 'meta'.
        # The prompt expects 'web' and 'apps'. Let's alias 'non_web_samples' to 'apps' or let LLM figure it out.
        # To be safe and consistent with prompt description, we can rename/restructure if needed.
        # But 'non_web_samples' contains 'window' which has 'app' field. LLM usually handles this well.
        
        web_count = len(compressed_data.get('web', {}))
        app_count = len(compressed_data.get('non_web_samples', {}).get('window', []))
        
        print(f'[INFO] Compressed data:')
        print(f'   - Web domains: {web_count}')
        print(f'   - Window samples: {app_count}')
        print(f'   - Meta: {compressed_data.get("meta")}')
        print()
        
    except Exception as e:
        print(f'[ERROR] Data compression failed: {e}')
        compressed_data = {}

    print()
    print('='*60)
    print(f'Task 4: LLM output to console (min={keyword_min}, max={keyword_max})')
    print('='*60)
    print('[SPEAK] Calling LLM to extract keywords...')
    print()
    
    try:
        result_data = client.extract_keywords(compressed_data, min_k=keyword_min, max_k=keyword_max)
        
        keywords = [] # For pusher compatibility
        
        if isinstance(result_data, dict):
            # Step 2A: Run Auditor
            print('[INFO] Running Step 2A: Auditor (annotate_keywords)...')
            audited_result = annotate_keywords(result_data, compressed_data)
            
            # Use audited result
            result_data = audited_result
            
            skills = result_data.get("skills_interests", [])
            tools = result_data.get("tools_platforms", [])
            
            print('[OK] LLM + Auditor call successful!')
            print()
            
            print(f'[LIST] Skills & Interests ({len(skills)}):')
            for i, kw in enumerate(skills, 1):
                level = kw.get('level', 'N/A').upper()
                ev = kw.get('evidence', {})
                print(f'   {i}. {kw["name"]} (weight: {kw["weight"]:.2f}) [{level}]')
                if ev:
                    print(f'      Evidence: {ev.get("support_count")} hits, {ev.get("duration_seconds", 0):.0f}s')
                
            print(f'\n[LIST] Tools & Platforms ({len(tools)}):')
            for i, kw in enumerate(tools, 1):
                level = kw.get('level', 'N/A').upper()
                ev = kw.get('evidence', {})
                print(f'   {i}. {kw["name"]} (weight: {kw["weight"]:.2f}) [{level}]')
                if ev:
                    print(f'      Evidence: {ev.get("support_count")} hits, {ev.get("duration_seconds", 0):.0f}s')
            
            keywords = result_data
            
        elif isinstance(result_data, list):
            # Old format fallback
            keywords = result_data
            print('[OK] LLM call successful!')
            print()
            print(f'[LIST] Extracted keywords ({len(keywords)} total):')
            for i, kw in enumerate(keywords, 1):
                print(f'   {i}. {kw["name"]} (weight: {kw["weight"]:.2f})')
        else:
            print(f"[ERROR] Unexpected result format: {type(result_data)}")
            keywords = []

    except Exception as e:
        print(f'[ERROR] LLM call failed: {e}')
        keywords = []

    # ========================================
    # Task 5: Push to Feishu
    # ========================================
    if keywords:
        print()
        print('='*60)
        print('Task 5: Push results to Feishu')
        print('='*60)
        
        from pusher.feishu_pusher import FeishuPusher
        
        # 优先检查是否有 webhook，如果有则使用 bot 模式（最简单）
        webhook_url = config.get_env("FEISHU_WEBHOOK_URL")
        app_id = config.get_env("FEISHU_APP_ID")
        
        try:
            if webhook_url:
                print('[INFO] Using Feishu Webhook mode')
                pusher = FeishuPusher(mode="bot", webhook_url=webhook_url)
                pusher.push_keywords(keywords)
                print('[OK] Pushed to Feishu via Webhook')
            elif app_id:
                print('[INFO] Using Feishu App mode')
                # 尝试获取 email 或 open_id 或 mobile
                email = config.get_env("FEISHU_EMAIL")
                open_id = config.get_env("FEISHU_OPEN_ID")
                mobile = config.get_env("FEISHU_MOBILES")
                app_secret = config.get_env("FEISHU_APP_SECRET")
                
                if not email and not open_id and not mobile:
                     print('[WARNING] FEISHU_APP_ID is set but FEISHU_EMAIL, FEISHU_MOBILES or FEISHU_OPEN_ID is missing.')
                     print('          Please set one of them in .env to enable App push.')
                else:
                    pusher = FeishuPusher(mode="app", app_id=app_id, app_secret=app_secret, email=email, user_id=open_id, mobile=mobile)
                    pusher.push_keywords(keywords)
                    print(f'[OK] Pushed to Feishu App (Target: {email or mobile or open_id})')
            else:
                print('[INFO] Feishu not configured (set FEISHU_WEBHOOK_URL or FEISHU_APP_ID in .env)')
                
        except Exception as e:
            print(f'[ERROR] Failed to push to Feishu: {e}')

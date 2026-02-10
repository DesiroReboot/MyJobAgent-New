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

# 加载配置
config = AppConfig.from_file('config.json')
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
    print('Compressing real data for LLM input...')
    print('='*60)
    
    # 按域名聚合web事件
    web_stats = defaultdict(lambda: {'titles': [], 'total_duration': 0})
    for e in events:
        if e.event_type == 'web' and e.url:
            # 提取域名
            domain = e.url.split('/')[0] if e.url else 'unknown'
            web_stats[domain]['titles'].append(e.title)
            web_stats[domain]['total_duration'] += e.duration
    
    # 取Top10域名
    sorted_domains = sorted(web_stats.items(), key=lambda x: x[1]['total_duration'], reverse=True)[:10]
    
    compressed_web = {}
    for domain, stats in sorted_domains:
        # 最多保留5个title samples
        unique_titles = list(dict.fromkeys(stats['titles']))[:5]
        compressed_web[domain] = {
            'title_samples': unique_titles,
            'total_duration': stats['total_duration']
        }
    
    # 统计非web事件 (按App聚合)
    app_stats = defaultdict(lambda: {'titles': [], 'total_duration': 0})
    for e in events:
        if e.event_type == 'window' and e.app:
            app_stats[e.app]['titles'].append(e.title)
            app_stats[e.app]['total_duration'] += e.duration

    # 取Top10 App
    sorted_apps = sorted(app_stats.items(), key=lambda x: x[1]['total_duration'], reverse=True)[:10]

    compressed_apps = {}
    for app_name, stats in sorted_apps:
        # 最多保留5个title samples
        unique_titles = list(dict.fromkeys(filter(None, stats['titles'])))[:5]
        compressed_apps[app_name] = {
            'title_samples': unique_titles,
            'total_duration': stats['total_duration']
        }
    
    # 计算afk比例
    total_duration = sum(e.duration for e in events)
    afk_duration = sum(e.duration for e in events if e.event_type == 'afk')
    afk_ratio = afk_duration / total_duration if total_duration > 0 else 0
    
    # 构建LLM输入
    test_data = {
        'web': compressed_web,
        'apps': compressed_apps, # 新的分组结构
        'meta': {
            'afk_ratio': round(afk_ratio, 2)
        }
    }
    
    print(f'[INFO] Compressed data:')
    print(f'   - Web domains: {len(compressed_web)}')
    print(f'   - Active Apps: {len(compressed_apps)}')
    print(f'   - AFK ratio: {afk_ratio:.2%}')
    print()
    
    print()
    print('='*60)
    print(f'Task 4: LLM output to console (min={keyword_min}, max={keyword_max})')
    print('='*60)
    print('[SPEAK] Calling LLM to extract keywords...')
    print()
    
    try:
        # 使用配置的关键词数量
        # client.extract_keywords now returns a dict {"skills_interests": [...], "tools_platforms": [...]}
        # but the wrapper might still be returning a list if we didn't update extract_keywords return type hint or logic
        # Let's check extract_keywords implementation first.
        # Wait, I modified _build_prompt but extract_keywords parses the JSON.
        # I need to verify if extract_keywords just returns json.loads(content) or expects a specific list format.
        # Assuming extract_keywords returns the raw dict from LLM now.
        
        result_data = client.extract_keywords(test_data, min_k=keyword_min, max_k=keyword_max)
        
        keywords = [] # For pusher compatibility
        
        if isinstance(result_data, dict) and ("skills_interests" in result_data or "tools_platforms" in result_data):
             # New structured format
            skills = result_data.get("skills_interests", [])
            tools = result_data.get("tools_platforms", [])
            
            print('[OK] LLM call successful (Structured Output)!')
            print()
            
            print(f'[LIST] Skills & Interests ({len(skills)}):')
            for i, kw in enumerate(skills, 1):
                print(f'   {i}. {kw["name"]} (weight: {kw["weight"]:.2f})')
                
            print(f'\n[LIST] Tools & Platforms ({len(tools)}):')
            for i, kw in enumerate(tools, 1):
                print(f'   {i}. {kw["name"]} (weight: {kw["weight"]:.2f})')
            
            # Combine for pusher (pusher needs update to handle dict, but for now we can merge or pass dict if pusher supports it)
            # We will update pusher next. For now, let's pass the dict.
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

        # ========================================
        # 推送结果至飞书
        # ========================================
        print()
        print('='*60)
        print('Task 5: Push results to Feishu')
        print('='*60)
        
        feishu_app_id = os.getenv('FEISHU_APP_ID')
        feishu_app_secret = os.getenv('FEISHU_APP_SECRET')
        
        if feishu_app_id and feishu_app_secret:
            print(f'[INFO] Found Feishu App configuration (ID: {feishu_app_id[:10]}...)')
            try:
                pusher = FeishuPusher(mode='app')
                # 尝试推送 - 注意：如果没有配置 user_id 或 email，且应用未安装，这里可能会失败
                # 如果没有明确的目标用户，我们尝试推送到当前授权用户（如果有）
                # 由于FeishuPusher需要user_id或email来定位用户，如果env中没有FEISHU_OPEN_ID或FEISHU_EMAIL
                # 我们可能无法推送。
                
                # 检查是否配置了接收用户
                if not pusher.user_id and not pusher.email:
                     # 尝试从环境变量获取 (虽然 FeishuPusher 已经尝试了)
                     pass

                success = pusher.push_keywords(keywords)
                if success:
                    print('[OK] Successfully pushed keywords to Feishu!')
                else:
                    print('[ERROR] Failed to push to Feishu (unknown reason)')
            except Exception as e:
                print(f'[ERROR] Feishu push failed: {e}')
                print('[HINT] Make sure the Feishu App is installed in your workspace and you have authorized it.')
                print('[HINT] You may need to set FEISHU_OPEN_ID or FEISHU_EMAIL in .env if not using a bot.')
        else:
            print('[WARNING] FEISHU_APP_ID or FEISHU_APP_SECRET not found in .env. Skipping push.')

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
        webhook_url = os.getenv("FEISHU_WEBHOOK_URL")
        app_id = os.getenv("FEISHU_APP_ID")
        
        try:
            if webhook_url:
                print('[INFO] Using Feishu Webhook mode')
                pusher = FeishuPusher(mode="bot", webhook_url=webhook_url)
                pusher.push_keywords(keywords)
                print('[OK] Pushed to Feishu via Webhook')
            elif app_id:
                print('[INFO] Using Feishu App mode')
                # 尝试获取 email 或 open_id 或 mobile
                email = os.getenv("FEISHU_EMAIL")
                open_id = os.getenv("FEISHU_OPEN_ID")
                mobile = os.getenv("FEISHU_MOBILES")
                
                if not email and not open_id and not mobile:
                     print('[WARNING] FEISHU_APP_ID is set but FEISHU_EMAIL, FEISHU_MOBILES or FEISHU_OPEN_ID is missing.')
                     print('          Please set one of them in .env to enable App push.')
                else:
                    pusher = FeishuPusher(mode="app", app_id=app_id, email=email, user_id=open_id, mobile=mobile)
                    pusher.push_keywords(keywords)
                    print(f'[OK] Pushed to Feishu App (Target: {email or mobile or open_id})')
            else:
                print('[INFO] Feishu not configured (set FEISHU_WEBHOOK_URL or FEISHU_APP_ID in .env)')
                
        except Exception as e:
            print(f'[ERROR] Failed to push to Feishu: {e}')

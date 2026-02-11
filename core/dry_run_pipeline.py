
import sys
import os
from datetime import datetime, timezone
from dataclasses import dataclass

# Ensure we can import core modules
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))

from core.cleaner.data_cleaner import DataCleaner
from core.analysis.auditor import annotate_keywords
from core.collectors.aw_collector import ActivityWatchRecord

def run_dry_run():
    print(">>> Starting End-to-End Dry Run (Mock Data -> Cleaner -> Auditor)")
    
    # 1. Mock Raw Data (ActivityWatchRecords)
    # We simulate a session where the user studied Python and used Docker briefly.
    now = datetime.now(timezone.utc)
    events = [
        # Python usage (Web) - Strong Signal
        ActivityWatchRecord(
            event_type="web", 
            url="https://docs.python.org/3/library/os.html", 
            title="os — Miscellaneous operating system interfaces — Python 3.9.7 documentation", 
            app="chrome.exe", 
            duration=1200, # 20 mins
            timestamp=now, 
            status="active"
        ),
        ActivityWatchRecord(
            event_type="web", 
            url="https://stackoverflow.com/questions/123/python-list", 
            title="How to use list comprehensions in Python? - Stack Overflow", 
            app="chrome.exe", 
            duration=600, # 10 mins
            timestamp=now, 
            status="active"
        ),
        # Docker usage (Window) - Medium Signal
        # Note: cmd.exe is blacklisted in DataCleaner, so we use VS Code here
        ActivityWatchRecord(
            event_type="window", 
            url="", 
            title="Dockerfile - MyProject - Visual Studio Code", 
            app="Code.exe", 
            duration=300, # 5 mins
            timestamp=now, 
            status="active"
        ),
        # Noise (should be cleaned or ignored)
        ActivityWatchRecord(
            event_type="window", 
            url="", 
            title="New Tab", 
            app="chrome.exe", 
            duration=50, 
            timestamp=now, 
            status="active"
        ),
    ]
    
    print(f"[-] Raw Events: {len(events)}")
    
    # 2. Run Data Cleaner
    print("[-] Running DataCleaner.compress_data...")
    compressed_data = DataCleaner.compress_data(events)
    
    # DEBUG: Print compressed data keys and some content
    import json
    print("DEBUG: Web Domains:", list(compressed_data.get('web', {}).keys()))
    # print(json.dumps(compressed_data, indent=2, default=str))

    # Quick check of what the cleaner produced for Python
    web_data = compressed_data.get("web", {})
    python_domain = "docs.python.org"
    if python_domain in web_data:
        print(f"    [Cleaner Check] Found {python_domain} with duration {web_data[python_domain]['dur']['active_seconds']}s")
        print(f"    [Cleaner Check] Titles: {web_data[python_domain].get('title_freq')}")
    else:
        print(f"    [Cleaner Check] {python_domain} domain stats not found (might be normalized differently)")

    so_domain = "stackoverflow.com"
    if so_domain in web_data:
        print(f"    [Cleaner Check] Found {so_domain} with duration {web_data[so_domain]['dur']['active_seconds']}s")
        print(f"    [Cleaner Check] Titles: {web_data[so_domain].get('title_freq')}")

    # 3. Mock LLM Output
    # The LLM sees the compressed data and suggests these keywords
    mock_keywords = [
        {"name": "Python", "category": "Skill"},     # Expected: PASS (Strong evidence)
        {"name": "Docker", "category": "Tool"},      # Expected: PASS or WEAK (Evidence exists)
        {"name": "Kubernetes", "category": "Skill"}, # Expected: REJECT (Hallucination - No events)
    ]
    print(f"[-] Mock LLM Candidates: {[k['name'] for k in mock_keywords]}")

    # 4. Run Auditor
    print("[-] Running Auditor (annotate_keywords)...")
    # Note: consistency_runs is None, so it will self-check against the input list (consistency=1.0)
    results = annotate_keywords(mock_keywords, compressed_data)
    
    # 5. Output Results
    print("\n>>> Final Audit Results:")
    for res in results:
        level = res.get('level', 'unknown').upper()
        name = res.get('name')
        scores = res.get('scores', {})
        evidence = res.get('evidence', {})
        
        print(f"[{level}] {name}")
        print(f"    Scores:   Evidence={scores.get('evidence')}, Consistency={scores.get('consistency')}")
        print(f"    Evidence: Count={evidence.get('support_count')}, Dur={evidence.get('duration_seconds')}s")

if __name__ == "__main__":
    run_dry_run()

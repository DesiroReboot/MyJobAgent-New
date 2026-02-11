
import sys
import os
import json

# Ensure we can import core modules
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))

from core.analysis.auditor import annotate_keywords

def run_integration_test():
    print(">>> Starting Step 2A Auditor Integration Test")

    # 1. Mock Compressed Data (User Behavior)
    # Scenario:
    # - High usage: "Python" (VS Code, Docs)
    # - Medium usage: "Docker" (CLI, Docs)
    # - Noise/No usage: "React" (Not in data)
    mock_data = {
        "web": {
            "chrome.exe": {
                "title_freq": {
                    "Python 3.9 Documentation": 50,
                    "Stack Overflow - Python list comprehension": 20,
                    "Docker Hub": 15
                },
                "dur": {
                    "active_seconds": 3600  # 1 hour total
                }
            }
        },
        "non_web_samples": {
            "window": [
                {"title": "main.py - MyJobAgent - Visual Studio Code", "duration": 1800},
                {"title": "cmd.exe - docker ps", "duration": 300},
                {"title": "Spotify", "duration": 120}
            ]
        }
    }

    # 2. Mock LLM Candidates (Input)
    # - "Python": Strong evidence (Web + VS Code) -> Should PASS
    # - "Docker": Medium evidence (Web + Cmd) -> Should PASS or WEAK (depending on threshold)
    # - "React": Hallucination (No evidence) -> Should REJECT
    # - "Spotify": Non-skill (Present but maybe low semantic relevance, but Auditor only checks evidence presence first) -> Should PASS (Auditor measures 'Truth', semantic filtering is LLM's job or Consistency's job)
    
    mock_keywords = [
        {"name": "Python", "category": "Skill"},
        {"name": "Docker", "category": "Tool"},
        {"name": "React", "category": "Skill"}, # Hallucination
    ]

    # 3. Run Auditor
    print(f"[-] Input Keywords: {[k['name'] for k in mock_keywords]}")
    print("[-] Running annotate_keywords...")
    
    # We simulate consistency_runs being identical to input for simplicity, 
    # meaning LLM is consistently saying these are the skills.
    consistency_runs = [["Python", "Docker", "React"], ["Python", "Docker", "React"]]
    
    results = annotate_keywords(mock_keywords, mock_data, consistency_runs)

    # 4. Verify Results
    print("\n>>> Audit Results:")
    for res in results:
        name = res.get("name")
        level = res.get("level")
        evidence = res.get("evidence", {})
        scores = res.get("scores", {})
        
        print(f"[{level.upper()}] {name}")
        print(f"    Evidence: Count={evidence.get('support_count')}, Dur={evidence.get('duration_seconds'):.1f}s")
        print(f"    Scores:   Ev={scores.get('evidence')}, Con={scores.get('consistency')}")

        # Assertions
        if name == "Python":
            if level != "pass":
                print(f"[FAIL] Python should be PASS, got {level}")
            else:
                print("[OK] Python is PASS")
        
        if name == "React":
            if level != "reject":
                print(f"[FAIL] React should be REJECT (no evidence), got {level}")
            else:
                print("[OK] React is REJECT")

if __name__ == "__main__":
    run_integration_test()

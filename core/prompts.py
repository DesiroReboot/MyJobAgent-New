import json
from typing import Dict, Optional

def build_keyword_extraction_prompt(
    compressed_data: Dict,
    min_k: int,
    max_k: int,
    skills_limit: Optional[int] = None,
    tools_limit: Optional[int] = None,
) -> str:
    """
    Constructs the prompt for extracting keywords from user activity data.
    """
    data_preview = json.dumps(compressed_data, ensure_ascii=False, indent=2)[:8000]
    if skills_limit is None:
        skills_limit = max_k // 2
    if tools_limit is None:
        tools_limit = max_k // 2
    return (
        "You are an expert career counselor. Analyze the provided user activity data to identify professional skills and interests.\n"
        "The data is grouped into 'web' (browser history) and 'apps' (active applications).\n\n"
        "**Goal**: Extract high-quality insights, STRICTLY separating 'Content/Skills' (The What) from 'Tools/Platforms' (The Via).\n\n"
        "**Inference Logic (Contextual Analysis)**:\n"
        "- **For Apps**: The 'App Name' is the Container/Tool. The 'Titles' are High-Value Context Tags (logic/code/docs).\n"
        "  - **Filtered Data**: The input titles have already been filtered to exclude low-value files (configs, logs, readmes). Trust the list.\n"
        "  - **Inference**: Use the remaining titles to infer the specific skill. If an App has no titles, it was likely used for generic/low-value tasks; record it as a Tool but infer no Skill from it.\n"
        "  - **AI Context**: If the App is an AI IDE (e.g., Trae, Cursor) and has code files, infer 'AI-Assisted Development' + the language skill.\n"
        "  - Example: App='Trae AI', Titles=['main.py'] -> Skill='Python Development', Tool='Trae AI'.\n"
        "- **For Web**: The 'Domain' is the Platform/Tool. The 'Titles' reveal the Interest/Skill.\n"
        "  - Example: Domain='github.com', Title='react-repo' -> Skill='React', Tool='GitHub'.\n\n"
        "**Categories**:\n"
        "1. **Skills & Interests (The What)**: Technical concepts, programming languages, fields of study, or job roles.\n"
        "   - EXAMPLES: 'Python', 'Data Analysis', 'Workflow Automation', 'Machine Learning', 'Product Management'\n"
        "2. **Tools & Platforms (The Via)**: Software applications, websites, browsers, or services used to perform the activity.\n"
        "   - EXAMPLES: 'Visual Studio Code', 'GitHub', 'BOSS直聘', '知乎', 'n8n.io', 'Chrome', 'Feishu', 'Trae AI'\n\n"
        "**Rules**:\n"
        "1. **Ignore Noise**: Strictly ignore system process names (e.g., 'exe', 'msedge', 'cmd') and generic terms (e.g., 'Home', 'Search').\n"
        "2. **Container Constraint**: If a Tool is a generic container (Browser/IDE/Terminal), list it ONLY under 'Tools & Platforms', NEVER under 'Skills & Interests'.\n"
        "3. **Merge Concepts**: If you see 'Python 3.9' and 'Python Script', output 'Python'.\n"
        "4. **No Overlap**: A keyword cannot exist in both categories. Decide which one fits best.\n\n"
        f"**Output Requirement**:\n"
        f"- Return JSON only with this structure:\n"
        "  {\n"
        "    \"skills_interests\": [{\"name\": str, \"weight\": float}],\n"
        "    \"tools_platforms\": [{\"name\": str, \"weight\": float}]\n"
        "  }\n"
        "- Weights (0.0-1.0) should reflect relevance/duration.\n"
        f"- Limits: Skills & Interests Top {skills_limit}; Tools & Platforms Top {tools_limit}.\n\n"
        f"DATA:\n{data_preview}"
    )

from typing import List, Dict

from pyecharts.charts import WordCloud
from pyecharts import options as opts


class WordCloudGenerator:
    def generate(self, keywords: List[Dict], output_file: str) -> None:
        if isinstance(keywords, dict):
            # Flatten structured keywords
            flat_keywords = []
            if "skills_interests" in keywords:
                flat_keywords.extend(keywords.get("skills_interests", []))
            if "tools_platforms" in keywords:
                flat_keywords.extend(keywords.get("tools_platforms", []))
            keywords = flat_keywords

        data = [(kw.get("name", ""), float(kw.get("weight", 0.5)) * 100) for kw in keywords]
        wc = (
            WordCloud()
            .add("keywords", data, word_size_range=[12, 80])
            .set_global_opts(title_opts=opts.TitleOpts(title="JobInsight Keywords"))
        )
        wc.render(output_file)

from typing import List, Dict

from pyecharts.charts import WordCloud
from pyecharts import options as opts


class WordCloudGenerator:
    def generate(self, keywords: List[Dict], output_file: str) -> None:
        data = [(kw.get("name", ""), float(kw.get("weight", 0.5)) * 100) for kw in keywords]
        wc = (
            WordCloud()
            .add("keywords", data, word_size_range=[12, 80])
            .set_global_opts(title_opts=opts.TitleOpts(title="JobInsight Keywords"))
        )
        wc.render(output_file)

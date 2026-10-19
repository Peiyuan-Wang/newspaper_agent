"""Static experiment charts and a self-contained HTML report; no web dependencies."""
from html import escape
from pathlib import Path
import os

COLORS={'clean':'#137d74','noise20':'#d9822b','opens':'#5675bc','shift':'#ad5282',
        'static':'#66717d','theory':'#137d74','materials':'#d9822b',
        'reliability':'#5675bc','consumer':'#ad5282','cooking':'#87929a'}
NAMES={'clean':'正常赞踩','noise20':'20%误点','opens':'仅打开','shift':'偏好反转'}


def chart(result):
    data=result['aggregate']
    def series(condition,key,label,color):
        return label,color,[(r['step'],r[key]['mean'],r[key]['ci95']) for r in data if r['condition']==condition]
    def four(key):
        return [series(c,key,NAMES[c],COLORS[c]) for c in NAMES]
    panels=[('01  未见材料命中率 P@10',1,four('precision')+[series('clean','baseline_precision','不学习',COLORS['static'])]),
            ('02  融合推荐命中率 P@10',1,four('hybrid_precision')+[series('clean','baseline_hybrid_precision','不学习',COLORS['static'])]),
            ('03  最大兴趣向量偏转角（度）',25,four('angle')),
            ('04  正常赞踩：不同内容的平均语义分',100,[series('clean','score_'+t,n,COLORS[t]) for t,n in
                [('theory','理论方法'),('materials','材料实验'),('reliability','Agent可靠性'),('consumer','消费产品'),('cooking','烹饪')]]),
            ('05  偏好反转后的 nDCG@10',1,[series('shift','ndcg','反馈学习',COLORS['shift']),
                series('shift','baseline_ndcg','不学习',COLORS['static'])]),
            ('06  前十条细分主题熵（bit）',2.4,four('topic_entropy')+[series('clean','baseline_topic_entropy','不学习',COLORS['static'])])]
    elements=['<svg xmlns="http://www.w3.org/2000/svg" width="1500" height="1410" viewBox="0 0 1500 1410">',
        '<rect width="1500" height="1410" fill="#f3f6f6"/>',
        '<g font-family="Microsoft YaHei" fill="#203c43">',
        '<text x="45" y="48" font-size="27" font-weight="bold">兴趣反馈演化 · 离线对照实验</text>',
        '<text x="45" y="79" font-size="16">真实本地 embedding / 虚构材料与反馈 / 20 个固定种子 / 阴影为均值的近似 95% 区间</text>']
    # Label is parameterized so non-default reproduction runs remain honest.
    elements[-1]=elements[-1].replace('20 个',f"{result['seeds']} 个")
    for index,(title,maximum,lines) in enumerate(panels):
        x=30+(index%2)*735; y=110+(index//2)*420
        left,top,width,height=x+64,y+105,625,245
        elements.append(f'<rect x="{x}" y="{y}" width="715" height="400" rx="14" fill="white"/>')
        elements.append(f'<text x="{x+22}" y="{y+34}" font-size="19" font-weight="bold">{escape(title)}</text>')
        for i,(name,color,points) in enumerate(lines):
            lx=x+22+(i%3)*222;ly=y+59+(i//3)*24
            elements.extend([f'<path d="M{lx},{ly}h20" stroke="{color}" stroke-width="3"/>',
                f'<text x="{lx+27}" y="{ly+5}" font-size="13">{escape(name)}</text>'])
        for tick in range(5):
            value=maximum*tick/4; ty=top+height-height*tick/4
            elements.extend([f'<path d="M{left},{ty}h{width}" stroke="#e4ebee"/>',
                f'<text x="{left-12}" y="{ty+4}" text-anchor="end" font-size="12">{value:g}</text>'])
        def xy(step,value): return left+width*step/64,top+height-height*max(0,min(maximum,value))/maximum
        for step in (0,16,32,48,64):
            tx=left+width*step/64
            elements.append(f'<text x="{tx}" y="{top+height+21}" text-anchor="middle" font-size="12">{step}</text>')
        for name,color,points in lines:
            upper=[xy(t,v+ci) for t,v,ci in points];lower=[xy(t,v-ci) for t,v,ci in reversed(points)]
            polygon=' '.join(f'{px:.2f},{py:.2f}' for px,py in upper+lower)
            points_text=' '.join(f'{px:.2f},{py:.2f}' for px,py in [xy(t,v) for t,v,ci in points])
            elements.append(f'<polygon points="{polygon}" fill="{color}" opacity="0.10"/>')
            elements.append(f'<polyline points="{points_text}" stroke="{color}" stroke-width="2.5" fill="none"/>')
        elements.append(f'<text x="{left+width/2}" y="{top+height+43}" text-anchor="middle" font-size="13">累计展示材料数（仅反转场景在第32条后改变偏好）</text>')
    elements.extend(['<text x="45" y="1385" font-size="14">命中率依据预设偏好标签，不是实测 CTR；主题熵不代表质量；阴影不代表真实用户效果置信区间。</text>','</g></svg>'])
    return '\n'.join(elements)


def write_report(output,result):
    output=Path(output)
    svg=chart(result);(output/'charts.svg').write_text(svg,encoding='utf-8')
    os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
    from PySide6.QtGui import QGuiApplication,QImage,QPainter,QFontDatabase
    from PySide6.QtSvg import QSvgRenderer
    app=QGuiApplication.instance() or QGuiApplication([])
    if Path('C:/Windows/Fonts/msyh.ttc').is_file():
        QFontDatabase.addApplicationFont('C:/Windows/Fonts/msyh.ttc')
    renderer=QSvgRenderer(svg.encode('utf-8'))
    image=QImage(1500,1410,QImage.Format.Format_ARGB32);image.fill(0xffffffff)
    painter=QPainter(image);renderer.render(painter);painter.end()
    if not image.save(str(output/'charts.png')): raise RuntimeError('chart export failed')
    def at(condition,step):return next(r for r in result['aggregate'] if r['condition']==condition and r['step']==step)
    start=at('clean',0); end=at('clean',64)
    table=['| 场景 | 初始 P@10 | 末期 P@10 | 末期 nDCG@10 | 融合 P@10 | 最大偏转角均值 | 跳过反馈均值 |',
           '|---|---:|---:|---:|---:|---:|---:|']
    for key,name in NAMES.items():
        a,b=at(key,0),at(key,64)
        table.append(f"| {name} | {a['precision']['mean']:.1%} | {b['precision']['mean']:.1%} | "
            f"{b['ndcg']['mean']:.3f} | {b['hybrid_precision']['mean']:.1%} | {b['angle']['mean']:.2f}° | {b['skipped']['mean']:.1f} |")
    table.append(f"| 不学习（固定初始偏好） | {start['precision']['mean']:.1%} | {start['precision']['mean']:.1%} | "
        f"{start['ndcg']['mean']:.3f} | {start['hybrid_precision']['mean']:.1%} | 0° | — |")
    summary='\n'.join(table)
    control=result['controls']
    shifted32=at('shift',32);shifted64=at('shift',64)
    text=f'''# 兴趣向量反馈演化实验报告

## 结论与使用边界

正常赞踩条件下，未见评估材料的 P@10 从 **{start['precision']['mean']:.1%}** 变为 **{end['precision']['mean']:.1%}**，nDCG@10 从 **{start['ndcg']['mean']:.3f}** 变为 **{end['ndcg']['mean']:.3f}**。
接入当前 25% 语义残差后的融合 P@10 从 **{start['hybrid_precision']['mean']:.1%}** 变为 **{end['hybrid_precision']['mean']:.1%}**。正常反馈末期最大向量偏转角平均 **{end['angle']['mean']:.2f}°**，配置上限为 25°。

偏好反转当时 P@10 降到 **{shifted32['precision']['mean']:.1%}**，到第64条材料恢复至 **{shifted64['precision']['mean']:.1%}**。正常反馈下，前十条主题熵由 **{start['topic_entropy']['mean']:.3f}** 降到 **{end['topic_entropy']['mean']:.3f} bit**：相关性更强，但内容更集中，不等于已经解决信息茧房。

按反转后的新目标评估，不学习基线的语义P@10是 **{shifted64['baseline_precision']['mean']:.1%}**，融合P@10是 **{shifted64['baseline_hybrid_precision']['mean']:.1%}**；对应学习后的结果为 **{shifted64['precision']['mean']:.1%} / {shifted64['hybrid_precision']['mean']:.1%}**。不能把反转场景末期结果直接与旧目标的50%/51%比较。

这是实际代码和本地 384 维编码器运行的**合成控制实验**，不是线上真实用户评测。不能将结果当成产品准确率、CTR 或收益保证。所有条件和阈值预先固定，未按结果选择种子或调参。

{summary}

![反馈演化六面板](charts.png)

## 实验设计

- {result['articles']} 条虚构材料、五组内容：理论方法、材料实验、Agent可靠性、消费AI产品、烹饪；初始偏好喜欢理论方法与可靠性，不喜欢材料实验与消费产品。
- 实际使用 64 条独立反馈材料、40 条保留评估材料；另外16条域外训练材料不参与反馈。按细分主题切分训练与评估，ID和具体细分主题不交叉。它们仍共享人工主题模板与部分背景句，因此不是跨来源、跨时间的真实泛化测试。
- {result['seeds']} 个固定随机种子，每个条件7个检查点：0、4、8、16、32、48、64。曝光顺序是外部均匀打乱，不按当前推荐结果选样；避免把只给喜欢内容反馈的循环误当真实改善，但没有模拟真实曝光偏差。
- 对照：不学习；正常赞踩；每次票以20%概率反转；只对喜欢材料记弱打开、无打开不作负例；第32条之后只反转物理子兴趣，AI偏好不变。点击条件收到的观测少于展示材料数，不能与完整显式反馈视为同信息量。
- 评估只使用未反馈的40条材料，因此没有同篇赞踩±4分的捷径。融合使用正式代码的25%残差公式及±20上限；原AI分使用与目标标签独立的固定模拟分（领域内55±10、域外10），没有调用LLM评分，因此融合结果只检验此基线假设。
- 冻结编码器和生产路由/更新参数，不训练Wq/Wk/Wv。不改变用户画像或生产数据库，不联网、不调用聊天API。输入特征沿用正式标题、摘要分片及标签混合路径。

## 指标如何理解

- **P@10**：前10条里符合预设偏好的比例，衡量推荐命中。
- **nDCG@10**：越靠前出现相关内容得分越高，0—1；这里采用二元相关标签。
- **融合 P@10**：按当前上线的关联性融合公式排序后的命中率，不是完整三栏四指标评测。
- **偏转角**：更新向量和初始锚点的夹角；并非越大越好。固定锚点和25°约束防止偏离用户声明方向。
- **细分主题熵**：前10条分布在多少种内容、是否均衡；可能随筛除不喜欢内容下降，不应单独最大化。
- **路由跳过数**：低于相似度门槛或跨大类归属不清的反馈不学习。这限制误更新，也会减慢适应。
- 图中阴影为跨种子均值±1.96×样本标准差/√种子数，只有模拟顺序和误点的不确定性；不是对真实用户群的统计推断。偏好反转条件第32步起评估目标也反转，前后数值不能当作固定目标学习曲线。

## 可撤回、隔离与遗忘检查

| 检查 | 实测值 | 含义 |
|---|---:|---|
| 同一反馈再次重建后的最大坐标变化 | {control['replay_max_delta']:.3g} | 不把一条反馈重复训练 |
| 全部撤回后的最大锚点误差 | {control['undo_max_delta']:.3g} | 可恢复初始画像 |
| 仅物理反馈对AI槽的最大坐标变化 | {control['other_category_max_delta']:.3g} | 大类隔离 |
| 单次点赞当日有效质量 | {control['positive_mass_now']:.3f} | 赞踩强度参考 |
| 180天后有效质量 | {control['positive_mass_after_180_days']:.3f} | 90天半衰期，两次减半 |

## Q、K、V在这里分别是什么

Q可类比兴趣锚点/兴趣向量，K是同一编码器得到的新闻向量；V在当前方法中就是同一个新闻向量。路由attention依赖**固定锚点**，赞踩决定方向和强度；重建向量是带先验与角度约束的Rocchio式统计更新，不是Transformer解码概率分布，也没有梯度训练编码器。

本次形成 {result['slots']} 个兴趣槽；news_KV.json保存全部新闻K及V指向K的引用，profile_trajectories.json保存种子0各场景/检查点的完整兴趣向量。不把384维向量强行画成二维距离；偏转角直接在原坐标系计算。

## 局限与后续建议

1. 固定锚点路由和长期累计反馈让系统稳定，但突然反转偏好时，旧反馈与新反馈会抵消。应进一步做真实时间跨度的漂移评估，不能依据一次模拟就缩短半衰期或增大步长。
2. 当前语义路径不主动进行随机探索；图中的多样性来自候选库与偏好更新，不代表已经实现temperature探索或无偏bandit。
3. 通用embedding可能把材料实验与理论方法混在一起；需要真实新闻人工标注集检验细分区别。合成模板偏差、初始兴趣及模拟AI分都会影响本报告数值。
4. 本实验不评估热榜热度、新闻真实性或AI解读质量，也不评估真实点击率。下一阶段应在用户同意下做留出反馈回放，再做小规模线上对照。

## 复现与数据

运行：`.venv\\Scripts\\python.exe -X utf8 -m newspaper_agent.research.feedback_experiment --seeds {result['seeds']}`。
模型版本：`{result['encoder']}`；策略：`{result['policy']}`。
本机编码耗时 {result['encoding_seconds']} 秒；模拟耗时 {result['simulation_seconds']} 秒（机器负载相关，不是性能承诺）。聊天API调用 **0** 次。

- [每次运行指标 CSV](runs.csv)
- [聚合结果及均值区间 JSON](results.json)
- [新闻 K/V 特征](news_KV.json)
- [兴趣向量轨迹](profile_trajectories.json)
- [矢量图 SVG](charts.svg)
'''
    (output/'report.md').write_text(text,encoding='utf-8')
    # Self-contained, offline, no scripts or external assets.
    table_html='<table>'+''.join('<tr>'+''.join(f'<{"th" if i==0 else "td"}>{escape(c.strip())}</{"th" if i==0 else "td"}>'
        for c in row.strip('|').split('|'))+'</tr>' for i,row in enumerate(table) if i!=1)+'</table>'
    html=f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>兴趣反馈演化实验</title>
<style>body{{margin:0;background:#f3f6f6;color:#203c43;font:16px/1.8 "Microsoft YaHei",sans-serif}}main{{max-width:1200px;margin:36px auto;background:white;padding:40px;border-radius:18px}}svg{{width:100%;height:auto}}pre{{white-space:pre-wrap;font:inherit}}h1{{font-size:32px}}.note{{color:#697d83}}table{{border-collapse:collapse;width:100%;font-size:14px}}td,th{{padding:12px;border-bottom:1px solid #dee7e6;text-align:left}}th{{background:#eef5f3}}.cards{{display:flex;gap:20px}}.card{{flex:1;padding:20px;background:#eef5f3;border-radius:12px}}strong{{font-size:28px;color:#137d74}}a{{color:#137d74}}</style>
<main><p class="note">知闻 / ALGORITHM LAB / 2026-10-19</p><h1>反馈能否让推荐更懂用户？</h1>
<p>真实本地编码器，{result['seeds']}个固定种子，64条反馈材料与40条独立评估材料。<b>合成控制实验，不等于真实用户效果。</b></p>
<div class="cards"><div class="card">融合命中率<br><strong>{start['hybrid_precision']['mean']:.0%} → {end['hybrid_precision']['mean']:.0%}</strong><br>正常赞踩 / 模拟AI基分</div>
<div class="card">向量最大偏转角均值<br><strong>{end['angle']['mean']:.2f}°</strong><br>限制在初始方向25°内</div>
<div class="card">付费模型调用<br><strong>0</strong><br>画像及正文均为虚构材料</div></div>
<h2>对照结果</h2>{table_html}
<p>偏好反转后命中率先降至{shifted32['precision']['mean']:.1%}，随后恢复至{shifted64['precision']['mean']:.1%}。
正常反馈的主题熵从{start['topic_entropy']['mean']:.3f}降至{end['topic_entropy']['mean']:.3f} bit，说明内容更集中；当前实现没有主动探索机制。</p>
{svg}<h2>如何理解这些结果</h2><p>这是带固定锚点的向量偏好更新，不训练Transformer。Q类比兴趣，K是新闻向量，V与K相同。
图中阴影只表示随机顺序和误点带来的均值不确定性，不是对真实用户群的保证。模板材料较容易区分，融合评分的原AI分是模拟值。</p>
<p><a href="report.md">完整方法与局限（Markdown）</a> · <a href="runs.csv">逐次指标CSV</a> · <a href="results.json">聚合JSON</a> · <a href="charts.png">下载图表</a> · <a href="news_KV.json">新闻K/V</a></p>
<details><summary>展开完整实验记录</summary><pre>{escape(text.replace('![反馈演化六面板](charts.png)',''))}</pre></details></main></html>'''
    (output/'report.html').write_text(html,encoding='utf-8')

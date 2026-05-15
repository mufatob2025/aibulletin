"""
AIBulletin.news — fetch_news.py
Uses Google Gemini FREE API to process articles.
Runs every hour via GitHub Actions.
"""

import os, json, time, hashlib, feedparser, re
from datetime import datetime, timezone
from pathlib import Path
import google.generativeai as genai

SITE_NAME        = "AI Bulletin"
SITE_TAGLINE     = "Breaking AI News & Intelligence"
SITE_DOMAIN      = "https://www.aibulletin.news"
SITE_TWITTER     = "@aibulletinnews"
SITE_DESCRIPTION = (
    "Breaking AI news, research and analysis updated every hour. "
    "Covering artificial intelligence, machine learning, major model releases, "
    "funding rounds, policy and the people shaping the future of AI."
)

RSS_FEEDS = [
    "https://techcrunch.com/category/artificial-intelligence/feed/",
    "https://venturebeat.com/category/ai/feed/",
    "https://www.wired.com/feed/tag/ai/rss",
    "https://www.theverge.com/ai-artificial-intelligence/rss/index.xml",
    "https://www.technologyreview.com/feed/",
    "https://www.artificialintelligence-news.com/feed/",
]

MAX_PER_FEED = 8
MAX_TOTAL    = 40
SEEN_FILE    = "seen_ids.json"
ARCHIVE_FILE = "articles.json"

def slug(text):
    s = text.lower()
    s = re.sub(r'[^a-z0-9\s-]', '', s)
    s = re.sub(r'\s+', '-', s.strip())
    return re.sub(r'-+', '-', s)[:80]

def generate_sitemap(articles):
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    urls = [f"""  <url>
    <loc>{SITE_DOMAIN}/</loc>
    <lastmod>{now}</lastmod>
    <changefreq>hourly</changefreq>
    <priority>1.0</priority>
  </url>
  <url>
    <loc>{SITE_DOMAIN}/about.html</loc>
    <lastmod>{now}</lastmod>
    <changefreq>monthly</changefreq>
    <priority>0.5</priority>
  </url>
  <url>
    <loc>{SITE_DOMAIN}/privacy.html</loc>
    <lastmod>{now}</lastmod>
    <changefreq>monthly</changefreq>
    <priority>0.3</priority>
  </url>"""]
    for a in articles:
        urls.append(f"""  <url>
    <loc>{SITE_DOMAIN}/#{slug(a.get('headline',''))}</loc>
    <lastmod>{now}</lastmod>
    <changefreq>daily</changefreq>
    <priority>0.8</priority>
  </url>""")
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        + "\n".join(urls) + "\n</urlset>"
    )

def robots_txt():
    return f"User-agent: *\nAllow: /\nDisallow: /admin.html\nSitemap: {SITE_DOMAIN}/sitemap.xml\n"

def json_ld(article):
    pub = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return json.dumps({
        "@context": "https://schema.org",
        "@type": "NewsArticle",
        "headline": article.get("headline", ""),
        "description": article.get("meta_description", article.get("summary", ""))[:155],
        "datePublished": pub,
        "dateModified": pub,
        "author": {"@type": "Organization", "name": SITE_NAME},
        "publisher": {
            "@type": "Organization",
            "name": SITE_NAME,
            "logo": {"@type": "ImageObject", "url": f"{SITE_DOMAIN}/logo.png"}
        },
        "mainEntityOfPage": {
            "@type": "WebPage",
            "@id": f"{SITE_DOMAIN}/#{slug(article.get('headline',''))}"
        },
        "articleSection": article.get("tag", "AI News"),
        "keywords": article.get("focus_keyword", ""),
        "url": article.get("link", SITE_DOMAIN),
        "isAccessibleForFree": True
    })

def art_id(entry):
    url = getattr(entry, "link", "") or getattr(entry, "id", "")
    return hashlib.md5(url.encode()).hexdigest()

def load_seen():
    p = Path(SEEN_FILE)
    if p.exists():
        content = p.read_text().strip()
        if content and content != "[]":
            return set(json.loads(content))
    return set()

def save_seen(ids):
    Path(SEEN_FILE).write_text(json.dumps(list(ids)[-500:]))

def fetch_raw(seen):
    raw = []
    for url in RSS_FEEDS:
        try:
            print(f"   Fetching: {url}")
            feed = feedparser.parse(url)
            src  = feed.feed.get("title", url)
            count = 0
            for e in feed.entries[:MAX_PER_FEED]:
                aid = art_id(e)
                if aid in seen:
                    continue
                title   = e.get("title", "").strip()
                summary = e.get("summary", e.get("description", "")).strip()[:800]
                if not title:
                    continue
                raw.append({
                    "id":      aid,
                    "title":   title,
                    "summary": summary,
                    "link":    e.get("link", ""),
                    "source":  src,
                })
                count += 1
            print(f"   -> {count} new articles from {src}")
        except Exception as ex:
            print(f"   WARNING: Feed error {url}: {ex}")
    print(f"\n   Total raw articles: {len(raw)}")
    return raw[:MAX_TOTAL]

def is_ai(title, summary):
    kw = [
        "ai", "artificial intelligence", "machine learning", "llm",
        "gpt", "claude", "gemini", "openai", "deepmind", "neural",
        "chatbot", "large language", "generative", "transformer",
        "robot", "automation", "deep learning", "foundation model",
        "tech", "google", "microsoft", "meta", "nvidia",
        "startup", "funding", "billion", "model", "data", "algorithm",
        "software", "hardware", "chip", "cloud",
    ]
    text = (title + " " + summary).lower()
    return any(k in text for k in kw)

def process_with_gemini(model, article):
    prompt = f"""You are a senior editor at a technology news website.
Process this article and return ONLY a valid JSON object.
No markdown, no code fences, no explanation. Pure JSON only.

Article Title: {article['title']}
Source: {article['source']}
Content: {article['summary'][:500]}

Return this exact JSON:
{{
  "headline": "Rewritten headline under 12 words. Clear and factual.",
  "seo_title": "SEO title under 60 characters",
  "meta_description": "Description under 155 characters with main keyword",
  "summary": "2 sentence plain English summary of what happened",
  "takeaway": "One sentence: why this matters for AI and tech",
  "focus_keyword": "main 2-4 word keyword phrase",
  "secondary_keywords": ["keyword2", "keyword3"],
  "tag": "Breaking",
  "importance": 7,
  "read_time": 2,
  "is_ai_related": true
}}

For tag choose one of: Breaking, Hot, Research, Product, Opinion, Funding, Policy"""

    try:
        response = model.generate_content(prompt)
        text = response.text.strip()
        if "```" in text:
            parts = text.split("```")
            for part in parts:
                part = part.strip()
                if part.startswith("json"):
                    part = part[4:].strip()
                if part.startswith("{"):
                    text = part
                    break
        start = text.find("{")
        end   = text.rfind("}") + 1
        if start >= 0 and end > start:
            text = text[start:end]
        result = json.loads(text)
        print(f"   OK: {result.get('headline','')[:55]}")
        return result
    except Exception as ex:
        print(f"   WARNING: Gemini error: {ex} - using fallback")
        return {
            "headline":           article["title"][:100],
            "seo_title":          article["title"][:60],
            "meta_description":   article["summary"][:155],
            "summary":            article["summary"][:300],
            "takeaway":           "An important development in artificial intelligence.",
            "focus_keyword":      "AI news",
            "secondary_keywords": ["artificial intelligence", "tech news"],
            "tag":                "Hot",
            "importance":         6,
            "read_time":          2,
            "is_ai_related":      True
        }

TAG_STYLE = {
    "Breaking": ("background:#fee2e2;color:#991b1b", "#991b1b"),
    "Hot":      ("background:#ffedd5;color:#9a3412", "#9a3412"),
    "Research": ("background:#dbeafe;color:#1d4ed8", "#1d4ed8"),
    "Product":  ("background:#dcfce7;color:#166534", "#166534"),
    "Opinion":  ("background:#f3e8ff;color:#6b21a8", "#6b21a8"),
    "Funding":  ("background:#fef9c3;color:#854d0e", "#854d0e"),
    "Policy":   ("background:#e0f2fe;color:#0369a1", "#0369a1"),
}

def card(a, feat=False):
    tag    = a.get("tag","Hot")
    imp    = a.get("importance",5)
    heat   = "&#x25CF;" * min(imp,10) + "&#x25CB;" * (10-min(imp,10))
    ts, tc = TAG_STYLE.get(tag,("background:#e5e7eb;color:#374151","#374151"))
    sl     = slug(a.get("headline",""))
    rt     = a.get("read_time",2)
    fc     = " feat" if feat else ""
    pub    = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    hl     = a.get("headline","").replace('"','&quot;').replace('<','&lt;').replace('>','&gt;')
    sm     = a.get("summary","").replace('<','&lt;').replace('>','&gt;')
    tk     = a.get("takeaway","").replace('<','&lt;').replace('>','&gt;')
    src    = a.get("source","").replace('<','&lt;').replace('>','&gt;')
    lk     = a.get("link","#")
    st     = a.get("seo_title",hl).replace('"','&quot;')
    return f"""
      <article class="acard{fc}" id="{sl}" data-tag="{tag}" itemscope itemtype="https://schema.org/NewsArticle">
        <meta itemprop="datePublished" content="{pub}">
        <span class="ctag" style="{ts}">{tag}</span>
        <h2 class="chl" itemprop="headline">
          <a href="{lk}" target="_blank" rel="noopener" title="{st}">{hl}</a>
        </h2>
        <p class="csum" itemprop="description">{sm}</p>
        <p class="ctake">&#x2937; {tk}</p>
        <div class="cfoot">
          <span class="csrc">{src}</span>
          <span class="crt">{rt} min read</span>
          <span class="cheat" style="color:{tc}">{heat}</span>
        </div>
      </article>"""

def sidebar_item(a, rank):
    lk  = a.get("link","#")
    hl  = a.get("headline","").replace('"','&quot;').replace('<','&lt;').replace('>','&gt;')
    src = a.get("source","")
    tag = a.get("tag","")
    return f"""
      <div class="ss">
        <div class="ss-rank">{rank}</div>
        <div>
          <a class="ss-hl" href="{lk}" target="_blank" rel="noopener">{hl}</a>
          <div class="ss-src">{src} &middot; {tag}</div>
        </div>
      </div>"""

def ticker(articles):
    items = "".join(f'<span class="ti">{a.get("headline","").replace("<","&lt;").replace(">","&gt;")}</span>' for a in articles[:8])
    return items * 2

def trending_items(articles):
    return "".join(
        f'<div class="tri"><div class="tri-n">{i+1}</div>'
        f'<div class="tri-hl"><a href="{a.get("link","#")}" target="_blank">'
        f'{a.get("headline","").replace("<","&lt;").replace(">","&gt;")}</a></div></div>'
        for i,a in enumerate(articles[:5])
    )

CSS = """
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
:root{--ink:#0f0f0f;--ink2:#2c2c2c;--ink3:#555;--ink4:#888;--paper:#faf9f7;--paper2:#f3f1ed;--paper3:#e8e5df;--rule:#d8d4cc;--red:#c0172a;--red2:#8f1020;--navy:#0c1928;--serif:'Playfair Display',Georgia,serif;--sans:'Source Sans 3',sans-serif;--mono:'Courier Prime','Courier New',monospace}
html{background:var(--paper);color:var(--ink);font-family:var(--sans)}
body{min-height:100vh} a{color:inherit;text-decoration:none}
.ticker{background:var(--red);color:#fff;display:flex;align-items:center;overflow:hidden;height:36px;font-family:var(--mono);font-size:.72rem}
.tlabel{background:var(--red2);padding:0 1.1rem;height:100%;display:flex;align-items:center;font-weight:700;white-space:nowrap;flex-shrink:0;text-transform:uppercase;letter-spacing:.1em;gap:.5rem}
.tdot{width:7px;height:7px;background:#fff;border-radius:50%;animation:blink 1.2s ease-in-out infinite}
@keyframes blink{0%,100%{opacity:1}50%{opacity:.2}}
.twrap{overflow:hidden;flex:1}
.ttrack{display:flex;animation:scroll 45s linear infinite;white-space:nowrap}
.ttrack:hover{animation-play-state:paused}
.ti{padding:0 2.5rem}
.ti::before{content:"\\25C6";margin-right:1rem;opacity:.5;font-size:.5rem;vertical-align:middle}
@keyframes scroll{0%{transform:translateX(0)}100%{transform:translateX(-50%)}}
.masthead{background:var(--navy);padding:0 2rem;border-bottom:3px solid var(--red)}
.mh-top{display:flex;align-items:center;justify-content:space-between;padding:1rem 0 .6rem;border-bottom:1px solid rgba(255,255,255,.1)}
.mh-date{font-family:var(--mono);font-size:.68rem;color:rgba(255,255,255,.4);letter-spacing:.05em;text-transform:uppercase}
.mh-tag{font-family:var(--mono);font-size:.62rem;color:rgba(255,255,255,.3);letter-spacing:.08em;text-transform:uppercase}
.wordmark{font-family:var(--serif);font-size:3.2rem;font-weight:900;color:#fff;letter-spacing:-.04em;line-height:1;text-align:center;padding:.6rem 0}
.wordmark span{color:var(--red)}
.nav{display:flex;align-items:center;padding:.5rem 0;gap:0;flex-wrap:wrap}
.nl{font-family:var(--mono);font-size:.68rem;font-weight:700;letter-spacing:.1em;text-transform:uppercase;color:rgba(255,255,255,.55);padding:.4rem 1rem;transition:color .15s;cursor:pointer}
.nl:first-child{padding-left:0} .nl:hover,.nl.active{color:#fff} .nl.active{color:var(--red)}
.nsep{color:rgba(255,255,255,.15);font-size:.8rem}
.nav-pages{display:flex;align-items:center;gap:0;margin-left:auto}
.np{font-family:var(--mono);font-size:.62rem;color:rgba(255,255,255,.4);padding:.4rem .8rem;text-transform:uppercase;letter-spacing:.06em;transition:color .15s}
.np:hover{color:rgba(255,255,255,.8)}
.ad-banner{background:var(--paper2);display:flex;align-items:center;justify-content:center;min-height:90px;padding:.5rem;border-bottom:1px solid var(--rule)}
.wrap{max-width:1280px;margin:0 auto;padding:0 1.5rem}
.srule{display:flex;align-items:center;gap:1rem;padding:1.4rem 0 1rem;border-top:3px solid var(--ink)}
.srule h2{font-family:var(--mono);font-size:.7rem;font-weight:700;letter-spacing:.14em;text-transform:uppercase;color:var(--ink2);white-space:nowrap}
.rl{flex:1;height:1px;background:var(--rule)}
.rtag{font-family:var(--mono);font-size:.62rem;color:var(--red);font-weight:700;letter-spacing:.08em}
.hero{display:grid;grid-template-columns:1fr 340px;gap:2.5rem;padding-bottom:2rem;border-bottom:1px solid var(--rule)}
.htag{display:inline-block;background:var(--red);color:#fff;font-family:var(--mono);font-size:.62rem;font-weight:700;letter-spacing:.12em;text-transform:uppercase;padding:.25rem .6rem;margin-bottom:.9rem}
.hhl{font-family:var(--serif);font-size:2.6rem;font-weight:700;line-height:1.15;letter-spacing:-.02em;color:var(--ink);margin-bottom:1rem}
.hhl a{color:inherit} .hhl:hover a{color:var(--red)}
.hsum{font-family:var(--sans);font-size:1.05rem;line-height:1.75;color:var(--ink3);margin-bottom:1.1rem;max-width:640px}
.htake{font-family:var(--sans);font-size:.88rem;color:var(--ink3);border-left:3px solid var(--red);padding-left:.9rem;font-style:italic;line-height:1.6}
.hmeta{display:flex;align-items:center;gap:1rem;margin-top:1.2rem;padding-top:1rem;border-top:1px solid var(--rule)}
.hsrc{font-family:var(--mono);font-size:.68rem;color:var(--ink4);text-transform:uppercase;letter-spacing:.06em}
.himp{font-family:var(--mono);font-size:.62rem;color:var(--red);font-weight:700}
.hread{margin-left:auto;font-family:var(--mono);font-size:.68rem;font-weight:700;letter-spacing:.06em;text-transform:uppercase;color:var(--navy);border-bottom:2px solid var(--navy);padding-bottom:1px;transition:color .15s,border-color .15s}
.hread:hover{color:var(--red);border-color:var(--red)}
.hside{border-left:1px solid var(--rule);padding-left:2rem}
.hside-title{font-family:var(--mono);font-size:.65rem;font-weight:700;letter-spacing:.12em;text-transform:uppercase;color:var(--ink4);margin-bottom:1rem;padding-bottom:.5rem;border-bottom:1px solid var(--rule)}
.ss{display:flex;gap:.8rem;padding:.85rem 0;border-bottom:1px solid var(--paper3)}
.ss:last-child{border-bottom:none}
.ss-rank{font-family:var(--serif);font-size:1.4rem;font-weight:700;color:var(--rule);line-height:1;flex-shrink:0;width:24px}
.ss:hover .ss-rank{color:var(--red)}
.ss-hl{font-family:var(--serif);font-size:.88rem;font-weight:600;line-height:1.4;color:var(--ink);display:block}
.ss:hover .ss-hl{color:var(--red)}
.ss-src{font-family:var(--mono);font-size:.6rem;color:var(--ink4);margin-top:.3rem;text-transform:uppercase;letter-spacing:.05em}
.clayout{display:grid;grid-template-columns:1fr 300px;gap:2.5rem}
.agrid{display:grid;grid-template-columns:1fr 1fr;gap:0;border:1px solid var(--rule);border-bottom:none}
.acard{padding:1.3rem 1.4rem;border-bottom:1px solid var(--rule);border-right:1px solid var(--rule);transition:background .15s;cursor:pointer}
.acard:nth-child(even){border-right:none} .acard:hover{background:var(--paper2)}
.acard.feat{grid-column:1/-1;background:var(--paper2);border-right:none}
.ctag{display:inline-block;font-family:var(--mono);font-size:.6rem;font-weight:700;letter-spacing:.1em;text-transform:uppercase;padding:.18rem .5rem;border-radius:2px;margin-bottom:.6rem}
.chl{font-family:var(--serif);font-size:1.05rem;font-weight:600;line-height:1.38;color:var(--ink);margin-bottom:.55rem}
.acard.feat .chl{font-size:1.35rem} .acard:hover .chl{color:var(--red)} .chl a{color:inherit}
.csum{font-family:var(--sans);font-size:.82rem;color:var(--ink3);line-height:1.65;margin-bottom:.7rem}
.ctake{font-family:var(--sans);font-size:.78rem;color:var(--ink4);font-style:italic;line-height:1.5}
.cfoot{display:flex;align-items:center;justify-content:space-between;margin-top:.9rem;padding-top:.6rem;border-top:1px solid var(--paper3);gap:.5rem}
.csrc{font-family:var(--mono);font-size:.6rem;color:var(--ink4);text-transform:uppercase;letter-spacing:.06em}
.crt{font-family:var(--mono);font-size:.58rem;color:var(--ink4)}
.cheat{font-family:var(--mono);font-size:.5rem;letter-spacing:-1.5px;margin-left:auto}
.ad-inf{grid-column:1/-1;display:flex;align-items:center;justify-content:center;min-height:100px;padding:.5rem;border-bottom:1px solid var(--rule)}
.sw{margin-bottom:2rem;border:1px solid var(--rule)}
.sw-hd{background:var(--navy);color:#fff;font-family:var(--mono);font-size:.65rem;font-weight:700;letter-spacing:.12em;text-transform:uppercase;padding:.6rem 1rem}
.sw-body{padding:1rem}
.nl-copy{font-family:var(--sans);font-size:.82rem;color:var(--ink3);line-height:1.6;margin-bottom:.9rem}
.nl-form{display:flex;flex-direction:column;gap:.5rem}
.nl-input{font-family:var(--sans);font-size:.85rem;padding:.6rem .8rem;border:1px solid var(--rule);background:var(--paper);color:var(--ink);outline:none}
.nl-input:focus{border-color:var(--navy)}
.nl-btn{font-family:var(--mono);font-size:.68rem;font-weight:700;letter-spacing:.08em;text-transform:uppercase;padding:.65rem;background:var(--red);color:#fff;border:none;cursor:pointer;transition:background .15s}
.nl-btn:hover{background:var(--red2)}
.nl-note{font-family:var(--mono);font-size:.58rem;color:var(--ink4);line-height:1.5;margin-top:.4rem}
.tri{display:flex;align-items:flex-start;gap:.75rem;padding:.75rem 0;border-bottom:1px solid var(--paper3)}
.tri:last-child{border-bottom:none;padding-bottom:0}
.tri-n{font-family:var(--serif);font-size:1.3rem;font-weight:700;color:var(--paper3);line-height:1;width:22px;flex-shrink:0}
.tri:hover .tri-n{color:var(--red)}
.tri-hl{font-family:var(--serif);font-size:.82rem;font-weight:600;line-height:1.4;color:var(--ink)}
.tri-hl a{color:inherit} .tri:hover .tri-hl{color:var(--red)}
.af-item{display:flex;align-items:center;justify-content:space-between;padding:.7rem 0;border-bottom:1px solid var(--paper3);font-family:var(--sans)}
.af-item:last-child{border-bottom:none}
.af-name{font-size:.82rem;font-weight:600;color:var(--ink)} .af-desc{font-size:.72rem;color:var(--ink4)}
.af-link{font-family:var(--mono);font-size:.6rem;font-weight:700;letter-spacing:.06em;text-transform:uppercase;color:var(--navy);border-bottom:1px solid var(--navy);white-space:nowrap;transition:color .15s}
.af-link:hover{color:var(--red);border-color:var(--red)}
.ad-rect{display:flex;align-items:center;justify-content:center;min-height:250px;padding:.5rem}
footer{background:var(--navy);color:rgba(255,255,255,.5);margin-top:3rem;padding:2.5rem 2rem}
.fi{max-width:1280px;margin:0 auto;display:grid;grid-template-columns:1fr 1fr 1fr;gap:2rem}
.f-brand{font-family:var(--serif);font-size:1.6rem;font-weight:900;color:#fff;margin-bottom:.5rem}
.f-brand span{color:var(--red)}
.f-copy{font-family:var(--sans);font-size:.75rem;line-height:1.7}
.f-ct{font-family:var(--mono);font-size:.62rem;font-weight:700;letter-spacing:.1em;text-transform:uppercase;color:rgba(255,255,255,.3);margin-bottom:.75rem}
.f-link{display:block;font-family:var(--sans);font-size:.8rem;color:rgba(255,255,255,.55);margin-bottom:.4rem;transition:color .12s}
.f-link:hover{color:#fff}
.f-bot{max-width:1280px;margin:1.5rem auto 0;padding-top:1.2rem;border-top:1px solid rgba(255,255,255,.1);font-family:var(--mono);font-size:.6rem;color:rgba(255,255,255,.25);letter-spacing:.04em;display:flex;justify-content:space-between;flex-wrap:wrap;gap:.5rem}
@media(max-width:900px){.hero{grid-template-columns:1fr}.hside{border-left:none;border-top:1px solid var(--rule);padding-left:0;padding-top:1.5rem;margin-top:1rem}.clayout{grid-template-columns:1fr}.fi{grid-template-columns:1fr}.hhl{font-size:2rem}}
@media(max-width:600px){.wordmark{font-size:2.2rem}.agrid{grid-template-columns:1fr}.acard.feat{grid-column:1}}
"""

def build_html(articles):
    now_utc   = datetime.now(timezone.utc)
    now_str   = now_utc.strftime("%B %d, %Y - %H:%M UTC")
    date_disp = now_utc.strftime("%A, %B %d, %Y")
    count     = len(articles)
    hero      = articles[0] if articles else {
        "headline": "Loading latest AI news...",
        "summary": "Our editorial team is gathering the latest stories. Check back shortly.",
        "takeaway": "Updated every hour.",
        "tag": "Hot", "importance": 5, "source": "AI Bulletin", "link": "#"
    }
    sl0       = slug(hero.get("headline",""))
    meta_desc = hero.get("meta_description", SITE_DESCRIPTION)[:155]
    kw        = hero.get("focus_keyword","AI news")
    hero_hl   = hero.get("headline","").replace('"','&quot;').replace('<','&lt;').replace('>','&gt;')
    hero_sum  = hero.get("summary","").replace('<','&lt;').replace('>','&gt;')
    hero_take = hero.get("takeaway","").replace('<','&lt;').replace('>','&gt;')
    hero_src  = hero.get("source","")
    hero_link = hero.get("link","#")
    hero_st   = hero.get("seo_title", hero_hl).replace('"','&quot;')

    org_ld = json.dumps({
        "@context":"https://schema.org","@type":"NewsMediaOrganization",
        "name":SITE_NAME,"url":SITE_DOMAIN,"description":SITE_DESCRIPTION,
        "logo":{"@type":"ImageObject","url":f"{SITE_DOMAIN}/logo.png"}
    })

    art_ld_blocks = "\n".join(
        f'<script type="application/ld+json">\n{json_ld(a)}\n</script>'
        for a in articles[:5]
    )

    side_stories = "".join(sidebar_item(a,i+2) for i,a in enumerate(articles[1:5]))
    cards_html   = card(articles[1], feat=True) if len(articles) > 1 else ""
    cards_html  += '<div class="ad-inf"></div>'
    for a in articles[2:]:
        cards_html += card(a)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>AI Bulletin - {SITE_TAGLINE} | aibulletin.news</title>
  <meta name="description" content="{meta_desc}">
  <meta name="keywords" content="{kw}, AI news, artificial intelligence news, breaking AI news">
  <meta name="robots" content="index, follow, max-snippet:-1, max-image-preview:large">
  <meta name="author" content="{SITE_NAME} Editorial">
  <link rel="canonical" href="{SITE_DOMAIN}/">
  <meta property="og:type" content="website">
  <meta property="og:site_name" content="{SITE_NAME}">
  <meta property="og:title" content="{hero_st}">
  <meta property="og:description" content="{meta_desc}">
  <meta property="og:url" content="{SITE_DOMAIN}/">
  <meta property="og:image" content="{SITE_DOMAIN}/og-image.jpg">
  <meta name="twitter:card" content="summary_large_image">
  <meta name="twitter:site" content="{SITE_TWITTER}">
  <meta name="twitter:title" content="{hero_st}">
  <meta name="twitter:description" content="{meta_desc}">
  <link rel="sitemap" type="application/xml" href="{SITE_DOMAIN}/sitemap.xml">
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Playfair+Display:ital,wght@0,400;0,600;0,700;0,900;1,400;1,700&family=Source+Sans+3:wght@300;400;500;600&family=Courier+Prime:wght@400;700&display=swap" rel="stylesheet">
  <script async src="https://www.googletagmanager.com/gtag/js?id=G-XXXXXXXXXX"></script>
  <script>window.dataLayer=window.dataLayer||[];function gtag(){{dataLayer.push(arguments);}}gtag('js',new Date());gtag('config','G-XXXXXXXXXX');</script>
  <script type="application/ld+json">{org_ld}</script>
  {art_ld_blocks}
  <style>{CSS}</style>
</head>
<body>
<div class="ticker"><div class="tlabel"><span class="tdot"></span>Breaking</div><div class="twrap"><div class="ttrack">{ticker(articles)}</div></div></div>
<header class="masthead">
  <div class="mh-top"><div class="mh-date">{date_disp}</div><div class="mh-tag">Trusted AI Intelligence - Updated Hourly</div></div>
  <div class="wordmark">AI <span>Bulletin</span></div>
  <div style="display:flex;align-items:center">
    <nav class="nav">
      <span class="nl active" onclick="flt('all',this)">All</span><span class="nsep">.</span>
      <span class="nl" onclick="flt('Breaking',this)">Breaking</span><span class="nsep">.</span>
      <span class="nl" onclick="flt('Hot',this)">Hot</span><span class="nsep">.</span>
      <span class="nl" onclick="flt('Research',this)">Research</span><span class="nsep">.</span>
      <span class="nl" onclick="flt('Product',this)">Products</span><span class="nsep">.</span>
      <span class="nl" onclick="flt('Funding',this)">Funding</span><span class="nsep">.</span>
      <span class="nl" onclick="flt('Policy',this)">Policy</span>
    </nav>
    <nav class="nav-pages"><a class="np" href="/about.html">About</a><a class="np" href="/privacy.html">Privacy</a></nav>
  </div>
</header>
<div class="ad-banner"></div>
<main class="wrap">
  <div class="srule"><h2>Top Story</h2><div class="rl"></div><span class="rtag">Highest importance - {date_disp}</span></div>
  <section class="hero" itemscope itemtype="https://schema.org/NewsArticle">
    <div>
      <span class="htag">{hero.get('tag','Hot')}</span>
      <h1 class="hhl" itemprop="headline"><a href="{hero_link}" id="{sl0}" target="_blank" rel="noopener">{hero_hl}</a></h1>
      <p class="hsum" itemprop="description">{hero_sum}</p>
      <p class="htake">{hero_take}</p>
      <div class="hmeta">
        <span class="hsrc">{hero_src}</span>
        <span class="himp">Importance {hero.get('importance',0)}/10</span>
        <a class="hread" href="{hero_link}" target="_blank" rel="noopener">Read Full Story</a>
      </div>
    </div>
    <aside class="hside"><div class="hside-title">Also Today</div>{side_stories}</aside>
  </section>
  <div class="clayout">
    <div>
      <div class="srule"><h2>Latest Intelligence</h2><div class="rl"></div><span class="rtag">{count} stories - {now_str}</span></div>
      <div class="agrid" id="agrid">{cards_html}</div>
    </div>
    <aside>
      <div class="sw">
        <div class="sw-hd">Daily Briefing</div>
        <div class="sw-body">
          <p class="nl-copy">The 5 most important AI stories every morning. Trusted by founders, researchers and investors worldwide.</p>
          <div class="nl-form">
            <input class="nl-input" type="email" placeholder="your@email.com">
            <button class="nl-btn" onclick="subscribe()">Subscribe Free</button>
          </div>
          <p class="nl-note">Free - No spam - Unsubscribe anytime</p>
        </div>
      </div>
      <div class="ad-rect"></div>
      <div class="sw" style="margin-top:1.5rem">
        <div class="sw-hd">Trending</div>
        <div class="sw-body">{trending_items(articles)}</div>
      </div>
      <div class="sw" style="margin-top:1.5rem">
        <div class="sw-hd">Recommended Tools</div>
        <div class="sw-body">
          <p style="font-family:var(--mono);font-size:.58rem;color:var(--ink4);margin-bottom:.75rem">SPONSORED</p>
          <div class="af-item"><div><div class="af-name">Cursor</div><div class="af-desc">AI code editor</div></div><a class="af-link" href="https://cursor.sh" target="_blank" rel="sponsored">Try Free</a></div>
          <div class="af-item"><div><div class="af-name">Perplexity</div><div class="af-desc">AI search engine</div></div><a class="af-link" href="https://perplexity.ai" target="_blank" rel="sponsored">Try Free</a></div>
          <div class="af-item"><div><div class="af-name">Jasper AI</div><div class="af-desc">AI content platform</div></div><a class="af-link" href="https://jasper.ai" target="_blank" rel="sponsored">Try Free</a></div>
        </div>
      </div>
    </aside>
  </div>
</main>
<footer>
  <div class="fi">
    <div><div class="f-brand">AI <span>Bulletin</span></div><p class="f-copy">{SITE_DESCRIPTION}</p></div>
    <div>
      <div class="f-ct">Coverage</div>
      <span class="f-link">Breaking News</span>
      <span class="f-link">Research and Science</span>
      <span class="f-link">Products and Launches</span>
      <span class="f-link">Funding and Business</span>
      <span class="f-link">Policy and Regulation</span>
    </div>
    <div>
      <div class="f-ct">Company</div>
      <a class="f-link" href="/about.html">About Us</a>
      <a class="f-link" href="/privacy.html">Privacy Policy</a>
      <span class="f-link">Advertise</span>
      <a class="f-link" href="/sitemap.xml">Sitemap</a>
    </div>
  </div>
  <div class="f-bot">
    <span>2026 AI Bulletin - aibulletin.news - All rights reserved</span>
    <span>{count} stories - {now_str}</span>
  </div>
</footer>
<script>
function flt(tag,btn){{
  document.querySelectorAll('.nl').forEach(b=>b.classList.remove('active'));
  if(btn)btn.classList.add('active');
  document.querySelectorAll('#agrid .acard,#agrid .ad-inf').forEach(c=>{{
    if(c.classList.contains('ad-inf')){{c.style.display=tag==='all'?'':'none';return;}}
    c.style.display=(tag==='all'||c.dataset.tag===tag)?'':'none';
  }});
}}
function subscribe(){{
  var e=document.querySelector('.nl-input').value;
  if(!e||e.indexOf('@')<0){{alert('Please enter a valid email.');return;}}
  window.open('https://your-publication.beehiiv.com/subscribe?email='+encodeURIComponent(e),'_blank');
}}
</script>
</body>
</html>"""


def main():
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise SystemExit("ERROR: GEMINI_API_KEY environment variable not set.")

    print("Configuring Gemini API...")
    genai.configure(api_key=api_key)
    model = genai.GenerativeModel("gemini-1.5-flash")

    try:
        test = model.generate_content("Reply with just the word: OK")
        print(f"Gemini connection test: {test.text.strip()}")
    except Exception as ex:
        raise SystemExit(f"ERROR: Gemini API connection failed: {ex}")

    seen = load_seen()
    print(f"Seen IDs loaded: {len(seen)}")

    print("\nFetching RSS feeds...")
    raw = fetch_raw(seen)

    if not raw:
        print("No new articles found.")
        archive = Path(ARCHIVE_FILE)
        existing = json.loads(archive.read_text()) if archive.exists() else []
        if existing:
            print(f"Regenerating site with {len(existing)} existing articles...")
            Path("index.html").write_text(build_html(existing), encoding="utf-8")
            Path("sitemap.xml").write_text(generate_sitemap(existing), encoding="utf-8")
            Path("robots.txt").write_text(robots_txt(), encoding="utf-8")
        return

    print(f"\nProcessing {len(raw)} articles with Gemini...")
    processed, new_ids = [], set()

    for i, art in enumerate(raw):
        if not is_ai(art["title"], art["summary"]):
            print(f"[{i+1}/{len(raw)}] Skipped: {art['title'][:50]}")
            seen.add(art["id"])
            continue

        print(f"[{i+1}/{len(raw)}] Processing: {art['title'][:55]}")
        result = process_with_gemini(model, art)
        result["link"]   = art["link"]
        result["source"] = art["source"]
        processed.append(result)
        new_ids.add(art["id"])
        time.sleep(2)

    print(f"\nProcessed: {len(processed)} articles")
    processed.sort(key=lambda x: x.get("importance",0), reverse=True)

    archive = Path(ARCHIVE_FILE)
    old = json.loads(archive.read_text()) if archive.exists() else []
    all_articles = (processed + old)[:50]

    print(f"Saving {len(all_articles)} total articles...")
    archive.write_text(json.dumps(all_articles, indent=2))

    print("Generating site files...")
    Path("index.html").write_text(build_html(all_articles), encoding="utf-8")
    Path("sitemap.xml").write_text(generate_sitemap(all_articles), encoding="utf-8")
    Path("robots.txt").write_text(robots_txt(), encoding="utf-8")

    seen.update(new_ids)
    save_seen(seen)

    print(f"\nDone! {len(processed)} new articles added. Total: {len(all_articles)}")


if __name__ == "__main__":
    main()

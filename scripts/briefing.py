#!/usr/bin/env python3
"""
Briefing IA & Tech — Curador automático + gerador de landing page
Coleta arXiv cs.AI + cs.CL + notícias web, deduplica, atualiza JSON + HTML.
"""

import os, sys, json, time, re, html
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.request import urlopen, Request
from urllib.error import URLError

# Try importing hermes_tools (available in execute_code; standalone script may need sys.path injection)
try:
    from hermes_tools import web_search as _ws
except ImportError:
    _ws = None

# ─── CONFIG ──────────────────────────────────────────────
BASE = Path(os.environ.get("BRIEFING_BASE", r"C:\Users\MAARA1\squads\nexus-compute-news"))
LANDING = BASE / "landing"
STATE_FILE = BASE / "briefing_state.json"
MAX_STATE = 100
MAX_ITEMS_PER_CATEGORY = 20
MAX_TOTAL_ITEMS = 60

ARXIV_QUERIES = [
    ("cs.AI", 10),
    ("cs.CL", 8),
    ("cs.LG", 6),       # ML
    ("cs.CR", 4),       # segurança
]

WEB_QUERIES = [
    "AI model release launch this week",
    "LLM open weight commercial 2025",
    "GPU cloud data center renewable energy",
    "NVIDIA AMD Intel AI chip announcement",
    "AI startup funding raise round",
    "open source AI breakthrough benchmark",
]

BRT = timezone(timedelta(hours=-3))

# ─── STATE ───────────────────────────────────────────────
def load_state():
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return []

def save_state(state):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    # FIFO keep last MAX_STATE
    state = state[-MAX_STATE:]
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

def seen(state, item_id):
    return item_id in state

def add_seen(state, item_id):
    state.append(item_id)

# ─── ARXIV ───────────────────────────────────────────────
def fetch_arxiv():
    items = []
    ns = "http://www.w3.org/2005/Atom"
    for cat, n in ARXIV_QUERIES:
        url = f"https://export.arxiv.org/api/query?search_query=cat:{cat}&sortBy=submittedDate&sortOrder=descending&max_results={n}"
        try:
            data = urlopen(url, timeout=30).read().decode("utf-8")
        except Exception as e:
            print(f"[WARN] arxiv {cat}: {e}", file=sys.stderr)
            continue
        # parse XML com regex simples (sem lxml)
        entries = re.findall(r"<entry>(.*?)</entry>", data, re.S)
        for entry in entries:
            def g(tag):
                m = re.search(rf"<{tag}[^>]*>(.*?)</{tag}>", entry, re.S)
                return re.sub(r"<[^>]+>", "", m.group(1)).strip() if m else ""
            aid = re.search(r"<id>(.*?)</id>", entry)
            aid = aid.group(1).strip() if aid else ""
            title = g("title")
            summary = g("summary")
            published = g("published")
            # arxiv categories
            cats = re.findall(r"<category[^>]+term=\"([^\"]+)\"", entry)
            category = cats[0] if cats else cat
            items.append({
                "id": aid or f"arxiv-{cat}-{abs(hash(title))}",
                "title": title,
                "summary": summary[:500] + ("…" if len(summary) > 500 else ""),
                "url": aid,
                "published": published,
                "source": "arXiv",
                "category": category,
                "tags": ["research", "paper", category.split(".")[0]],
            })
    return items

# ─── WEB / RSS ───────────────────────────────────────────
RSS_FEEDS = [
    ("OpenAI", "https://openai.com/blog/rss.xml"),
    ("Hugging Face", "https://huggingface.co/blog/feed.xml"),
    ("DeepMind", "https://deepmind.google/blog/rss.xml"),
    ("MIT Tech Review", "https://www.technologyreview.com/feed/"),
    ("TechCrunch", "https://techcrunch.com/feed/"),
    ("The Verge AI", "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml"),
    ("Anthropic", "https://www.anthropic.com/rss.xml"),
    ("Google AI", "https://blog.google/technology/ai/rss/"),
    ("arXiv cs.AI", "https://rss.arxiv.org/rss/cs.AI"),
    ("arXiv cs.CL", "https://rss.arxiv.org/rss/cs.CL"),
    ("arXiv cs.LG", "https://rss.arxiv.org/rss/cs.LG"),
]

def fetch_rss():
    items = []
    for src, url in RSS_FEEDS:
        try:
            req = Request(url, headers={"User-Agent": "Mozilla/5.0 BriefingBot/1.0"})
            raw = urlopen(req, timeout=20).read().decode("utf-8", errors="ignore")
        except Exception as e:
            print(f"[WARN] RSS {src}: {e}", file=sys.stderr)
            continue
        # Parse <item> blocks
        entries = re.findall(r"<item>(.*?)</item>", raw, re.S)
        if not entries:
            entries = re.findall(r"<entry>(.*?)</entry>", raw, re.S)
        for entry in entries:
            def tag(t):
                m = re.search(rf"<{t}[^>]*>(.*?)</{t}>", entry, re.S)
                return re.sub(r"<[^>]+>", "", m.group(1)).strip() if m else ""
            title = tag("title")
            link = tag("link")
            # some rss put link as attribute
            if not link:
                m = re.search(r'<link[^>]+href="([^"]+)"', entry)
                link = m.group(1) if m else ""
            desc = tag("description") or tag("summary") or tag("content")
            pub = tag("pubDate") or tag("published") or ""
            if not title or not link:
                continue
            # category heuristic from source name + content
            src_low = src.lower()
            if any(k in src_low for k in ["openai", "anthropic", "deepmind", "google"]):
                cat = "PRODUTOS"
            elif any(k in src_low for k in ["techcrunch", "verge"]):
                cat = "MERCADO"
            elif any(k in src_low for k in ["mit", "review"]):
                cat = "PESQUISA"
            else:
                cat = "TECNOLOGIA"
            blob = (title + " " + desc).lower()
            if any(k in blob for k in ["funding", "raise", "investment", "startup"]):
                cat = "MERCADO"
            elif any(k in blob for k in ["paper", "research", "model", "benchmark"]):
                cat = "PESQUISA"
            elif any(k in blob for k in ["gpu", "cloud", "data center", "infrastructure"]):
                cat = "INFRA"
            elif any(k in blob for k in ["launch", "release", "product"]):
                cat = "PRODUTOS"
            items.append({
                "id": f"rss-{abs(hash(link))}",
                "title": title,
                "summary": re.sub(r"<[^>]+>", "", desc)[:500],
                "url": link,
                "published": pub or datetime.now(BRT).isoformat(),
                "source": src,
                "category": cat,
                "tags": ["news", cat.lower()],
            })
    return items

# ─── CURATION ────────────────────────────────────────────
KEYWORDS_IA = [
    "AI", "LLM", "large language model", "transformer", "GPT", "Claude", "Gemini",
    "open source AI", "foundation model", "diffusion", "multimodal", "agent",
    "fine-tuning", "RLHF", "quantization", "GPU", "H100", "A100", "tensor",
    "embedding", "RAG", "inference", "benchmark", " Hugging Face", "PyTorch",
    "machine learning", "deep learning", "neural", "robotics", "autonomous",
]

def relevance_score(item):
    blob = (item.get("title", "") + " " + item.get("summary", "")).lower()
    return sum(1 for k in KEYWORDS_IA if k.lower() in blob)

def dedupe_and_rank(raw_items, seen_ids):
    # dedupe by id
    seen_local = set(seen_ids)
    fresh = [it for it in raw_items if it["id"] not in seen_local]
    # rank by relevance
    fresh.sort(key=lambda x: relevance_score(x), reverse=True)
    # cap
    return fresh[:MAX_TOTAL_ITEMS]

def categorize(items):
    cats = {}
    for it in items:
        cat = it.get("category", "OUTROS")
        cats.setdefault(cat, []).append(it)
    return dict(sorted(cats.items()))

# ─── HTML GENERATION ─────────────────────────────────────
def generate_html(categorized, state):
    now = datetime.now(BRT).strftime("%d/%m/%Y %H:%M")
    total = sum(len(v) for v in categorized.values())
    sections = ""
    for cat, items in categorized.items():
        emoji_map = {
            "MERCADO": "💰", "PESQUISA": "🔬", "PRODUTOS": "🚀", "TECNOLOGIA": "⚙️",
            "INFRA": "🖥️", "cs.AI": "🤖", "cs.CL": "💬", "cs.LG": "📊",
            "cs.CR": "🔒", "OUTROS": "📰"
        }
        emoji = emoji_map.get(cat, "📌")
        cards = ""
        for it in items[:MAX_ITEMS_PER_CATEGORY]:
            title = html.escape(it.get("title", ""))
            summary = html.escape(it.get("summary", ""))[:280]
            url = html.escape(it.get("url", "#"))
            src = html.escape(it.get("source", ""))
            pub = it.get("published", "")[:16].replace("T", " ")
            tags = " ".join(f"<span class='tag'>{html.escape(t)}</span>" for t in it.get("tags", []))
            cards += f"""
            <div class="card">
              <div class="card-header">
                <span class="source-badge">{src}</span>
                <span class="time-badge">{pub}</span>
              </div>
              <h3><a href="{url}" target="_blank" rel="noopener">{title}</a></h3>
              <p>{summary}</p>
              <div class="tags">{tags}</div>
            </div>"""
        sections += f"""
        <section class="category">
          <h2>{emoji} {html.escape(cat)} <span class="count">{len(items)}</span></h2>
          <div class="cards">{cards}</div>
        </section>"""

    html_doc = f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Briefing IA & Tech — Nexus Compute</title>
<style>
  :root{{
    --bg:#0a0a0f;--panel:#12121a;--line:#1f1f2e;--accent:#7c3aed;
    --accent2:#06b6d4;--text:#e2e8f0;--dim:#94a3b8;--green:#10b981;--red:#ef4444;
    --card:#0f0f17;--border:#2a2a3a;--hover:#181825;
  }}
  *{{box-sizing:border-box;margin:0;padding:0}}
  body{{
    background:var(--bg);color:var(--text);font-family:'Segoe UI',system-ui,-apple-system,sans-serif;
    line-height:1.6;min-height:100vh;
    background-image:
      radial-gradient(ellipse 80% 50% at 50% -20%,rgba(124,58,237,0.18),transparent),
      radial-gradient(ellipse 60% 40% at 80% 100%,rgba(6,182,212,0.10),transparent);
  }}
  header{{
    background:linear-gradient(180deg,rgba(18,18,26,0.95),rgba(18,18,26,0.7));
    border-bottom:1px solid var(--line);padding:2.5rem 1.5rem 2rem;
    position:sticky;top:0;z-index:50;backdrop-filter:blur(12px);
  }}
  .header-inner{{
    max-width:1200px;margin:0 auto;display:flex;flex-wrap:wrap;gap:1rem;
    align-items:center;justify-content:space-between;
  }}
  .brand h1{{
    font-size:clamp(1.2rem,2.5vw,1.8rem);font-weight:800;letter-spacing:-0.02em;
    background:linear-gradient(135deg,var(--accent),var(--accent2));
    -webkit-background-clip:text;-webkit-text-fill-color:transparent;
  }}
  .brand small{{color:var(--dim);font-size:0.8rem;display:block;margin-top:0.2rem}}
  .stats{{
    display:flex;gap:1.2rem;flex-wrap:wrap;
  }}
  .stat{{
    background:var(--panel);border:1px solid var(--border);border-radius:0.6rem;
    padding:0.5rem 0.9rem;text-align:center;min-width:90px;
  }}
  .stat-value{{font-size:1.3rem;font-weight:700;color:var(--accent2)}}
  .stat-label{{font-size:0.7rem;color:var(--dim);text-transform:uppercase;letter-spacing:0.05em}}
  .refresh{{
    display:inline-flex;align-items:center;gap:0.4rem;
    background:var(--accent);color:#fff;border:none;padding:0.5rem 1rem;
    border-radius:0.5rem;cursor:pointer;font-size:0.85rem;font-weight:600;
    transition:opacity 0.2s;
  }}
  .refresh:hover{{opacity:0.85}}
  .update-time{{font-size:0.75rem;color:var(--dim);margin-top:0.4rem}}
  main{{max-width:1200px;margin:0 auto;padding:2rem 1.5rem 4rem}}
  .category{{margin-bottom:2.5rem}}
  .category h2{{
    font-size:1.1rem;color:var(--accent2);margin-bottom:1rem;
    border-bottom:1px solid var(--line);padding-bottom:0.4rem;
    display:flex;align-items:center;gap:0.5rem;
  }}
  .count{{
    background:var(--panel);border:1px solid var(--border);color:var(--dim);
    font-size:0.75rem;padding:0.15rem 0.5rem;border-radius:999px;
  }}
  .cards{{display:grid;grid-template-columns:repeat(auto-fill,minmax(320px,1fr));gap:1rem}}
  .card{{
    background:var(--card);border:1px solid var(--border);border-radius:0.75rem;
    padding:1.1rem 1.2rem;transition:transform 0.2s,border-color 0.2s;
  }}
  .card:hover{{transform:translateY(-2px);border-color:var(--accent);background:var(--hover)}}
  .card-header{{display:flex;justify-content:space-between;margin-bottom:0.5rem;gap:0.5rem;flex-wrap:wrap}}
  .source-badge{{
    font-size:0.7rem;font-weight:700;text-transform:uppercase;letter-spacing:0.04em;
    background:rgba(124,58,237,0.15);color:var(--accent);padding:0.2rem 0.5rem;border-radius:0.3rem;
  }}
  .time-badge{{font-size:0.7rem;color:var(--dim)}}
  .card h3{{font-size:0.95rem;font-weight:600;line-height:1.35;margin-bottom:0.4rem}}
  .card h3 a{{color:var(--text);text-decoration:none;transition:color 0.2s}}
  .card h3 a:hover{{color:var(--accent2)}}
  .card p{{font-size:0.82rem;color:var(--dim);line-height:1.5}}
  .tags{{display:flex;flex-wrap:wrap;gap:0.35rem;margin-top:0.7rem}}
  .tag{{
    font-size:0.68rem;color:var(--dim);background:var(--panel);
    border:1px solid var(--border);padding:0.15rem 0.45rem;border-radius:999px;
  }}
  footer{{
    text-align:center;padding:2rem 1rem;color:var(--dim);font-size:0.75rem;
    border-top:1px solid var(--line);
  }}
  footer a{{color:var(--accent);text-decoration:none}}
  @media(max-width:640px){{
    .cards{{grid-template-columns:1fr}}
    .header-inner{{flex-direction:column;align-items:flex-start}}
  }}
</style>
</head>
<body>
<header>
  <div class="header-inner">
    <div class="brand">
      <h1>📡 Briefing IA & Tech</h1>
      <small>Nexus Compute — Curadoria automática com deduplicação</small>
    </div>
    <div class="stats">
      <div class="stat">
        <div class="stat-value">{total}</div>
        <div class="stat-label">Itens</div>
      </div>
      <div class="stat">
        <div class="stat-value">{len(categorized)}</div>
        <div class="stat-label">Categorias</div>
      </div>
      <button class="refresh" onclick="location.reload()">🔄 Atualizar</button>
    </div>
  </div>
  <div class="update-time">Última atualização: {html.escape(now)} BRT</div>
</header>
<main>
{sections}
</main>
<footer>
  <p>🤖 Gerado por Hermes Agent · <a href="https://nexus-compute-news.netlify.app" target="_blank">Nexus Compute</a></p>
  <p>Fontes: arXiv (cs.AI / cs.CL / cs.LG / cs.CR) + Web Search · Deduplicação ativa · Estado: {len(state)} itens</p>
</footer>
</body>
</html>"""
    return html_doc

# ─── MAIN ────────────────────────────────────────────────
def main():
    print(f"[BRIEFING] Início {datetime.now(BRT).isoformat()}", flush=True)
    state = load_state()
    seen_ids = set(state)

    raw = fetch_arxiv() + fetch_rss()
    print(f"[BRIEFING] Bruto: {len(raw)} itens", flush=True)

    fresh = dedupe_and_rank(raw, seen_ids)
    print(f"[BRIEFING] Frescos (não duplicados): {len(fresh)}", flush=True)

    categorized = categorize(fresh)

    LANDING.mkdir(parents=True, exist_ok=True)
    (LANDING / "index.html").write_text(
        generate_html(categorized, state), encoding="utf-8"
    )
    (LANDING / "news.json").write_text(
        json.dumps({"updated_at": datetime.now(BRT).isoformat(), "items": fresh},
                   ensure_ascii=False, indent=2),
        encoding="utf-8"
    )

    # update state
    for it in fresh:
        add_seen(state, it["id"])
    save_state(state)
    print(f"[BRIEFING] Estado: {len(state)} itens · HTML atualizado · Total categorias: {len(categorized)}", flush=True)

if __name__ == "__main__":
    main()

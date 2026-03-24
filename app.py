from flask import Flask, send_file, request, jsonify, session
import os
import requests
import time
import json

app = Flask(__name__)
app.secret_key = "linkedin-chatbot-secret-2024"

# ─── API KEYS ─────────────────────────────────────────────────────────────────
BRIGHT_DATA_API_KEY = "ENTER_YOUR_API_KEY"
GROQ_API_KEY        = "ENTER_YOUR_API_KEY"

# ─── Bright Data ──────────────────────────────────────────────────────────────
BD_TRIGGER_URL  = "https://api.brightdata.com/datasets/v3/trigger"
BD_SNAPSHOT_URL = "https://api.brightdata.com/datasets/v3/snapshot"

LINKEDIN_DATASET_ID        = "gd_l1viktl72bvl7bjuj0"
TWITTER_PROFILE_DATASET_ID = "gd_lwxmeb2u1cniijd7t4"
TWITTER_POSTS_DATASET_ID   = "gd_lwxkxvnf1cynvib9co"

# ─── Groq ──────────────────────────────────────────────────────────────────────
GROQ_URL   = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL = "llama-3.3-70b-versatile"


# ── Global JSON error handlers so Flask never returns HTML on errors ───────────
@app.errorhandler(404)
def not_found(e):
    return jsonify({"error": "Route not found"}), 404

@app.errorhandler(405)
def method_not_allowed(e):
    return jsonify({"error": "Method not allowed"}), 405

@app.errorhandler(500)
def internal_error(e):
    return jsonify({"error": f"Server error: {str(e)}"}), 500


# ──────────────────────────────────────────────────────────────────────────────
#  Bright Data helpers
# ──────────────────────────────────────────────────────────────────────────────

def trigger_scrape(dataset_id: str, payload: list) -> str:
    """Trigger a Bright Data scrape; return snapshot_id."""
    headers = {
        "Authorization": f"Bearer {BRIGHT_DATA_API_KEY}",
        "Content-Type": "application/json",
    }
    resp = requests.post(
        BD_TRIGGER_URL,
        headers=headers,
        params={"dataset_id": dataset_id, "include_errors": "true"},
        json=payload,
        timeout=30,
    )
    if not resp.ok:
        raise RuntimeError(f"Bright Data trigger failed [{resp.status_code}]: {resp.text[:300]}")
    data = resp.json()
    snap_id = data.get("snapshot_id")
    if not snap_id:
        raise RuntimeError(f"No snapshot_id in response: {data}")
    return snap_id


def fetch_snapshot(snapshot_id: str) -> list:
    """Poll until snapshot is ready, return list of records."""
    headers = {"Authorization": f"Bearer {BRIGHT_DATA_API_KEY}"}
    url     = f"{BD_SNAPSHOT_URL}/{snapshot_id}"

    for attempt in range(24):   # ~2 minutes max (24 x 5 s)
        resp = requests.get(url, headers=headers, params={"format": "json"}, timeout=30)
        if resp.status_code == 200:
            try:
                data = resp.json()
                return data if isinstance(data, list) else [data]
            except Exception:
                return [{"raw": resp.text}]
        elif resp.status_code == 202:
            time.sleep(5)
        else:
            raise RuntimeError(f"Snapshot fetch failed [{resp.status_code}]: {resp.text[:300]}")

    raise TimeoutError("Bright Data snapshot not ready after 2 minutes")


def scrape_linkedin(profile_url: str) -> dict:
    """Scrape a LinkedIn profile by full URL."""
    snap_id = trigger_scrape(LINKEDIN_DATASET_ID, [{"url": profile_url}])
    records = fetch_snapshot(snap_id)
    data    = records[0] if records else {}

    print("\n" + "=" * 60)
    print("  BRIGHT DATA — LINKEDIN RAW RESPONSE")
    print("=" * 60)
    print(json.dumps(data, indent=2, default=str))
    print("=" * 60 + "\n")

    return data


def scrape_x_profile(profile_url: str) -> dict:
    """
    Scrape an X (Twitter) profile by full URL.
    Accepts: https://x.com/username  or  https://twitter.com/username
    Uses: { "url": profile_url } — identical pattern to LinkedIn scraping.
    """
    snap_id = trigger_scrape(
        TWITTER_PROFILE_DATASET_ID,
        [{"url": profile_url}]
    )
    records = fetch_snapshot(snap_id)
    data    = records[0] if records else {}

    print("\n" + "=" * 60)
    print("  ===== X PROFILE DATA =====")
    print("=" * 60)
    print(json.dumps(data, indent=2, default=str))
    print("=" * 60 + "\n")

    return data


def scrape_x_posts(profile_url: str) -> list:
    """
    Scrape recent X/Twitter posts for a profile URL.
    Uses /scrape endpoint synchronously with data=json.dumps() — NOT json= kwarg.
    Returns the list of post records directly (no snapshot polling needed).
    """
    headers = {
        "Authorization": f"Bearer {BRIGHT_DATA_API_KEY}",
        "Content-Type": "application/json",
    }

    payload = json.dumps({
        "input": [
            {
                "url": profile_url,
                "start_date": "",
                "end_date": "",
            }
        ]
    })

    response = requests.post(
        f"https://api.brightdata.com/datasets/v3/scrape"
        f"?dataset_id={TWITTER_POSTS_DATASET_ID}"
        f"&notify=false&include_errors=true"
        f"&type=discover_new&discover_by=profile_url",
        headers=headers,
        data=payload,       # ← must be data=, NOT json=
        timeout=60,
    )

    if not response.ok:
        print("X POSTS ERROR:", response.text)
        return []

    # Bright Data /scrape returns NDJSON (Newline Delimited JSON) on success
    # but a single JSON object if it returns an error (like "dead_page")
    text = response.text.strip()
    if not text:
        return []

    records = []
    try:
        # First try to parse as single JSON object (error from Bright Data)
        data = json.loads(text)
        if isinstance(data, dict):
            if "error" in data:
                print("X POSTS API ERROR:", data["error"])
                return []
            records.append(data)
        elif isinstance(data, list):
            records = data
    except Exception:
        # NDJSON format — successfully scraped posts
        for line in text.split("\n"):
            line = line.strip()
            if line:
                try:
                    records.append(json.loads(line))
                except Exception:
                    pass

    print("\n===== X POSTS DATA =====")
    print(json.dumps(records, indent=2, default=str))
    print("========================\n")

    return records
# ──────────────────────────────────────────────────────────────────────────────
#  Profile context builder
# ──────────────────────────────────────────────────────────────────────────────

def build_context(linkedin: dict, twitter: dict | None) -> str:
    """Convert scraped profile data into a plain-text context string for the LLM."""
    lines = ["=== LINKEDIN PROFILE ==="]

    # ── Basic info ────────────────────────────────────────────────────────
    name      = linkedin.get("name") or linkedin.get("full_name", "")
    headline  = linkedin.get("headline") or linkedin.get("title", "")
    summary   = linkedin.get("summary") or linkedin.get("about") or linkedin.get("description", "")
    location  = (linkedin.get("location") or linkedin.get("city") or
                 linkedin.get("country") or linkedin.get("country_code", ""))
    website   = linkedin.get("website") or linkedin.get("url", "")
    followers = linkedin.get("followers") or linkedin.get("followers_count", "")

    if name:      lines.append(f"NAME: {name}")
    if headline:  lines.append(f"HEADLINE: {headline}")
    if location:  lines.append(f"LOCATION: {location}")
    if followers: lines.append(f"FOLLOWERS: {followers}")
    if summary:   lines.append(f"ABOUT: {summary[:500]}")
    if website:   lines.append(f"WEBSITE: {website}")

    # ── Current company ───────────────────────────────────────────────────
    current = linkedin.get("current_company") or {}
    if isinstance(current, dict) and current.get("name"):
        lines.append(f"CURRENT COMPANY: {current.get('name')} — {current.get('title', '')}")

    # ── Experience ────────────────────────────────────────────────────────
    experiences = (
        linkedin.get("experiences") or
        linkedin.get("experience") or
        linkedin.get("positions") or
        linkedin.get("work_experience") or
        []
    )
    if experiences:
        lines.append("\nEXPERIENCE:")
    for exp in experiences[:8]:
        title   = exp.get("title", "")
        company = exp.get("company") or exp.get("company_name") or exp.get("organization", "")
        start   = exp.get("starts_at") or exp.get("start_date", "?")
        end     = exp.get("ends_at") or exp.get("end_date", "Present")
        desc    = (exp.get("description") or "")[:200]
        lines.append(f"  * {title} @ {company} ({start}-{end})\n    {desc}")

    # ── Education ─────────────────────────────────────────────────────────
    edus = (
        linkedin.get("education") or
        linkedin.get("educations") or
        []
    )[:4]
    if edus:
        lines.append("\nEDUCATION:")
        for e in edus:
            degree = e.get("degree_name") or e.get("degree", "")
            field  = e.get("field_of_study") or e.get("field", "")
            school = e.get("school") or e.get("school_name") or e.get("institution", "")
            lines.append(f"  * {degree} {field} — {school}")

    # ── Skills ────────────────────────────────────────────────────────────
    skills = (linkedin.get("skills") or [])[:20]
    if skills:
        names = [s.get("name") or s if isinstance(s, dict) else s for s in skills]
        lines.append(f"\nSKILLS: {', '.join(str(n) for n in names if n)}")

    # ── Own Posts ─────────────────────────────────────────────────────────
    own_posts = (
        linkedin.get("posts") or
        linkedin.get("recent_posts") or
        []
    )
    if own_posts:
        lines.append("\nOWN POSTS:")
        for p in own_posts[:5]:
            text = (
                p.get("text") or
                p.get("content") or
                p.get("title") or
                str(p)[:200]
            )
            likes  = p.get("num_likes") or p.get("likes", "")
            suffix = f" [{likes} likes]" if likes else ""
            lines.append(f"  — {text[:250]}{suffix}")

    # ── Recent Interactions (liked / commented / shared) ──────────────────
    interactions = (
        linkedin.get("activity") or
        linkedin.get("activities") or
        []
    )
    if interactions:
        lines.append("\nRECENT INTERACTIONS:")
        for item in interactions[:8]:
            action = (
                item.get("interaction") or
                item.get("action") or
                "interacted with"
            )
            title_ = (
                item.get("title") or
                item.get("text") or
                item.get("content") or
                "a post"
            )
            link  = item.get("link") or item.get("url") or ""
            time_ = item.get("time") or item.get("date") or ""
            ts    = f" ({time_})" if time_ else ""
            lines.append(f"  * {action}{ts}: {title_[:200]}")
            if link:
                lines.append(f"    -> {link}")

    # ── Certifications ────────────────────────────────────────────────────
    certs = (linkedin.get("certifications") or [])[:5]
    if certs:
        lines.append("\nCERTIFICATIONS: " + ", ".join(c.get("name", "") for c in certs))

    # ── X (Twitter) enrichment ────────────────────────────────────────────
    if twitter:
        lines.append("\n=== X PROFILE ===")

        tw_name      = twitter.get("name", "")
        tw_username  = twitter.get("username") or twitter.get("screen_name", "")
        tw_bio       = twitter.get("description") or twitter.get("bio", "")
        tw_followers = twitter.get("followers_count", "")
        tw_following = twitter.get("following_count", "")
        tw_tweet_cnt = twitter.get("tweet_count", "")

        if tw_name:      lines.append(f"NAME: {tw_name}")
        if tw_username:  lines.append(f"USERNAME: @{tw_username}")
        if tw_bio:       lines.append(f"BIO: {tw_bio}")
        if tw_followers: lines.append(f"FOLLOWERS: {tw_followers}")
        if tw_following: lines.append(f"FOLLOWING: {tw_following}")
        if tw_tweet_cnt: lines.append(f"TOTAL TWEETS: {tw_tweet_cnt}")

        tweets = (
            twitter.get("tweets") or
            twitter.get("posts") or
            twitter.get("timeline") or
            []
        )
        if tweets:
            lines.append("\nRECENT POSTS (X):")
            for t in tweets[:10]:
                text = (
                    t.get("text") or
                    t.get("full_text") or
                    t.get("content") or
                    str(t)
                )
                likes    = t.get("favorite_count") or t.get("likes", "")
                retweets = t.get("retweet_count") or t.get("retweets", "")
                stats    = ""
                if likes:    stats += f" [{likes} likes]"
                if retweets: stats += f" [{retweets} RTs]"
                lines.append(f"  — {text[:250]}{stats}")
        else:
            lines.append("\nRECENT POSTS (X): (none retrieved)")

    return "\n".join(lines)


# ──────────────────────────────────────────────────────────────────────────────
#  RAG — keyword-scored retrieval from the context string
# ──────────────────────────────────────────────────────────────────────────────

def retrieve_relevant_docs(query: str, context: str) -> list[str]:
    """
    Return the most query-relevant lines from the context block.
    Tweets and X posts are treated as strong personality/interest signals
    and are boosted so queries about interests, activity, or personality
    always surface X content alongside LinkedIn data.
    """
    lines = context.split("\n")
    query_words = set(query.lower().split())

    # Keywords that map to social/personality signals
    social_keywords = {
        "interest", "like", "liked", "activity", "interact",
        "personality", "engage", "passion", "follow", "topic",
        "tweet", "post", "x", "twitter", "opinion", "share",
        "retweet", "content", "wrote", "said", "published",
    }

    # Section headers that are always relevant context anchors
    section_headers = {
        "=== linkedin profile ===",
        "=== x profile ===",
        "recent posts (x):",
        "own posts:",
        "recent interactions:",
        "experience:",
        "skills:",
        "education:",
    }

    scored = []
    for line in lines:
        line_lower = line.lower().strip()
        if not line_lower:
            continue

        score = sum(1 for word in query_words if word in line_lower)

        # Boost X / Twitter post lines — strong personality signals
        if any(kw in line_lower for kw in ("tweet", "post", "x profile", "twitter", "recent posts (x)")):
            score = max(score, 1)   # always surface if present
            score *= 3

        # Boost interaction / engagement lines
        if "interaction" in line_lower or "interacted" in line_lower:
            score *= 2

        # Boost lines matching social-signal keywords when query overlaps
        if score > 0 and any(kw in line_lower for kw in social_keywords):
            score *= 2

        # Always include section headers that overlap with the query
        if line_lower in section_headers and score > 0:
            score *= 2

        if score > 0:
            scored.append((score, line))

    scored.sort(reverse=True, key=lambda x: x[0])

    # Always pull in the full X PROFILE section + RECENT POSTS (X) block
    x_section_lines = [
        line for line in lines
        if "=== x profile ===" in line.lower()
        or "recent posts (x)" in line.lower()
    ]

    top_lines = [line for _, line in scored[:15]]

    # Merge X section lines without duplicates
    for xl in x_section_lines:
        if xl not in top_lines:
            top_lines.append(xl)

    return top_lines[:20]


# ──────────────────────────────────────────────────────────────────────────────
#  Groq — pure HTTP, no SDK
# ──────────────────────────────────────────────────────────────────────────────

def chat_groq(system_prompt: str, history: list, user_msg: str) -> str:
    messages = [{"role": "system", "content": system_prompt}]
    messages += history
    messages.append({"role": "user", "content": user_msg})

    resp = requests.post(
        GROQ_URL,
        headers={
            "Authorization": f"Bearer {GROQ_API_KEY}",
            "Content-Type": "application/json",
        },
        json={
            "model": GROQ_MODEL,
            "messages": messages,
            "temperature": 0.7,
            "max_tokens": 1024,
        },
        timeout=30,
    )
    if not resp.ok:
        raise RuntimeError(f"Groq API error [{resp.status_code}]: {resp.text[:300]}")
    return resp.json()["choices"][0]["message"]["content"]


# ──────────────────────────────────────────────────────────────────────────────
#  Routes
# ──────────────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    session.clear()
    return send_file(os.path.join(os.path.dirname(__file__), "templates", "index.html"))


@app.route("/favicon.ico")
def favicon():
    """Silence browser favicon requests with no-content response."""
    return "", 204


@app.route("/load_profile", methods=["POST"])
def load_profile():
    try:
        body         = request.get_json(force=True) or {}
        linkedin_url = (body.get("linkedin_url") or "").strip()

        # twitter_handle field now carries the full X profile URL
        # e.g.  https://x.com/elonmusk  or  https://twitter.com/elonmusk
        x_url        = (body.get("twitter_handle") or "").strip()

        if not linkedin_url:
            return jsonify({"error": "LinkedIn URL is required."}), 400
        if "linkedin.com" not in linkedin_url:
            return jsonify({"error": "Please enter a valid LinkedIn profile URL."}), 400

        # ── LinkedIn (required) ───────────────────────────────────────────
        linkedin_data = scrape_linkedin(linkedin_url)

        if not linkedin_data:
            return jsonify({"error": "No data returned — profile may be private or URL is incorrect."}), 400

        # ── X / Twitter (optional) — full URL passed directly ─────────────
        twitter_data = None

        if x_url:
            if "x.com" not in x_url and "twitter.com" not in x_url:
                print(f"WARNING: X URL looks invalid: {x_url} — skipping X enrichment")
            else:
                try:
                    profile = scrape_x_profile(x_url)
                    posts   = scrape_x_posts(x_url)

                    if profile:
                        # Merge posts into profile dict
                        profile["tweets"] = posts
                        twitter_data = profile
                    elif posts:
                        # Got posts but no profile record — still useful context
                        twitter_data = {"tweets": posts}

                except Exception as e:
                    print(f"X SCRAPE ERROR (ignored, continuing without X data): {e}")
                    twitter_data = None

        # ── Build context & store in session ──────────────────────────────
        context = build_context(linkedin_data, twitter_data)

        session["context"]  = context
        session["history"]  = []
        session["name"]     = linkedin_data.get("name") or linkedin_data.get("full_name", "Unknown")
        session["headline"] = linkedin_data.get("headline") or linkedin_data.get("title", "")
        session["pic"]      = linkedin_data.get("profile_pic_url", "")

        return jsonify({
            "success":     True,
            "name":        session["name"],
            "headline":    session["headline"],
            "pic":         session["pic"],
            "has_twitter": twitter_data is not None,
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/chat", methods=["POST"])
def chat():
    try:
        if "context" not in session:
            return jsonify({"error": "No profile loaded. Please scan a profile first."}), 400

        body    = request.get_json(force=True) or {}
        message = (body.get("message") or "").strip()
        if not message:
            return jsonify({"error": "Message cannot be empty."}), 400

        full_context = session["context"]

        # ── RAG — retrieve top relevant lines for focused injection ───────
        relevant_lines = retrieve_relevant_docs(message, full_context)

        print("\n===== RAG TOP LINES =====")
        print("\n".join(relevant_lines))
        print("=========================\n")

        rag_snippet = ""
        if relevant_lines:
            rag_snippet = (
                "\n\n--- MOST RELEVANT SECTIONS FOR THIS QUERY ---\n"
                + "\n".join(relevant_lines)
                + "\n--- END RELEVANT SECTIONS ---\n"
            )

        # ── System prompt: RAG hint + full context ────────────────────────
        system_prompt = (
            "You are an AI assistant analyzing a professional's public digital footprint.\n"
            "Use ALL the data below to answer questions accurately. Pay close attention to:\n"
            "- RECENT POSTS (X): reveals what the person tweets, thinks, and cares about\n"
            "- RECENT INTERACTIONS: reveals what the person likes and engages with on LinkedIn\n"
            "- OWN POSTS: shows what they publish on LinkedIn\n"
            "- EXPERIENCE / SKILLS: career background\n"
            "- X PROFILE: bio, followers, and overall presence on X/Twitter\n"
            "Infer intelligently from all available data. "
            "If a section is truly empty, say so briefly then use other data to answer.\n"
            "Never invent facts not present in the data.\n"
            + rag_snippet
            + "\n\n=== FULL PROFILE DATA ===\n"
            + full_context
        )

        reply = chat_groq(system_prompt, session.get("history", []), message)

        history = session.get("history", [])
        history.append({"role": "user",      "content": message})
        history.append({"role": "assistant", "content": reply})
        session["history"] = history[-20:]   # keep last 10 turns

        return jsonify({"reply": reply})

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/reset", methods=["POST"])
def reset():
    session.clear()
    return jsonify({"success": True})


@app.route("/debug_profile", methods=["POST"])
def debug_profile():
    """Returns the raw Bright Data response so you can inspect which fields exist."""
    try:
        body         = request.get_json(force=True) or {}
        linkedin_url = (body.get("linkedin_url") or "").strip()
        if not linkedin_url:
            return jsonify({"error": "linkedin_url required"}), 400
        snap_id = trigger_scrape(LINKEDIN_DATASET_ID, [{"url": linkedin_url}])
        records = fetch_snapshot(snap_id)
        raw     = records[0] if records else {}
        return jsonify({
            "keys": list(raw.keys()),
            "raw":  raw,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    app.run(debug=True, port=5000)
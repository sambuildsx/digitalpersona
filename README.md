# ProfileScan AI 🔍

An AI-powered chatbot that scrapes a person's public LinkedIn (and optionally X/Twitter) profile using **Bright Data**, then lets you have a multi-turn conversation about them using **Groq (LLaMA 3 70B)** — all via pure HTTP, no SDKs.

---

## Tech Stack

| Layer | Tool |
|---|---|
| Web Framework | Flask |
| Data Scraping | Bright Data Datasets API |
| LLM | Groq API → LLaMA 3 70B |
| Frontend | Vanilla HTML/CSS/JS |

---

## Setup

### 1. Clone & install dependencies

```bash
pip install -r requirements.txt
```

### 2. Add your API keys

Open `app.py` and replace the placeholders at the top:

```python
BRIGHT_DATA_API_KEY = "YOUR_BRIGHT_DATA_API_KEY"
GROQ_API_KEY        = "YOUR_GROQ_API_KEY"
```

### 3. Get your API keys

**Bright Data:**
1. Sign up at https://brightdata.com
2. Go to **Datasets & Web Scraper → Ready Datasets**
3. Find "LinkedIn People Profiles" → note the Dataset ID
4. Find "Twitter Profiles" → note the Dataset ID
5. Update `LINKEDIN_DATASET_ID` and `TWITTER_DATASET_ID` in `app.py`
6. Go to **Account → API Token** to get your key

**Groq:**
1. Sign up at https://console.groq.com
2. Go to **API Keys → Create API Key**

### 4. Run

```bash
python app.py
```

Visit: http://localhost:5000

---

## Project Structure

```
linkedin-chatbot/
├── app.py                  # Flask backend (scraping + Groq chat)
├── requirements.txt
├── templates/
│   └── index.html          # Single-page UI
└── static/
    ├── css/style.css       # Dark sci-fi theme
    └── js/app.js           # Chat logic
```

---

## How It Works

```
User enters LinkedIn URL
        ↓
Bright Data Datasets API triggered (async scrape)
        ↓
Poll snapshot until data is ready (~10–30s)
        ↓
Profile context built into a text block
        ↓
Stored in Flask session
        ↓
User asks questions → sent to Groq LLaMA 3 70B
        ↓
Full conversation history maintained (last 10 turns)
        ↓
Streamed reply rendered in chat UI
```

---

## Features

- ✅ LinkedIn profile scraping (name, headline, experience, education, skills, posts)
- ✅ X/Twitter scraping (bio, tweets, follower counts) — optional
- ✅ Multi-turn conversation with full history
- ✅ Quick-prompt chips for common questions
- ✅ Error handling for private profiles, invalid URLs, API failures
- ✅ No SDKs — pure `requests` HTTP calls to both Bright Data and Groq
- ✅ Responsive dark UI

---

## Error Handling

| Scenario | Behaviour |
|---|---|
| Missing LinkedIn URL | Validation error shown |
| Private / invalid LinkedIn URL | Bright Data returns empty → graceful error |
| Twitter scrape fails | Non-fatal — continues with LinkedIn only |
| Groq API error | Error message shown in chat bubble |
| Snapshot timeout (>60s) | `TimeoutError` surfaced to user |

---

## Deployment (Optional)

**Streamlit Cloud** — not applicable (Flask app)

**Railway / Render:**
```bash
# Procfile
web: python app.py
```

**Vercel** — use `vercel.json` with Flask WSGI adapter.

---

## Notes

- Bright Data scraping takes **10–60 seconds** depending on queue.
- Groq LLaMA 3 70B is extremely fast (~1–2s responses).
- Session data is stored server-side; refresh clears the chat.
- The app keeps the **last 10 conversation turns** to stay within Groq's context limits.
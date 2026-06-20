import os
import json
import time
import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from groq import Groq
from supabase import create_client, Client
from dotenv import load_dotenv

load_dotenv()

# --- LOGGING ---------------------------------------------------------------
# Detailed diagnostics go to the server logs; users only ever see the short,
# friendly replies returned by the endpoint.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
)
logger = logging.getLogger("concierge")

app = FastAPI(title="Well Circle Concierge - Production")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- ENVIRONMENT VARIABLES -------------------------------------------------
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_KEY")

# Small, fast Groq model by default; overridable without a code change.
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.1-8b-instant")
# Cap generation so a runaway response can't blow up latency. Replies are
# 2-3 sentences, so this is generous headroom.
GROQ_MAX_TOKENS = int(os.getenv("GROQ_MAX_TOKENS", "320"))
# Fail fast instead of hanging the user if Groq is slow/unreachable.
GROQ_TIMEOUT_SECONDS = float(os.getenv("GROQ_TIMEOUT_SECONDS", "20"))
# How long provider data is cached in-memory before we re-hit Supabase.
PROVIDER_CACHE_TTL_SECONDS = float(os.getenv("PROVIDER_CACHE_TTL_SECONDS", "60"))

if not GROQ_API_KEY or not SUPABASE_URL or not SUPABASE_KEY:
    logger.warning("Missing environment configuration variables — running in degraded mode")

# --- CLIENT INITIALIZATION -------------------------------------------------
# max_retries=1 keeps failures fast (the default of 2 adds latency on errors).
groq_client = Groq(
    api_key=GROQ_API_KEY or "fallback_placeholder",
    timeout=GROQ_TIMEOUT_SECONDS,
    max_retries=1,
)

try:
    supabase_client: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
except Exception as e:
    # Usually just missing config in local/degraded runs — keep it to one line.
    logger.warning("Supabase client init failed (%s) — falling back to local dataset", e)
    supabase_client = None


# --- LOCAL FALLBACK DATASET ------------------------------------------------
FALLBACK_PROVIDERS = [
    {
        "id": "fb-001",
        "name": "Bole Wellness Hub",
        "category": "gym",
        "description": "Modern gym with personal training and group classes.",
        "location_text": "Bole, Addis Ababa",
        "price_range": "ETB 800-2500",
        "rating": 4.6,
    },
    {
        "id": "fb-002",
        "name": "Serenity Yoga Studio",
        "category": "yoga",
        "description": "Calm, beginner-friendly yoga studio with daily sessions.",
        "location_text": "Kazanchis, Addis Ababa",
        "price_range": "ETB 500-1200",
        "rating": 4.8,
    },
    {
        "id": "fb-003",
        "name": "NutriLife Consulting",
        "category": "nutrition",
        "description": "Affordable nutrition planning and weight management coaching.",
        "location_text": "CMC, Addis Ababa",
        "price_range": "ETB 400-1000",
        "rating": 4.5,
    },
    {
        "id": "fb-004",
        "name": "Spa Oasis Addis",
        "category": "spa",
        "description": "Relaxing massage and spa treatments in a tranquil setting.",
        "location_text": "Bole, Addis Ababa",
        "price_range": "ETB 600-2000",
        "rating": 4.7,
    },
    {
        "id": "fb-005",
        "name": "Mindful Therapy Center",
        "category": "therapy",
        "description": "Licensed therapists offering individual counseling sessions.",
        "location_text": "Sarbet, Addis Ababa",
        "price_range": "ETB 700-1800",
        "rating": 4.9,
    },
]

# Only these fields are ever sent to the model. Keeping the payload small and
# allow-listed makes the call faster (fewer tokens) and stops internal DB
# columns from leaking into the prompt.
PROMPT_FIELDS = (
    "id",
    "name",
    "category",
    "description",
    "location_text",
    "price_range",
    "rating",
)


class ChatMessage(BaseModel):
    role: str  # "user" or "assistant"
    content: str


class ConciergeRequest(BaseModel):
    message: str
    is_first_message: bool = False
    history: list[ChatMessage] = []  # prior turns in this session, oldest first


class ConciergeResponse(BaseModel):
    intro: str = ""
    reply: str
    provider_id: str | None = None
    provider_name: str | None = None
    data_source: str = "unknown"  # "live" or "fallback"


# --- PROVIDER DATA (with TTL cache) ----------------------------------------
_provider_cache = {"data": None, "source": None, "ts": 0.0}


def fetch_providers():
    """Fetch providers from Supabase, falling back to the local dataset."""
    if supabase_client is not None:
        try:
            db_response = supabase_client.table("providers").select("*").execute()
            if db_response.data:
                return db_response.data, "live"
            logger.info("Supabase returned 0 providers — using fallback dataset")
            return FALLBACK_PROVIDERS, "fallback"
        except Exception:
            logger.exception("Supabase fetch failed — using fallback dataset")
            return FALLBACK_PROVIDERS, "fallback"
    logger.info("Supabase client not initialized — using fallback dataset")
    return FALLBACK_PROVIDERS, "fallback"


def get_providers():
    """Return providers, served from a short-lived in-memory cache.

    Avoids a Supabase round-trip on every single chat turn, which is the main
    avoidable latency on free-tier hosting.
    """
    now = time.monotonic()
    if (
        _provider_cache["data"] is not None
        and (now - _provider_cache["ts"]) < PROVIDER_CACHE_TTL_SECONDS
    ):
        return _provider_cache["data"], _provider_cache["source"]

    data, source = fetch_providers()
    _provider_cache.update(data=data, source=source, ts=now)
    return data, source


def compact_providers(providers):
    """Project to the allow-listed fields the model is allowed to see."""
    return [
        {k: p[k] for k in PROMPT_FIELDS if k in p}
        for p in providers
    ]


def get_onboarding_intro() -> str:
    return (
        "Welcome to Well Circle — Addis Ababa's wellness ecosystem.\n\n"
        "• AI Concierge: Tell me your goal, budget, or neighbourhood and I'll match you instantly.\n"
        "• Circles: Join accountability groups, post daily wins, and track your squad's streaks.\n"
        "• Pay Direct: Book and pay via Telebirr or M-Pesa — no redirects.\n\n"
        "Try: \"Affordable gym near Bole\" · \"Stress relief under 800 ETB\" · \"Nutritionist in CMC\""
    )


@app.get("/")
def health():
    db_status = "not_configured"
    if supabase_client:
        try:
            supabase_client.table("providers").select("id").limit(1).execute()
            db_status = "live"
        except Exception:
            db_status = "unreachable_using_fallback"
    return {
        "status": "ok",
        "service": "well-circle-concierge",
        "database": db_status,
    }


def _resolve_provider(parsed: dict, providers: list) -> tuple[str | None, str | None]:
    """Ground the model's provider choice against real data.

    The model's `provider_name` is never trusted: we accept its `provider_id`
    only if it exists in the dataset, then look up the *authoritative* name from
    that record. Anything unknown is dropped entirely. This is the core
    anti-hallucination guard.
    """
    provider_id = parsed.get("provider_id")

    # Normalise common "no match" shapes the model emits.
    if isinstance(provider_id, str):
        provider_id = provider_id.strip()
        if provider_id.lower() in ("", "null", "none"):
            provider_id = None
    elif provider_id is not None:
        provider_id = str(provider_id)

    if provider_id is None:
        return None, None

    name_by_id = {p["id"]: p.get("name") for p in providers}
    if provider_id not in name_by_id:
        logger.info("Model returned unknown provider_id %r — dropping it", provider_id)
        return None, None

    # Use the real name from our data, not whatever the model wrote.
    return provider_id, name_by_id[provider_id]


# Everything except the provider data, which is appended at request time. Built
# by concatenation (not str.format) so the literal JSON example braces are safe.
SYSTEM_PROMPT_PREFIX = (
    "You are Well Circle's wellness concierge for Addis Ababa. "
    "Your task is to provide expert, empathetic advice first, and helpful service recommendations second.\n\n"
    "INTENT-BASED LOGIC:\n"
    "1. ADVISORY INTENT (Weight, Pain, Stress): Provide a scientifically-backed, actionable tip first. "
    "If you have a relevant provider, suggest them only after the tip. If no provider fits, omit the provider fields.\n"
    "2. SEARCH INTENT (Gyms, Yoga, Spas): Direct the user to the best-match provider immediately.\n\n"
    "ABSOLUTE RULES:\n"
    "1. REPLY MUST BE 2-3 SENTENCES MAX. No fluff, no 'Hello', no 'I am an AI'.\n"
    "2. ONLY recommend a provider that appears in the Available Providers list below, and use its EXACT id. "
    "If nothing in the list genuinely fits, set 'provider_id' and 'provider_name' to null. Never invent providers.\n"
    "3. OUTPUT ONLY RAW JSON. NO MARKDOWN. NO CODE FENCES.\n"
    'REQUIRED FORMAT: {"reply": "<Advice + optional recommendation>", "provider_id": "<id or null>", "provider_name": "<name or null>"}\n\n'
    "Available Providers: "
)


def build_system_prompt(providers) -> str:
    return SYSTEM_PROMPT_PREFIX + json.dumps(compact_providers(providers))

FALLBACK_REPLY = (
    "I'm having trouble matching that request right now - try stating your "
    "health goal, budget, or neighbourhood."
)


@app.post("/ai/concierge", response_model=ConciergeResponse)
def ai_concierge(req: ConciergeRequest):

    # 1. First Message Check - frontend handles its own welcome, backend stays silent
    if req.is_first_message:
        return ConciergeResponse(
            intro="",
            reply="",
            provider_id=None,
            provider_name=None,
            data_source="n/a",
        )

    # 2. Hybrid fetch: cached live Supabase data with automatic fallback
    providers, data_source = get_providers()

    # 3. System prompt — Advice-First, Concierge Logic (compact, grounded payload)
    system_prompt = build_system_prompt(providers)

    try:
        MAX_HISTORY_TURNS = 6
        trimmed_history = req.history[-MAX_HISTORY_TURNS:]

        messages = [{"role": "system", "content": system_prompt}]
        for turn in trimmed_history:
            if turn.role in ("user", "assistant"):
                messages.append({"role": turn.role, "content": turn.content})
        messages.append({"role": "user", "content": req.message})

        response = groq_client.chat.completions.create(
            model=GROQ_MODEL,
            response_format={"type": "json_object"},
            messages=messages,
            temperature=0.2,
            max_tokens=GROQ_MAX_TOKENS,
        )

        raw_text = response.choices[0].message.content.strip()
        parsed = json.loads(raw_text)

        provider_id, provider_name = _resolve_provider(parsed, providers)

        reply = parsed.get("reply") or ""
        if not isinstance(reply, str) or not reply.strip():
            # Model gave us structured-but-empty output; don't render a blank bubble.
            reply = FALLBACK_REPLY

        return ConciergeResponse(
            intro="",
            reply=reply,
            provider_id=provider_id,
            provider_name=provider_name,
            data_source=data_source,
        )

    except json.JSONDecodeError:
        logger.exception("Model returned non-JSON output")
    except Exception:
        logger.exception("AI processing error")

    return ConciergeResponse(
        intro="",
        reply=FALLBACK_REPLY,
        provider_id=None,
        provider_name=None,
        data_source=data_source,
    )


if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port)

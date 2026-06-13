import os
import json
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from groq import Groq
from supabase import create_client, Client
from dotenv import load_dotenv
 
load_dotenv()
 
app = FastAPI(title="Well Circle Concierge - Production")
 
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
 
# --- ENVIRONMENT VARIABLES ---
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_KEY")
 
if not GROQ_API_KEY or not SUPABASE_URL or not SUPABASE_KEY:
    print("⚠️  WARNING: Missing environment configuration variables!")
 
# --- CLIENT INITIALIZATION ---
groq_client = Groq(api_key=GROQ_API_KEY or "fallback_placeholder")
 
try:
    supabase_client: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
except Exception as e:
    print(f"⚠️  Supabase client init failed: {str(e)}")
    supabase_client = None
 
 
# --- LOCAL FALLBACK DATASET ---
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
 
 
def fetch_providers():
    if supabase_client is not None:
        try:
            db_response = supabase_client.table("providers").select("*").execute()
            if db_response.data:
                return db_response.data, "live"
            print("Supabase returned 0 providers, using fallback dataset.")
            return FALLBACK_PROVIDERS, "fallback"
        except Exception as e:
            print(f"Supabase Fetch Error (using fallback): {str(e)}")
            return FALLBACK_PROVIDERS, "fallback"
    else:
        print("Supabase client not initialized, using fallback dataset.")
        return FALLBACK_PROVIDERS, "fallback"
 
 
def get_onboarding_intro() -> str:
    return (
        "🌿 Welcome to the Well Circle Ecosystem! 🌿\n"
        "We build consistency through community and direct access. Here is everything you can do right now:\n\n"
        "🕵️‍♂️ 1. AI Concierge Discovery: Talk directly to me! Tell me what wellness services you need, your area in Addis Ababa, or your ETB budget range, and I will instantly scan our dataset to find your match.\n\n"
        "👥 2. Accountability Circles: Don't train alone. Switch over to our Community tab to join group circles, share daily milestone updates, and view your squad's active consistency feeds.\n\n"
        "💳 3. Direct Payments: Found a fitness center, spa, or yoga hub you like? Book seamlessly with integrated Telebirr and M-Pesa mobile push triggers.\n\n"
        "🔥 4. Daily Check-Ins & Level Ups: Build up your health streak to earn Legacy Points, transition your tier status from 'Seed' up to 'Forest', and earn rewards!\n\n"
        "💬 To start a consultation, try typing: 'I need a luxury spa package around Bole Atlas' or 'Show me an affordable gym option near Stadium'."
    )
 
 
@app.post("/ai/concierge", response_model=ConciergeResponse)
def ai_concierge(req: ConciergeRequest):
 
    # 🎯 BULLETPROOF FIRST TURN FIX:
    # Populates both fields so the frontend never breaks, then hits the brakes immediately.
    if req.is_first_message:
        onboarding_text = get_onboarding_intro()
        return ConciergeResponse(
            intro=onboarding_text,
            reply=onboarding_text,
            provider_id=None,
            provider_name=None,
            data_source="static_intro",
        )
 
    # -----------------------------------------------------------------
    # Everything below here ONLY triggers on message 2, 3, 4, etc.
    # -----------------------------------------------------------------
    providers, data_source = fetch_providers()
 
    system_prompt = (
        "You are Well Circle's wellness concierge for Addis Ababa, Ethiopia. "
        "A user will describe how they feel, what service they want, or their budget in ETB. "
        "Below is a JSON list of wellness providers (gyms, yoga studios, nutritionists, spas, therapists) "
        "with id, name, category, description, location_text, price_range, and rating.\n\n"
        "CRITICAL RULES:\n"
        "1. Pick the ONE provider that best matches the user's stated need, location, or budget.\n"
        "2. Your 'reply' field MUST be ONE TO TWO sentences max. Be punchy, natural, and direct.\n"
        "3. The reply MUST mention the provider's name, its location, and a price reference in ETB.\n"
        "4. If the user's message is unrelated to health, fitness, or wellness, politely state in ONE sentence "
        "what you can help with, and set 'provider_id' and 'provider_name' to null.\n"
        "5. Output STRICTLY as a valid JSON object. No markdown, no preamble, no code fences.\n"
        'JSON format: {"reply": "<one to two sentences>", "provider_id": "<id or null>", "provider_name": "<name or null>"}\n\n'
        f"Providers:\n{json.dumps(providers)}"
    )
 
    try:
        MAX_HISTORY_TURNS = 6
        trimmed_history = req.history[-MAX_HISTORY_TURNS:]
 
        messages = [{"role": "system", "content": system_prompt}]
        for turn in trimmed_history:
            if turn.role in ("user", "assistant"):
                messages.append({"role": turn.role, "content": turn.content})
        messages.append({"role": "user", "content": req.message})
 
        response = groq_client.chat.completions.create(
            model="llama-3.1-8b-instant",
            response_format={"type": "json_object"},
            messages=messages,
            temperature=0.2,
        )
 
        raw_text = response.choices[0].message.content.strip()
        parsed = json.loads(raw_text)
 
        valid_ids = {p["id"] for p in providers}
        provider_id = parsed.get("provider_id")
        provider_name = parsed.get("provider_name")
 
        if provider_id is not None and provider_id not in valid_ids:
            print(f"AI returned unknown provider_id '{provider_id}', nulling it out.")
            provider_id = None
            provider_name = None
 
        return ConciergeResponse(
            intro="",
            reply=parsed.get("reply", "Let's find the best wellness option for you."),
            provider_id=provider_id,
            provider_name=provider_name,
            data_source=data_source,
        )
 
    except (json.JSONDecodeError, Exception) as e:
        print(f"AI processing error: {str(e)}")
        return ConciergeResponse(
            intro="",
            reply="I'm having trouble matching that request right now - try stating your health goal, budget, or neighbourhood.",
            provider_id=None,
            provider_name=None,
            data_source=data_source,
        )
 
 
if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port)
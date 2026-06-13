import os
import json
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from groq import Groq
from dotenv import load_dotenv

load_dotenv()

app = FastAPI(title="Well Circle Concierge")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

api_key = os.getenv("GROQ_API_KEY")
if not api_key:
    print("❌ WARNING: GROQ_API_KEY is missing from environment variables!")

groq_client = Groq(api_key=api_key or "fallback_placeholder_for_build")


# --- LOCAL HACKATHON DATASET ---
# This bypasses Supabase and provides real data directly to the AI
LOCAL_PROVIDERS = [
    {
        "id": "prov_1",
        "name": "Adona Spa Lodge",
        "category": "spa",
        "description": "Premium luxury relaxation, deep tissue massages, and hydrotherapy treatments designed to melt away stress.",
        "location_text": "Addis Ababa",
        "price_range": "2500 - 5000 ETB",
        "rating": 4.9
    },
    {
        "id": "prov_2",
        "name": "Biruh Mind Wellness",
        "category": "therapy",
        "description": "Professional mental health counseling, cognitive behavioral therapy, and mindful stress management sessions.",
        "location_text": "Kazanchis",
        "price_range": "800 - 1500 ETB",
        "rating": 4.9
    },
    {
        "id": "prov_3",
        "name": "Haile Spa & Wellness",
        "category": "spa",
        "description": "High-end steam, sauna, and massage packages with state-of-the-art relaxation facilities.",
        "location_text": "Bole Atlas",
        "price_range": "3000 - 6000 ETB",
        "rating": 4.8
    },
    {
        "id": "prov_4",
        "name": "Nourish Ethiopia",
        "category": "nutrition",
        "description": "Personalized meal plans, dietary assessments, and expert weight management guidance using local ingredients.",
        "location_text": "Sarbet",
        "price_range": "1200 - 3000 ETB",
        "rating": 4.8
    },
    {
        "id": "prov_5",
        "name": "Signature Studio",
        "category": "yoga",
        "description": "A calming space offering beginner to advanced vinyasa yoga, flexibility training, and guided meditation.",
        "location_text": "Bole",
        "price_range": "600 - 1200 ETB per session",
        "rating": 4.8
    },
    {
        "id": "prov_6",
        "name": "MoveMind Running Club",
        "category": "gym",
        "description": "High-energy group running tracks, athletic conditioning, and aerobic endurance training.",
        "location_text": "Addis Ababa Stadium",
        "price_range": "300 - 700 ETB monthly",
        "rating": 4.7
    },
    {
        "id": "prov_7",
        "name": "Roots Fitness",
        "category": "gym",
        "description": "Modern strength training gym equipped with free weights, cardio machines, and professional fitness coaches.",
        "location_text": "Addis Ababa",
        "price_range": "1500 - 3500 ETB monthly",
        "rating": 4.7
    },
    {
        "id": "prov_8",
        "name": "Harmony Wellness",
        "category": "therapy",
        "description": "Holistic psychological support, group therapy, and supportive space for emotional well-being.",
        "location_text": "Addis Ababa",
        "price_range": "1000 - 2000 ETB",
        "rating": 4.6
    },
    {
        "id": "prov_9",
        "name": "Piassa Heritage Hammam",
        "category": "spa",
        "description": "Traditional public bathhouse and authentic cultural steam treatments with an organic scrubbing experience.",
        "location_text": "Piassa (Arada)",
        "price_range": "400 - 900 ETB",
        "rating": 4.5
    },
    {
        "id": "prov_10",
        "name": "Green Plate Kitchen",
        "category": "nutrition",
        "description": "Healthy meal delivery services prepping low-calorie, nutrient-dense Ethiopian fusion food.",
        "location_text": "Megenagna",
        "price_range": "200 - 500 ETB per meal",
        "rating": 4.4
    }
]


class ConciergeRequest(BaseModel):
    message: str


@app.get("/")
def health():
    return {"status": "ok", "service": "well-circle-concierge", "mode": "local-json-mock"}


@app.post("/ai/concierge")
def ai_concierge(req: ConciergeRequest):
    # Use the local variable directly instead of querying Supabase
    providers = LOCAL_PROVIDERS

    system_prompt = (
        "You are Well Circle's wellness concierge for Addis Ababa, Ethiopia. "
        "A user will describe how they feel and/or their budget in ETB. "
        "Below is a JSON list of real wellness providers (gyms, yoga studios, "
        "nutritionists, spas, therapists) with id, name, category, description, "
        "location_text, price_range, and rating.\n\n"
        "Pick the ONE provider that best matches the user's need and budget. "
        "CRITICAL: If the user message is completely irrelevant to wellness, health, or fitness, "
        "or if no providers match, politely tell them what services you CAN help them find "
        "and set 'provider_id' and 'provider_name' to null.\n\n"
        "Respond with ONLY valid JSON, no markdown, no preamble, in this exact shape:\n"
        '{"reply": "<warm, specific 2-3 sentence recommendation explaining WHY '
        "this provider fits, mentioning its name, location, and price in ETB>\", "
        '"provider_id": "<the id of the chosen provider>", '
        '"provider_name": "<the name of the chosen provider>"}\n\n'
        f"Providers:\n{json.dumps(providers)}"
    )

    try:
        response = groq_client.chat.completions.create(
            model="llama-3.1-8b-instant",  # Fast response for live hackathon presentation
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": req.message}
            ],
            temperature=0.2,
        )
        
        raw_text = response.choices[0].message.content.strip()
        parsed = json.loads(raw_text)
        return parsed
        
    except json.JSONDecodeError:
        return {
            "reply": "Sorry, I couldn't process that recommendation properly. Try rephrasing!",
            "provider_id": None,
            "provider_name": None,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Groq AI error: {str(e)}")
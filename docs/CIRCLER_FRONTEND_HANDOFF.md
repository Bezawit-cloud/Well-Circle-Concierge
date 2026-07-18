# Circler AI Concierge — Frontend Integration Handoff
> **For:** Frontend Team  
> **Service:** Well Circle Concierge (Circler chatbot)  
> **Live Backend URL:** `https://well-circle-concierge.onrender.com`  
> **Primary Endpoint:** `POST https://well-circle-concierge.onrender.com/ai/concierge`  
> **Status:** ✅ Memory feature deployed and live  
> **Date:** July 2026

---

## What Changed (Memory Update)

The backend now has **server-side session memory**. Previously, the frontend was responsible for sending the full conversation history with every request (`history: [...]`). That still works for backward compatibility, but the new approach is simpler and more reliable:

- The backend now stores the last **5 messages** (user + assistant combined) per session in Supabase (`chat_memory` table) and in an in-process cache
- The frontend only needs to send a `session_id` — the backend handles the rest
- Memory survives page refreshes and server restarts automatically

---

## Supabase Table Required

Before testing, make sure the `chat_memory` table exists in your Supabase project. Run this in the SQL editor:

```sql
CREATE TABLE IF NOT EXISTS chat_memory (
    session_id TEXT PRIMARY KEY,
    messages JSONB NOT NULL DEFAULT '[]',
    updated_at TIMESTAMP DEFAULT NOW()
);
```

---

## Updated API Contract

### `POST /ai/concierge`

**Request body:**
```json
{
  "message": "I need a gym near Bole with a budget of 2000 ETB",
  "session_id": "abc-123-def-456"
}
```

| Field | Type | Required | Notes |
|---|---|---|---|
| `message` | string | ✅ Yes | The user's typed message |
| `session_id` | string | Recommended | UUID persisted in `localStorage`. If omitted, backend generates one and returns it — but it won't be reused next turn unless you save it |
| `history` | array | ❌ No longer needed | Kept for backward compat only. Ignored if server-side memory exists for the session |

**Response body:**
```json
{
  "reply": "Bole Wellness Hub in Bole offers gym services from ETB 800-2500 — would that fit your budget?",
  "provider_id": "uuid-from-supabase",
  "provider_name": "Bole Wellness Hub",
  "data_source": "live",
  "session_id": "abc-123-def-456"
}
```

| Field | Type | Notes |
|---|---|---|
| `reply` | string | The AI's response, always 2-4 sentences ending with a question |
| `provider_id` | string \| null | Matches `providers.id` in Supabase — use to navigate to `/provider/:id` |
| `provider_name` | string \| null | Display name — shown on the provider link button |
| `data_source` | string | `"live"` or `"fallback"` — for debugging only, not user-facing |
| `session_id` | string | **Always save this** — echo it back on every subsequent request |

---

## What the Frontend Must Do

### 1. Generate and persist a `session_id`

On first load, generate a UUID and store it in `localStorage`. Reuse it for every message in that chat session. Clear it when the user taps "Clear chat."

```js
// On component mount / app load
function getOrCreateSessionId() {
  let sessionId = localStorage.getItem('circler_session_id');
  if (!sessionId) {
    sessionId = crypto.randomUUID(); // built into all modern browsers
    localStorage.setItem('circler_session_id', sessionId);
  }
  return sessionId;
}
```

### 2. Send `session_id` with every request

```js
const sessionId = getOrCreateSessionId();

const res = await fetch("https://well-circle-concierge.onrender.com/ai/concierge", {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({
    message: userMsg,
    session_id: sessionId,
  }),
});

const data = await res.json();
```

### 3. Save the returned `session_id`

The backend may generate a new `session_id` if you didn't send one (e.g. first load before localStorage is set). Always save what comes back:

```js
if (data.session_id) {
  localStorage.setItem('circler_session_id', data.session_id);
}
```

### 4. Clear `session_id` when user clears chat

When the user taps "Clear" in the chat UI, reset both the message history and the session:

```js
function clearChat() {
  setMessages([WELCOME_MESSAGE]);
  localStorage.removeItem('circler_session_id');
  localStorage.removeItem('concierge_messages');
}
```

This ensures the next message starts a fresh session with no memory of the cleared conversation.

---

## What the Frontend Does NOT Need to Do Anymore

- ~~Send `history: [...]` array with every request~~ — backend handles this server-side
- ~~Track `isFirstMessage` boolean~~ — removed from backend entirely
- ~~Send `is_first_message: true/false`~~ — removed from backend entirely
- ~~Manage conversation turns in state for the AI's context~~ — the backend's `chat_memory` table owns this now

---

## How Memory Works (for context)

```
User sends message + session_id
        ↓
Backend looks up session_id in in-process cache (fast)
        ↓ (cache miss)
Backend reads from Supabase chat_memory table
        ↓
Backend builds: [system_prompt] + [last 5 messages] + [current message]
        ↓
Groq LLM generates reply
        ↓
Backend saves [last 5 messages + new turn, trimmed to 5] back to Supabase
        ↓
Returns reply + session_id to frontend
```

Memory limit is **5 messages** (configurable via `MEMORY_MAX_MESSAGES` env var on Render). This keeps the prompt size bounded and latency low.

---

## Complete `handleSend` Example (React)

```jsx
const isSendingRef = useRef(false);

async function handleSend() {
  if (isSendingRef.current) return;
  const userMsg = input.trim();
  if (!userMsg) return;

  isSendingRef.current = true;
  setIsLoading(true);
  setInput('');
  setMessages(prev => [...prev, { id: Date.now(), text: userMsg, sender: 'user' }]);

  try {
    const sessionId = getOrCreateSessionId();

    const res = await fetch("https://well-circle-concierge.onrender.com/ai/concierge", {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message: userMsg, session_id: sessionId }),
    });

    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();

    // Always persist the returned session_id
    if (data.session_id) {
      localStorage.setItem('circler_session_id', data.session_id);
    }

    if (data.reply && data.reply.trim().length > 0) {
      setMessages(prev => [...prev, {
        id: Date.now() + 1,
        text: data.reply,
        sender: 'assistant',
        provider: data.provider_name
          ? { id: data.provider_id, name: data.provider_name, data_source: data.data_source }
          : null,
      }]);
    }
  } catch (err) {
    setMessages(prev => [...prev, {
      id: Date.now() + 1,
      text: "Sorry, I'm having trouble connecting right now. Please try again.",
      sender: 'assistant',
    }]);
  } finally {
    isSendingRef.current = false;
    setIsLoading(false);
  }
}
```

---

## Provider Link Navigation

When `data.provider_id` is non-null and `data.data_source === 'live'`, the provider link should navigate to the existing Provider Detail screen:

```jsx
{msg.provider && (
  <button onClick={() => {
    if (msg.provider.data_source === 'live') {
      navigate(`/provider/${msg.provider.id}`);
    } else {
      navigate('/explore', { state: { search: msg.provider.name } });
    }
  }}>
    📍 {msg.provider.name}
  </button>
)}
```

If `data_source === 'fallback'`, the ID won't match a real Supabase row — navigate to `/explore` with the provider name as a search query instead.

---

## Environment Variables on Render (Backend, already configured)

| Variable | Purpose |
|---|---|
| `GROQ_API_KEY` | Groq LLM API key |
| `SUPABASE_URL` | Supabase project URL |
| `SUPABASE_SERVICE_KEY` | Supabase service role key (bypasses RLS) |
| `MEMORY_MAX_MESSAGES` | Max messages stored per session (default: 5) |
| `GROQ_MODEL` | LLM model (default: `llama-3.1-8b-instant`) |
| `GROQ_MAX_TOKENS` | Max tokens per reply (default: 320) |

---

## Pre-Integration Checklist

- [ ] `chat_memory` table created in Supabase (SQL above)
- [ ] `GET https://well-circle-concierge.onrender.com/` returns `"database": "live"`
- [ ] `session_id` generated via `crypto.randomUUID()` and persisted in `localStorage`
- [ ] `session_id` sent with every `/ai/concierge` request
- [ ] Returned `session_id` saved back to `localStorage` after each response
- [ ] "Clear chat" button removes `circler_session_id` from `localStorage`
- [ ] `history` array removed from request body (no longer needed)
- [ ] `is_first_message` removed from request body (no longer exists in backend)
- [ ] Provider link navigates to `/provider/:id` when `data_source === 'live'`

---

*Circler AI Concierge — Frontend Handoff | Memory Feature | July 2026*

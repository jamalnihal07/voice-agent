# VoiceAgent — AI Appointment Scheduling with Live Monitoring & Warm Transfer

A full-stack voice agent application built with **LiveKit**, **OpenAI**, **Deepgram**, and **Twilio**.

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                        FRONTEND (Next.js)                        │
│                                                                   │
│  ┌─────────────────────┐    ┌──────────────────────────────────┐ │
│  │   Caller Page        │    │     Monitor Dashboard            │ │
│  │  /call               │    │     /monitor                     │ │
│  │                      │    │                                  │ │
│  │  - Voice call UI     │    │  - Live transcript               │ │
│  │  - Transcript view   │    │  - Agent state (listen/think/    │ │
│  │  - Mute/hang up      │    │    speak/pause)                  │ │
│  │                      │    │  - Collected info panel          │ │
│  │                      │    │  - Transfer events               │ │
│  │                      │    │  - Takeover button               │ │
│  │                      │    │  - Post-call summary             │ │
│  └─────────────────────┘    └──────────────────────────────────┘ │
└──────────────────────┬──────────────────────────────────────────┘
                       │ LiveKit WebRTC + Data Channel
┌──────────────────────▼──────────────────────────────────────────┐
│                     BACKEND (Python)                              │
│                                                                   │
│  ┌────────────────────────────────────────────────────────────┐  │
│  │                FastAPI Server (port 8000)                   │  │
│  │  POST /token         — LiveKit JWT generation               │  │
│  │  POST /token/watcher — Full-publish JWT for takeover        │  │
│  │  GET  /twilio/...    — TwiML for warm transfer              │  │
│  └────────────────────────────────────────────────────────────┘  │
│                                                                   │
│  ┌────────────────────────────────────────────────────────────┐  │
│  │              LiveKit Agent (AppointmentAgent)               │  │
│  │                                                             │  │
│  │  STT (Deepgram) → LLM (GPT-4o-mini) → TTS (OpenAI)        │  │
│  │                                                             │  │
│  │  Tools:                                                     │  │
│  │    check_availability() — check booking slots               │  │
│  │    book_appointment()   — store confirmed appointments      │  │
│  │    warm_transfer()      — dial human via Twilio             │  │
│  │                                                             │  │
│  │  MonitoringPublisher — streams events via data channel      │  │
│  └────────────────────────────────────────────────────────────┘  │
│                                                                   │
│  ┌───────────────────┐    ┌───────────────────────────────────┐  │
│  │   BookingTools    │    │         TransferTools             │  │
│  │  (appointments.   │    │  (Twilio outbound call,           │  │
│  │   json storage)   │    │   TwiML gather, accept/decline)   │  │
│  └───────────────────┘    └───────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
                                    │
                              Twilio API
                                    │
                         ┌──────────▼─────────┐
                         │   Human Agent       │
                         │   (real phone)      │
                         └────────────────────┘
```

## Flows Explained

### 1. Appointment Booking
1. Caller connects via `/call` page → LiveKit room is created
2. The LiveKit agent (Alex) greets the caller
3. Alex collects: **name, reason, preferred date/time, contact number**
4. Alex calls `check_availability()` — checks existing bookings for slot conflicts
5. Alex confirms details verbally, then calls `book_appointment()` — stores in JSON
6. Booking confirmation with reference number is read back to the caller
7. Monitor dashboard shows collected fields updating in real time

### 2. Live Monitoring
1. Staff opens `/monitor` and enters the active room name
2. They join as a silent observer (no audio publish until takeover)
3. All agent events are streamed via LiveKit data channel (topic: `monitoring`):
   - `transcript` — each spoken turn
   - `state` — agent_state (listening/thinking/speaking) + current action
   - `collected_info` — structured data as it's gathered
   - `transfer` — warm transfer lifecycle events
   - `summary` — post-call summary when call ends

### 3. Watcher Takeover
1. Watcher clicks **Take Over** in the dashboard
2. Frontend requests a **watcher token** (full audio publish rights) from `/api/watcher-token`
3. A `takeover` data message is sent to the agent via the data channel
4. The agent pauses (mutes its pipeline)
5. The watcher's microphone goes live — they speak directly to the caller
6. Clicking **Release Call** sends `release_takeover` → agent resumes

### 4. Warm Transfer (Twilio)
1. Caller says billing / complaint / "talk to a person"
2. LLM detects intent and calls the `warm_transfer()` tool
3. Agent tells caller to hold
4. Backend dials the human agent's phone via Twilio Programmable Voice
5. TwiML plays the call summary + asks human to press 1 (accept) or 2 (decline)
6. **If accepted** → agent bridges caller to human and exits
7. **If declined** → agent returns to caller: "Our team isn't available right now"

---

## Setup

### Prerequisites
- Python 3.11+
- Node.js 20+
- A LiveKit server (or [LiveKit Cloud](https://cloud.livekit.io) free tier)
- OpenAI API key
- Deepgram API key
- Twilio account (free trial works)

### 1. Clone & Install

```bash
git clone https://github.com/your-org/voiceagent
cd voiceagent
```

**Backend:**
```bash
cd backend
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

**Frontend:**
```bash
cd frontend
npm install
```

### 2. Configure Environment

**Backend** — copy and fill in:
```bash
cp backend/.env.example backend/.env
```

```env
# LiveKit
LIVEKIT_URL=wss://your-project.livekit.cloud
LIVEKIT_API_KEY=APIxxxxxxxx
LIVEKIT_API_SECRET=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx

# OpenAI
OPENAI_API_KEY=sk-...

# Deepgram
DEEPGRAM_API_KEY=...

# Twilio
TWILIO_ACCOUNT_SID=ACxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
TWILIO_AUTH_TOKEN=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
TWILIO_FROM_NUMBER=+1xxxxxxxxxx   # Your Twilio number
HUMAN_AGENT_NUMBER=+1xxxxxxxxxx   # Where to call for transfer

# For Twilio webhooks — expose backend via ngrok:
BACKEND_URL=https://your-ngrok-url.ngrok.io
TWILIO_TRANSFER_TWIML_URL=https://your-ngrok-url.ngrok.io/twilio/transfer-twiml
```

**Frontend:**
```bash
cp frontend/.env.local.example frontend/.env.local
```

```env
NEXT_PUBLIC_BACKEND_URL=http://localhost:8000
NEXT_PUBLIC_LIVEKIT_URL=wss://your-project.livekit.cloud
```

### 3. Run

In separate terminals:

**Terminal 1 — FastAPI server:**
```bash
cd backend
source .venv/bin/activate
uvicorn server:app --host 0.0.0.0 --port 8000 --reload
```

**Terminal 2 — LiveKit Agent worker:**
```bash
cd backend
source .venv/bin/activate
python -m agent.main start
```

**Terminal 3 — Next.js frontend:**
```bash
cd frontend
npm run dev
```

**Terminal 4 — ngrok (for Twilio webhooks in development):**
```bash
ngrok http 8000
# Copy the https URL into BACKEND_URL and TWILIO_TRANSFER_TWIML_URL in backend/.env
```

Open [http://localhost:3000](http://localhost:3000)

---

## Twilio TwiML Setup (Warm Transfer)

The warm transfer dials the human agent and plays a summary prompt. The backend serves the TwiML at `/twilio/transfer-twiml`.

For the outbound call to read the **actual call summary**, set:
```
TWILIO_TRANSFER_TWIML_URL=https://your-ngrok-url.ngrok.io/twilio/transfer-twiml
```

Twilio will POST to `/twilio/transfer-response` when the human presses a digit.

---

## Project Structure

```
voiceagent/
├── backend/
│   ├── agent/
│   │   ├── main.py               # LiveKit worker entrypoint
│   │   ├── appointment_agent.py  # Core voice agent pipeline
│   │   └── monitoring.py         # Real-time event publisher
│   ├── tools/
│   │   ├── booking_tools.py      # Availability check + booking
│   │   └── transfer_tools.py     # Twilio warm transfer
│   ├── utils/
│   │   └── twilio_client.py      # Twilio client wrapper
│   ├── data/
│   │   └── appointments.json     # Booking store (auto-created)
│   ├── server.py                 # FastAPI server
│   └── requirements.txt
│
└── frontend/
    ├── app/
    │   ├── page.tsx              # Landing page
    │   ├── call/page.tsx         # Caller view
    │   ├── monitor/page.tsx      # Monitor view
    │   └── api/                  # Token API routes
    ├── components/
    │   ├── CallRoom.tsx          # LiveKit voice call component
    │   └── MonitorRoom.tsx       # Full monitoring dashboard
    └── ...
```

---

## Extending

- **Real database**: Replace `appointments.json` in `BookingTools` with PostgreSQL/Supabase
- **Cal.com integration**: Swap `check_availability`/`book_appointment` for Cal.com API calls
- **More tools**: Add `reschedule_appointment`, `cancel_appointment`, `lookup_appointment`
- **Multilingual**: Add language detection → pass locale to Deepgram STT + OpenAI TTS voice
- **Interruption handling**: The LiveKit pipeline supports `allow_interruptions=True` by default
- **SIP/PSTN**: For true caller telephony, configure a LiveKit SIP trunk connected to Twilio


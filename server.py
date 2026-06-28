"""
FastAPI Server - Token generation, room management, Twilio webhooks
"""
import logging
import os
import uuid
from datetime import datetime
from typing import Optional

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from livekit import api
from pydantic import BaseModel

app = FastAPI(title="VoiceAgent API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Restrict in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

logger = logging.getLogger("server")

LIVEKIT_URL = os.environ.get("LIVEKIT_URL", "ws://localhost:7880")
LIVEKIT_API_KEY = os.environ.get("LIVEKIT_API_KEY", "devkey")
LIVEKIT_API_SECRET = os.environ.get("LIVEKIT_API_SECRET", "secret")


class TokenRequest(BaseModel):
    room_name: Optional[str] = None
    identity: Optional[str] = None
    is_monitor: bool = False


class TokenResponse(BaseModel):
    token: str
    room_name: str
    livekit_url: str


@app.get("/health")
async def health():
    return {"status": "ok", "timestamp": datetime.now().isoformat()}


@app.post("/token", response_model=TokenResponse)
async def create_token(req: TokenRequest):
    """Create a LiveKit access token for caller or monitor"""
    room_name = req.room_name or f"appointment-{uuid.uuid4().hex[:8]}"
    identity = req.identity or (
        f"monitor-{uuid.uuid4().hex[:6]}" if req.is_monitor else f"caller-{uuid.uuid4().hex[:6]}"
    )

    lk_api = api.LiveKitAPI(
        url=LIVEKIT_URL,
        api_key=LIVEKIT_API_KEY,
        api_secret=LIVEKIT_API_SECRET,
    )

    grants = api.VideoGrants(
        room_join=True,
        room=room_name,
        can_publish=not req.is_monitor,       # monitors only subscribe
        can_subscribe=True,
        can_publish_data=True,                 # allow data channel for takeover
        room_record=False,
    )

    token = (
        api.AccessToken(api_key=LIVEKIT_API_KEY, api_secret=LIVEKIT_API_SECRET)
        .with_identity(identity)
        .with_name(identity)
        .with_grants(grants)
        .to_jwt()
    )

    await lk_api.aclose()

    return TokenResponse(
        token=token,
        room_name=room_name,
        livekit_url=LIVEKIT_URL,
    )


@app.post("/token/watcher")
async def create_watcher_token(req: TokenRequest):
    """Create a token for a watcher who can take over the call (publish audio)"""
    room_name = req.room_name
    if not room_name:
        raise HTTPException(400, "room_name required for watcher token")

    identity = f"watcher-{uuid.uuid4().hex[:6]}"

    lk_api = api.LiveKitAPI(
        url=LIVEKIT_URL,
        api_key=LIVEKIT_API_KEY,
        api_secret=LIVEKIT_API_SECRET,
    )

    # Watcher gets full publish rights so they can speak to the caller
    grants = api.VideoGrants(
        room_join=True,
        room=room_name,
        can_publish=True,
        can_subscribe=True,
        can_publish_data=True,
    )

    token = (
        api.AccessToken(api_key=LIVEKIT_API_KEY, api_secret=LIVEKIT_API_SECRET)
        .with_identity(identity)
        .with_name("Human Watcher")
        .with_grants(grants)
        .to_jwt()
    )

    await lk_api.aclose()

    return {"token": token, "identity": identity, "room_name": room_name}


@app.get("/rooms/{room_name}/participants")
async def list_participants(room_name: str):
    """List participants in a room"""
    lk_api = api.LiveKitAPI(
        url=LIVEKIT_URL,
        api_key=LIVEKIT_API_KEY,
        api_secret=LIVEKIT_API_SECRET,
    )
    try:
        participants = await lk_api.room.list_participants(
            api.ListParticipantsRequest(room=room_name)
        )
        await lk_api.aclose()
        return {
            "participants": [
                {"identity": p.identity, "name": p.name, "state": str(p.state)}
                for p in participants.participants
            ]
        }
    except Exception as e:
        await lk_api.aclose()
        raise HTTPException(500, str(e))


@app.post("/twilio/status")
async def twilio_status_callback(request: Request):
    """Twilio status callback for transfer call events"""
    form = await request.form()
    call_sid = form.get("CallSid")
    status = form.get("CallStatus")
    logger.info(f"Twilio status update: {call_sid} -> {status}")
    return Response(content="", media_type="text/plain")


@app.get("/twilio/transfer-twiml")
async def transfer_twiml(summary: str = "", agent_name: str = "Alex"):
    """
    TwiML for the outbound call to the human agent.
    Speaks the summary and asks them to press 1 to accept or 2 to decline.
    """
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Say voice="Polly.Joanna">
        Hello, this is {agent_name} from MedCare Clinic. 
        I have a caller on the line who needs assistance. 
        {summary}
        Press 1 to accept this call, or press 2 if you are unavailable.
    </Say>
    <Gather numDigits="1" action="/twilio/transfer-response" method="POST" timeout="10">
        <Say voice="Polly.Joanna">Please press 1 to accept or 2 to decline.</Say>
    </Gather>
    <Say voice="Polly.Joanna">We didn't receive a response. The caller will be informed you are unavailable.</Say>
</Response>"""
    return Response(content=xml, media_type="application/xml")


@app.post("/twilio/transfer-response")
async def transfer_response(request: Request):
    """Handle the digit response from the human agent"""
    form = await request.form()
    digits = form.get("Digits", "")

    if digits == "1":
        xml = """<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Say voice="Polly.Joanna">Connecting you now. Thank you for accepting.</Say>
</Response>"""
    else:
        xml = """<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Say voice="Polly.Joanna">Understood. The caller will be informed. Goodbye.</Say>
    <Hangup/>
</Response>"""

    return Response(content=xml, media_type="application/xml")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)

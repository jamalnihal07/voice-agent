"""
AppointmentAgent - Core voice agent handling conversation, booking, and transfers
"""
import asyncio
import json
import logging
from datetime import datetime, timedelta
from typing import Optional

from livekit import rtc
from livekit.agents import JobContext, llm
from livekit.agents.pipeline import VoicePipelineAgent
from livekit.plugins import openai, deepgram, silero

from ..tools.booking_tools import BookingTools
from ..tools.transfer_tools import TransferTools
from ..utils.twilio_client import TwilioClient
from .monitoring import MonitoringPublisher

logger = logging.getLogger("appointment-agent")

SYSTEM_PROMPT = """You are Alex, a friendly and professional appointment scheduling assistant for MedCare Clinic.

Your primary goals:
1. Greet callers warmly and understand what they need
2. Book appointments by collecting: name, reason for visit, preferred date/time, and contact number
3. Check availability and confirm bookings
4. Transfer to a human agent when caller asks about billing, complaints, or requests a human

## Conversation Flow:
- Start with a warm greeting
- Understand their need (appointment booking, information, or human agent)
- For appointments: collect info naturally in conversation, don't interrogate
- Always confirm details before booking
- Speak naturally and concisely — this is a phone call

## Transfer Triggers (use warm_transfer tool immediately):
- "billing", "invoice", "payment", "charge"
- "complaint", "complain", "unhappy", "upset"
- "speak to someone", "talk to a person", "human", "representative", "manager"

## Tools Available:
- check_availability: Check if a time slot is available
- book_appointment: Confirm and store the appointment
- warm_transfer: Transfer to a human agent via Twilio

## Important:
- Keep responses SHORT — max 2-3 sentences per turn
- Speak dates naturally: "Tuesday the 15th at 2pm" not "2024-01-15T14:00"
- If availability check fails, offer alternative times
- Always read back the full booking confirmation when done
- Current date/time context will be provided in each message
"""


class AppointmentAgent:
    def __init__(
        self,
        room: rtc.Room,
        participant: rtc.RemoteParticipant,
        monitor: MonitoringPublisher,
    ):
        self.room = room
        self.participant = participant
        self.monitor = monitor
        self.booking_tools = BookingTools()
        self.transfer_tools = TransferTools()
        self.twilio_client = TwilioClient()
        self.collected_info = {}
        self.call_transcript = []
        self.call_start_time = datetime.now()
        self._pipeline: Optional[VoicePipelineAgent] = None
        self._taken_over = False

    async def start(self, ctx: JobContext, participant: rtc.RemoteParticipant):
        """Start the voice pipeline agent"""

        # Subscribe to takeover events from monitoring data channel
        @self.room.on("data_received")
        def on_data(data_packet: rtc.DataPacket):
            asyncio.ensure_future(self._handle_data(data_packet))

        # Build function context with our tools
        fnc_ctx = self._build_function_context()

        # Create the pipeline agent
        self._pipeline = VoicePipelineAgent(
            vad=silero.VAD.load(),
            stt=deepgram.STT(model="nova-2"),
            llm=openai.LLM(model="gpt-4o-mini"),
            tts=openai.TTS(voice="alloy"),
            fnc_ctx=fnc_ctx,
            chat_ctx=self._build_initial_context(),
        )

        # Wire up event handlers
        self._pipeline.on("user_speech_committed", self._on_user_speech)
        self._pipeline.on("agent_speech_committed", self._on_agent_speech)
        self._pipeline.on("function_calls_collected", self._on_function_calls)
        self._pipeline.on("function_calls_finished", self._on_function_calls_finished)

        # Start the agent
        self._pipeline.start(ctx.room, participant)
        await self.monitor.publish_state("connected", "listening", "Waiting for caller")

        # Greet the caller
        await self._pipeline.say(
            "Hello! Thank you for calling MedCare Clinic. I'm Alex, your scheduling assistant. "
            "How can I help you today?",
            allow_interruptions=True,
        )

        # Keep running until call ends
        await self._pipeline.aclose()
        await self._on_call_ended()

    def _build_initial_context(self) -> llm.ChatContext:
        ctx = llm.ChatContext()
        ctx.append(
            role="system",
            text=SYSTEM_PROMPT + f"\n\nCurrent date/time: {datetime.now().strftime('%A, %B %d, %Y at %I:%M %p')}",
        )
        return ctx

    def _build_function_context(self) -> llm.FunctionContext:
        fnc_ctx = llm.FunctionContext()

        @fnc_ctx.ai_callable(
            description="Check if a specific appointment slot is available. Call this before booking."
        )
        async def check_availability(
            date: str,
            time: str,
            reason: str = "General visit",
        ) -> str:
            """
            date: Date in format YYYY-MM-DD
            time: Time in format HH:MM (24-hour)
            reason: Reason for the visit
            """
            await self.monitor.publish_state("connected", "thinking", f"Checking availability for {date} at {time}...")
            result = await self.booking_tools.check_availability(date, time, reason)
            return result

        @fnc_ctx.ai_callable(
            description="Book an appointment after confirming details with the caller."
        )
        async def book_appointment(
            patient_name: str,
            reason: str,
            date: str,
            time: str,
            phone: str,
        ) -> str:
            """
            patient_name: Full name of the patient
            reason: Reason for the visit
            date: Date in format YYYY-MM-DD
            time: Time in format HH:MM (24-hour)
            phone: Contact phone number
            """
            await self.monitor.publish_state("connected", "thinking", f"Booking appointment for {patient_name}...")
            self.collected_info = {
                "name": patient_name,
                "reason": reason,
                "date": date,
                "time": time,
                "phone": phone,
            }
            await self.monitor.publish_collected_info(self.collected_info)
            result = await self.booking_tools.book_appointment(
                patient_name, reason, date, time, phone
            )
            return result

        @fnc_ctx.ai_callable(
            description="Initiate a warm transfer to a human agent via Twilio when caller asks for billing, complaints, or a human."
        )
        async def warm_transfer(
            reason: str,
        ) -> str:
            """
            reason: Brief reason for the transfer (e.g., 'billing inquiry', 'complaint')
            """
            await self.monitor.publish_state("transferring", "speaking", f"Initiating warm transfer: {reason}")

            # Pause agent audio while we handle transfer
            summary = self._build_call_summary(reason)

            result = await self.transfer_tools.initiate_warm_transfer(
                room=self.room,
                pipeline=self._pipeline,
                caller_summary=summary,
                transfer_reason=reason,
            )
            return result

        return fnc_ctx

    def _build_call_summary(self, transfer_reason: str) -> str:
        transcript_text = "\n".join(
            [f"{t['role'].upper()}: {t['text']}" for t in self.call_transcript[-10:]]
        )
        info = self.collected_info
        return (
            f"Transfer reason: {transfer_reason}. "
            + (f"Caller name: {info.get('name', 'unknown')}. " if info.get("name") else "")
            + f"Call duration: {int((datetime.now() - self.call_start_time).total_seconds() / 60)} minutes. "
            f"Recent conversation: {transcript_text[-500:]}"
        )

    async def _on_user_speech(self, msg: llm.ChatMessage):
        text = msg.content if isinstance(msg.content, str) else str(msg.content)
        self.call_transcript.append({"role": "caller", "text": text, "ts": datetime.now().isoformat()})
        await self.monitor.publish_transcript("caller", text)
        await self.monitor.publish_state("connected", "thinking", "Processing...")

    async def _on_agent_speech(self, msg: llm.ChatMessage):
        text = msg.content if isinstance(msg.content, str) else str(msg.content)
        self.call_transcript.append({"role": "agent", "text": text, "ts": datetime.now().isoformat()})
        await self.monitor.publish_transcript("agent", text)
        await self.monitor.publish_state("connected", "speaking", "Speaking...")

    async def _on_function_calls(self, calls):
        for call in calls:
            await self.monitor.publish_state(
                "connected", "thinking", f"Calling {call.call_info.name}..."
            )

    async def _on_function_calls_finished(self, calls):
        await self.monitor.publish_state("connected", "listening", "Listening...")

    async def _handle_data(self, data_packet: rtc.DataPacket):
        """Handle data messages from the monitoring dashboard (takeover)"""
        try:
            data = json.loads(data_packet.data.decode())
            if data.get("type") == "takeover":
                logger.info("Watcher is taking over the call")
                self._taken_over = True
                if self._pipeline:
                    # Mute the agent
                    await self._pipeline.mute()
                await self.monitor.publish_state("connected", "paused", "Human watcher has taken over")
            elif data.get("type") == "release_takeover":
                logger.info("Watcher released the call back to agent")
                self._taken_over = False
                if self._pipeline:
                    await self._pipeline.unmute()
                await self.monitor.publish_state("connected", "listening", "Agent resumed")
        except Exception as e:
            logger.error(f"Error handling data: {e}")

    async def _on_call_ended(self):
        """Generate and publish post-call summary"""
        await self.monitor.publish_state("ended", "idle", "Call ended")

        # Generate summary via LLM
        summary = await self._generate_summary()
        await self.monitor.publish_summary(summary)
        logger.info("Call ended, summary published")

    async def _generate_summary(self) -> dict:
        """Generate a structured post-call summary"""
        transcript_text = "\n".join(
            [f"{t['role'].upper()}: {t['text']}" for t in self.call_transcript]
        )
        duration = int((datetime.now() - self.call_start_time).total_seconds())

        try:
            client = openai.LLM(model="gpt-4o-mini")
            ctx = llm.ChatContext()
            ctx.append(
                role="user",
                text=f"""Summarize this call transcript in JSON with keys:
- outcome: "appointment_booked" | "transferred" | "information_only" | "abandoned"
- patient_name: string or null
- appointment_details: object with date/time/reason or null
- key_topics: list of strings
- sentiment: "positive" | "neutral" | "negative"
- summary: 2-3 sentence summary

Transcript:
{transcript_text}

Return ONLY valid JSON.""",
            )
            stream = client.chat(ctx)
            full_response = ""
            async for chunk in stream:
                full_response += chunk.choices[0].delta.content or ""

            summary_data = json.loads(full_response.strip())
        except Exception as e:
            logger.error(f"Summary generation failed: {e}")
            summary_data = {
                "outcome": "unknown",
                "summary": "Call completed.",
                "key_topics": [],
                "sentiment": "neutral",
            }

        summary_data["duration_seconds"] = duration
        summary_data["collected_info"] = self.collected_info
        summary_data["transcript"] = self.call_transcript
        return summary_data

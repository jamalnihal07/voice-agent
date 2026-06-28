"""
TransferTools - Warm transfer to human agent via Twilio
"""
import asyncio
import logging
import os
from typing import Optional

from livekit import rtc
from livekit.agents.pipeline import VoicePipelineAgent
from twilio.rest import Client as TwilioClient

logger = logging.getLogger("transfer-tools")


class TransferTools:
    def __init__(self):
        self.account_sid = os.environ.get("TWILIO_ACCOUNT_SID", "")
        self.auth_token = os.environ.get("TWILIO_AUTH_TOKEN", "")
        self.from_number = os.environ.get("TWILIO_FROM_NUMBER", "")
        self.human_agent_number = os.environ.get("HUMAN_AGENT_NUMBER", "")
        self._twilio: Optional[TwilioClient] = None

    def _get_twilio(self) -> TwilioClient:
        if not self._twilio:
            self._twilio = TwilioClient(self.account_sid, self.auth_token)
        return self._twilio

    async def initiate_warm_transfer(
        self,
        room: rtc.Room,
        pipeline: VoicePipelineAgent,
        caller_summary: str,
        transfer_reason: str,
    ) -> str:
        """
        Warm transfer flow:
        1. Notify caller we're connecting them
        2. Call human agent via Twilio
        3. Speak summary to human agent
        4. Human accepts or declines
        5a. If accepted: bridge caller & human, agent exits
        5b. If declined: return to caller, explain unavailability
        """
        if not self.account_sid or not self.human_agent_number:
            return (
                "I'm sorry, I wasn't able to connect you to a team member right now. "
                "Our team is not available. I can take a message or schedule a callback."
            )

        # Tell caller we're transferring
        await pipeline.say(
            "I'm going to connect you with one of our team members right now. "
            "Please hold for just a moment while I bring them on.",
            allow_interruptions=False,
        )

        # Import monitoring publisher from room context
        from ..agent.monitoring import MonitoringPublisher
        monitor = MonitoringPublisher(room)
        await monitor.publish_transfer_event("initiated", f"Calling {self.human_agent_number}")

        try:
            twilio = self._get_twilio()

            # Create a Twilio call to the human agent
            # The TwiML URL should serve a prompt that plays the summary and asks them to press 1 to accept
            twiml_url = os.environ.get(
                "TWILIO_TRANSFER_TWIML_URL",
                "https://handler.twilio.com/twiml/EH_placeholder"  # Replace with your TwiML Bin URL
            )

            call = twilio.calls.create(
                to=self.human_agent_number,
                from_=self.from_number,
                url=twiml_url,
                status_callback=os.environ.get("BACKEND_URL", "http://localhost:8000") + "/twilio/status",
                status_callback_event=["completed", "no-answer", "busy", "failed"],
                timeout=30,
            )

            logger.info(f"Twilio call initiated: {call.sid}")
            await monitor.publish_transfer_event("ringing", f"Ringing human agent (SID: {call.sid})")

            # Wait for the human agent to pick up (poll call status)
            accepted = await self._wait_for_human_response(twilio, call.sid)

            if accepted:
                await monitor.publish_transfer_event("accepted", "Human agent accepted the call")
                # In a full SIP/LiveKit integration, here we'd bridge the SIP participant
                # For demo: return success message to agent to speak
                return (
                    "Great news! I've connected you with a team member. "
                    "They'll be with you in just a moment. Thank you for calling MedCare Clinic, and have a wonderful day!"
                )
            else:
                await monitor.publish_transfer_event("declined", "Human agent declined or unavailable")
                return (
                    "I'm sorry, our team members are all busy at the moment. "
                    "I'd be happy to schedule a callback for you, or you can call back during business hours. "
                    "Is there anything else I can help you with?"
                )

        except Exception as e:
            logger.error(f"Transfer failed: {e}")
            await monitor.publish_transfer_event("failed", str(e))
            return (
                "I'm sorry, I wasn't able to complete the transfer right now. "
                "Would you like me to have someone call you back, or can I help you with something else?"
            )

    async def _wait_for_human_response(
        self, twilio: TwilioClient, call_sid: str, timeout: int = 45
    ) -> bool:
        """Poll Twilio to see if human agent answered and accepted"""
        import asyncio

        for _ in range(timeout // 3):
            await asyncio.sleep(3)
            try:
                call = twilio.calls(call_sid).fetch()
                status = call.status
                logger.debug(f"Call {call_sid} status: {status}")

                if status == "in-progress":
                    # Human picked up — in real integration, check keypress (digits) from TwiML gather
                    # For demo, treat answered as accepted
                    await asyncio.sleep(5)  # Let summary play
                    return True
                elif status in ("completed", "busy", "no-answer", "failed", "canceled"):
                    return False
            except Exception as e:
                logger.error(f"Error checking call status: {e}")
                return False

        return False

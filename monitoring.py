"""
MonitoringPublisher - Real-time event streaming to dashboard via LiveKit data channel
"""
import json
import logging
from datetime import datetime
from typing import Optional

from livekit import rtc

logger = logging.getLogger("monitoring")


class MonitoringPublisher:
    """Publishes agent state and events to all room participants via data channel"""

    def __init__(self, room: rtc.Room):
        self.room = room

    async def _publish(self, event_type: str, payload: dict):
        """Send a JSON event to all participants"""
        message = json.dumps(
            {
                "type": event_type,
                "ts": datetime.now().isoformat(),
                **payload,
            }
        ).encode()
        try:
            await self.room.local_participant.publish_data(
                message,
                reliable=True,
                topic="monitoring",
            )
        except Exception as e:
            logger.error(f"Failed to publish monitoring event: {e}")

    async def publish_state(
        self,
        call_status: str,
        agent_state: str,
        action: str,
    ):
        """
        call_status: connected | transferring | ended
        agent_state: listening | thinking | speaking | paused | idle
        action: human-readable description of current action
        """
        await self._publish(
            "state",
            {
                "call_status": call_status,
                "agent_state": agent_state,
                "action": action,
            },
        )

    async def publish_transcript(self, role: str, text: str):
        """
        role: caller | agent | watcher
        text: transcript text
        """
        await self._publish(
            "transcript",
            {
                "role": role,
                "text": text,
            },
        )

    async def publish_collected_info(self, info: dict):
        """Publish the structured data collected during conversation"""
        await self._publish("collected_info", {"data": info})

    async def publish_summary(self, summary: dict):
        """Publish the post-call summary"""
        await self._publish("summary", {"data": summary})

    async def publish_transfer_event(self, event: str, details: str = ""):
        """Publish transfer-specific events"""
        await self._publish(
            "transfer",
            {
                "event": event,  # initiated | ringing | accepted | declined | failed
                "details": details,
            },
        )

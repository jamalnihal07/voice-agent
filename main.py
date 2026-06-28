"""
VoiceAgent Main - LiveKit Agents entry point
"""
import asyncio
import logging
from livekit.agents import AutoSubscribe, JobContext, WorkerOptions, cli, llm
from livekit.agents.pipeline import VoicePipelineAgent
from livekit.plugins import openai, deepgram, silero

from .appointment_agent import AppointmentAgent
from .monitoring import MonitoringPublisher

logger = logging.getLogger("voice-agent")


async def entrypoint(ctx: JobContext):
    logger.info(f"Starting agent for room: {ctx.room.name}")

    # Connect to the room
    await ctx.connect(auto_subscribe=AutoSubscribe.AUDIO_ONLY)

    # Wait for the first participant (the caller)
    participant = await ctx.wait_for_participant()
    logger.info(f"Caller joined: {participant.identity}")

    # Initialize monitoring publisher
    monitor = MonitoringPublisher(ctx.room)

    # Build the agent
    agent = AppointmentAgent(
        room=ctx.room,
        participant=participant,
        monitor=monitor,
    )

    # Start the agent
    await agent.start(ctx, participant)


if __name__ == "__main__":
    cli.run_app(
        WorkerOptions(
            entrypoint_fnc=entrypoint,
        )
    )

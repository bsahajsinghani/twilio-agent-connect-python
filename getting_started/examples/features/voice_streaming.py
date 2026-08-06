"""
Voice Streaming Example with OpenAI Agents SDK

Streaming reduces latency by sending LLM tokens immediately to the caller.

Performance comparison (streaming vs non-streaming):
- Streaming: Caller hears first words in ~0.5-0.7s (first token latency)
- Non-streaming: Caller waits ~1.0-1.5s for full LLM response before hearing anything
- Result: ~40-50% faster time-to-first-audio with streaming
"""

from collections.abc import AsyncGenerator
from typing import Any

from agents import Agent, Runner, set_tracing_disabled
from dotenv import load_dotenv

from tac import TAC, TACConfig
from tac.channels.voice import VoiceChannel
from tac.models.session import ConversationSession
from tac.models.tac import TACMemoryResponse
from tac.server import TACFastAPIServer

load_dotenv()
set_tracing_disabled(True)

# --- LLM provider ---
# Default: OpenAI via the Agents SDK (gpt-4o-mini).
# To swap to SageMaker or Bedrock, replace the stream_tokens() body below
# with a boto3 / bedrock-runtime streaming call and yield tokens the same way.

# --- STT model ---
# transcription_provider: "deepgram" (default) or "google"
# speech_model options:
#   Deepgram: "nova-2-general" (default), "nova-3-general", "flux" (turn-aware, lower latency)
#   Google:   "telephony" (default), "latest_long"
# Leave as None to use ConversationRelay's defaults.

# --- Memory mode ---
# "never"  — no memory retrieval (lowest latency, good for stateless agents)
# "always" — Recall API on every turn (semantic search, ~750ms per turn)
# "once"   — List Observations API once at call start, cached for the call
#             (best for voice: static memory block enables LLM prefix caching)

tac = TAC(config=TACConfig.from_env())
voice_channel = VoiceChannel(
    tac,
    config={
        "memory_mode": "never",
        # "transcription_provider": "deepgram",
        # "speech_model": "flux",
    },
)

SYSTEM_INSTRUCTIONS = (
    "You are a voice assistant speaking with a user over the phone. "
    "Keep responses short and conversational — a sentence or two. "
    "Do not use markdown, asterisks, bullets, or emojis; your words will be spoken aloud."
)

conversation_history: dict[str, list[Any]] = {}


async def handle_message_ready(
    user_message: str, context: ConversationSession, memory_response: TACMemoryResponse | None
) -> None:
    """Stream voice responses through the OpenAI Agents SDK.

    Returns None and manually calls send_response() with an async generator
    so tokens are sent to the caller as they arrive from the LLM.
    """
    conv_id = context.conversation_id

    # Inject memory into system prompt if available (used with memory_mode "always" or "once")
    system = SYSTEM_INSTRUCTIONS
    if memory_response:
        obs_text = "\n".join(
            o.content for o in memory_response.observations if hasattr(o, "content") and o.content
        )
        sum_text = "\n".join(
            s.content for s in memory_response.summaries if hasattr(s, "content") and s.content
        )
        if obs_text or sum_text:
            memory_block = "\n\n".join(filter(None, [obs_text, sum_text]))
            system = f"{SYSTEM_INSTRUCTIONS}\n\nUser profile:\n{memory_block}"

    agent = Agent(name="Voice Assistant", instructions=system)
    history = conversation_history.get(conv_id, [])
    agent_input = history + [{"role": "user", "content": user_message}]

    async def stream_tokens() -> AsyncGenerator[str, None]:
        result = Runner.run_streamed(agent, agent_input)
        async for event in result.stream_events():
            if event.type == "raw_response_event" and hasattr(event.data, "delta"):
                yield event.data.delta
        conversation_history[conv_id] = result.to_input_list()

    await voice_channel.send_response(conv_id, stream_tokens())


tac.on_message_ready(handle_message_ready)

if __name__ == "__main__":
    server = TACFastAPIServer(tac=tac, voice_channel=voice_channel)
    server.start()

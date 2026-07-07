"""Voice channel configuration."""

from pydantic import BaseModel, Field

from tac.models.memory import MemoryMode
from tac.session import SessionManager, ThreadSafeSessionManager


class VoiceChannelConfig(BaseModel):
    """
    Configuration for Voice channel.

    Attributes:
        session_manager: SessionManager for tracking and canceling in-flight tasks.
            Defaults to ThreadSafeSessionManager for automatic task cancellation on
            interrupts and new prompts. Set to None only for debugging/testing.
        memory_mode: Memory retrieval mode. Default is "never".
            - "always": Retrieve memory for every message with the query string
            - "once": Retrieve memory once at conversation start with empty query and cache it.
                     Cache is invalidated when conversation becomes INACTIVE.
            - "never": Skip memory retrieval
    """

    model_config = {"arbitrary_types_allowed": True}

    session_manager: SessionManager | None = Field(
        default_factory=ThreadSafeSessionManager,
        description=(
            "SessionManager for task cancellation. Defaults to ThreadSafeSessionManager. "
            "Set to None only for debugging/testing."
        ),
    )
    memory_mode: MemoryMode = Field(
        default="never",
        description="Memory retrieval mode for this channel",
    )
    transcription_provider: str | None = Field(
        default=None,
        description="STT provider for ConversationRelay (e.g. 'deepgram', 'google').",
    )
    speech_model: str | None = Field(
        default=None,
        description="STT model for ConversationRelay (e.g. 'flux', 'nova-3-general').",
    )

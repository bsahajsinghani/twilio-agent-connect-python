# Getting Started with Twilio Agent Connect (TAC)

This guide will walk you through setting up and running your first TAC application.

## Prerequisites

1. **Python 3.10+** installed
2. **Twilio account** with a phone number that has both **Voice** and **Messaging** capabilities enabled. Messaging requires [A2P 10DLC registration](https://www.twilio.com/docs/messaging/compliance/a2p-10dlc) for US long-code numbers before the number can send SMS.
3. **API key** for the SDK you're using (e.g., OpenAI API key)
4. **ngrok** or similar tunneling tool for local development

## Step 1: Set Up Twilio Services

You need to create a Twilio Conversation Configuration and Memory Store before using TAC.

**Option 1: Use the Setup Wizard**

Run the interactive wizard to automatically create services:

```bash
make setup
# Open http://localhost:8080 and follow the wizard
```

The wizard will:
- Create a Twilio Conversation Configuration and Memory Store
- Generate a `.env` file with all required credentials

**Option 2: Manual Setup**

Create services manually through the [Twilio Console](https://1console.twilio.com/). For a complete walkthrough — including which credentials to gather, how to configure SMS and Voice webhooks, and step-by-step Console navigation — see the [TAC Quickstart](https://www.twilio.com/docs/platform/tac/quickstart).

## Step 2: Choose an Example

TAC includes examples for different integration approaches:

### `overview.py` - Framework-Agnostic Pattern

Learn the core pattern for manually extracting and injecting TAC memory into **any** agent framework:
- Works with OpenAI, AWS Bedrock, Azure AI, GCP Vertex AI, custom agents
- Full control over memory formatting and injection
- Uses TAC's official `MemoryPromptBuilder` utility
- **Start here** to understand how TAC memory works

### `partners/` - Partner SDK Examples

Production-ready examples integrating TAC with partner SDKs:
- **`openai_chat_completions.py`**: OpenAI Chat Completions API with automatic memory injection via `with_tac_memory()`
- **`openai_responses_api.py`**: OpenAI Responses API with automatic memory injection
- **`aws_bedrock_agent.py`**: AWS Bedrock Agent integration
- **`aws_bedrock_agentcore.py`**: AWS Bedrock AgentCore integration
- **`aws_strands.py`**: AWS Strands agents integration

### `features/` - Feature Examples

- **`voice_streaming.py`**: Stream LLM responses token-by-token for ~40-50% faster time-to-first-audio
- **`handoff.py`**: Hand the conversation off to a human agent via a Twilio Studio Flow (works on voice and SMS)
- **`rcs.py`**: RCS (Rich Communication Services) channel with automatic memory retrieval
- **`whatsapp.py`**: WhatsApp channel with automatic memory retrieval
- **`outbound.py`**: Agent-initiated outbound conversations via SMS, RCS, WhatsApp, or Voice channels
- **`chat/`**: Twilio Conversations (Chat) channel examples
- **`relay_only.py`**: ConversationRelay-only mode — get started with voice using just ConversationRelay
- **`dashboard/`**: Real-time observation dashboard for monitoring active sessions, message history, and agent context during development

## Step 3: Run an Example

### Configure Environment Variables

```bash
cd getting_started/examples
cp .env.example .env
# Edit .env with your credentials
```

See the **Environment Variables** section below for details.

### Run the Server

`uv run` auto-syncs this repo's default dependency groups (`examples` and
`dev`) on first use — no separate install step required. `load_dotenv()`
walks up from the script's directory, so it'll find
`getting_started/examples/.env` from any working directory.

```bash
uv run getting_started/examples/overview.py
uv run getting_started/examples/partners/openai_chat_completions.py
uv run getting_started/examples/partners/openai_responses_api.py
uv run getting_started/examples/partners/aws_bedrock_agent.py
uv run getting_started/examples/partners/aws_bedrock_agentcore.py
uv run getting_started/examples/partners/aws_strands.py
uv run getting_started/examples/features/voice_streaming.py
uv run getting_started/examples/features/handoff.py
uv run getting_started/examples/features/relay_only.py
```

### Expose Your Server

In another terminal, start ngrok:

```bash
ngrok http 8000
# Copy the ngrok URL (e.g., abc123.ngrok.io)
```

Update `TWILIO_VOICE_PUBLIC_DOMAIN` in your `.env` file with the ngrok URL (without `https://`).

Restart the server to pick up the new configuration.

## Environment Variables

See `examples/.env.example` for all available configuration options. Key variables:

### Required
- `TWILIO_ACCOUNT_SID`: Twilio Account SID
- `TWILIO_AUTH_TOKEN`: Twilio auth token
- `TWILIO_API_KEY`: Twilio API key SID (starts with SK)
- `TWILIO_API_SECRET`: Twilio API key secret
- `TWILIO_PHONE_NUMBER`: Your Twilio phone number

### Required for Orchestrator Mode (omit for ConversationRelay-only)
- `TWILIO_CONVERSATION_CONFIGURATION_ID`: Conversation Configuration ID

### Optional (Voice Channel)
- `TWILIO_VOICE_PUBLIC_DOMAIN`: Your ngrok domain (required for voice)

### Optional (OpenAI Example)
- `OPENAI_API_KEY`: Your OpenAI API key (only needed to run OpenAI examples)

### Optional (Conversation Intelligence)
- `CONVERSATION_INTELLIGENCE_CONFIGURATION_ID`: CI Configuration ID — enables the `/ci-webhook` route on the TAC server to receive operator results
- `CONVERSATION_INTELLIGENCE_OBSERVATION_OPERATOR_SID`: Operator SID for a custom observation extraction operator
- `CONVERSATION_INTELLIGENCE_SUMMARY_OPERATOR_SID`: Operator SID for a custom summary operator

### Optional (Channel-Specific)
- `TWILIO_STUDIO_HANDOFF_FLOW_SID`: Studio Flow SID used by `create_studio_handoff_tool` (required for `features/handoff.py`)
- `TWILIO_RCS_SENDER_ID`: RCS Sender ID (required for `features/rcs.py`)
- `TWILIO_WHATSAPP_NUMBER`: WhatsApp-enabled phone number in format `whatsapp:+1234567890` (required for `features/whatsapp.py`)
- `TWILIO_CONVERSATIONS_SERVICE_SID`: Conversations Service SID (required for Chat channel examples)

## Conversation Intelligence and Memory

### How memory gets written (the standard path)

TAC does **not** write observations or summaries into Conversation Memory directly. Instead, Memora (Twilio's memory service) creates an internal Conversation Intelligence configuration automatically when you set up a Conversation Configuration. After a call ends and the session goes INACTIVE (typically ~5 minutes), Memora's ingest pipeline fires, processes the transcript, and writes observations and summaries into the Memory Store. These are then available via Recall on the next call.

This means there is typically a **5–10 minute delay** between a call ending and the memories being available.

### Custom Conversation Intelligence operators

You can create your own CI operators (e.g. "extract product preferences", "detect customer sentiment") with custom prompts via the Twilio Console. However, **results from custom operators do not automatically flow into Memora's ingest pipeline** — only Memora's own internal operators do.

TAC provides an alternate path for custom operators: if you set `CONVERSATION_INTELLIGENCE_CONFIGURATION_ID` in your `.env`, the TAC server exposes a `/ci-webhook` route. Twilio will POST custom operator results to this endpoint, and TAC will actively write them into Conversation Memory via `create_observation()`. This enables near real-time observation ingestion for custom operators, bypassing the standard delay.

To enable this in your voice example:

```python
if __name__ == "__main__":
    from tac.server.config import TACServerConfig

    server_config = TACServerConfig.from_env()
    if os.environ.get("CONVERSATION_INTELLIGENCE_CONFIGURATION_ID"):
        server_config.cintel_webhook_path = "/ci-webhook"

    server = TACFastAPIServer(tac=tac, voice_channel=voice_channel, config=server_config)
    server.start()
```

Then configure the webhook URL in the Twilio Console under your CI configuration to point to `https://<your-domain>/ci-webhook`.

> **Note:** This is an alternate path intended for custom operator use cases. For standard memory (observations and summaries from calls), Memora's ingest pipeline handles everything automatically — you do not need to configure CI webhooks.

### Reducing memory availability latency

By default, the Twilio Console sets the VOICE inactive timeout to a minimum of 5 minutes — but this is a UI constraint only. The underlying API allows values as low as 1 minute:

```bash
curl -X PUT "https://conversations.twilio.com/v2/ControlPlane/Configurations/<your-config-id>" \
  -u "<api-key>:<api-secret>" \
  -H "Content-Type: application/json" \
  -d '{"channelSettings": {"VOICE": {"statusTimeouts": {"inactive": 1, "closed": 5}}}}'
```

See the [Conversation Configuration API](http://twilio.com/docs/api/conversations/v2/configuration/update-configuration) for full details.

Even better: TAC automatically closes the conversation via the CO API as soon as the call ends (WebSocket disconnect), bypassing the inactive timer entirely and triggering Memora extraction immediately. This is the fastest path to getting memories available for the next call.

For background on how conversation state transitions (ACTIVE → INACTIVE → CLOSED) trigger memory extraction, see the [Conversation Lifecycle docs](https://www.twilio.com/docs/conversations/orchestrator/concepts/lifecycle). Key insight: observations are extracted on both INACTIVE and CLOSED, but summaries are only extracted on CLOSED — so closing immediately on call end is important for summaries.

## Next Steps

- Start with `examples/overview.py` to learn the core memory injection pattern
- Try the `examples/partners/` examples for production-ready partner SDK integration
- Customize the agent's behavior by modifying the message handler
- Add tool calling to enable agent actions beyond text responses
- Explore the main [README](../README.md) for advanced features

## AWS and Microsoft connectors

Building AI agents on AWS or Microsoft? Connect them to Twilio's voice, messaging, and conversation context with these dedicated packages:

- **[TAC for AWS](https://github.com/twilio/twilio-agent-connect-aws)** — `StrandsConnector`, `BedrockConnector`, `BedrockAgentCoreConnector` for AWS Strands, Bedrock Agents, and Bedrock AgentCore
- **[TAC for Microsoft](https://github.com/twilio/twilio-agent-connect-microsoft)** — `AgentFrameworkConnector` and `VoiceLiveConnector` for Microsoft Agent Framework, Azure AI Foundry (including Voice Live), and Azure OpenAI

# Nemo pyclaudir setup

This clone is configured as Nemo for Avazbek only.

## What is configured

- Telegram owner ID: `1965085976`
- Access policy: `owner_only`
- Codex MCP: enabled through `codex mcp-server`
- Prompt order: `prompts/nemo-system.md` -> `prompts/project.md`
- Nemo system prompt and private architecture: `prompts/nemo-system.md`
- Editable project overlay: `prompts/project.md`
- NemoVoice: copied into `nemo-voice/`

Unauthorized users are silently dropped in the Telegram dispatcher before
database persistence, engine submission, Claude, Codex, or MCP calls.

## Run

```bash
uv sync --extra dev
uv run python -m pyclaudir
```

Or with Docker:

```bash
docker compose up -d --build
docker compose logs -f
```

## Notes

The original Nemo `bot.json` referenced `data/prod/nemo/memories/SYSTEM.md`,
but that file was not present in the local `claudir-main` folder inspected here.
The available Nemo personality from NemoVoice and the old Nemo security
architecture were moved into `prompts/nemo-system.md`.

# claude-context

A context window monitor for [Claude Code](https://docs.anthropic.com/en/docs/claude-code). Replaces the default status line with a real-time breakdown of where your tokens are going.

```
Opus 4.6 (1M context) ▊░░░░ 3% 32.5k/1.0m · my-project (main*) · 12m  ░░░ 0% 5:23pm  ░░░ 0%w
  sys 5.6k · tools 19.3k · skills 5.0k · mem 1.2k · msg 5.0k · free 800.2k · buf 165.0k
```

## Install

```bash
# Prerequisites
brew install jq          # JSON parsing
pip3 install tiktoken    # BPE tokenizer

# Install
npx claude-context

# Uninstall
npx claude-context --uninstall
```

Restart Claude Code after install.

## What It Shows

**Line 1** — Model, context usage bar + percentage, project directory, git branch, session duration, effort level, rate limits (5h / 7d) with reset times

**Line 2** — Token breakdown: system prompt, tools, MCP servers, skills, CLAUDE.md memory, messages, free space, autocompact buffer

**Line 3** (conditional) — Warnings when context or rate limit utilization exceeds 80%

## Accuracy: Three-Tier Precision

Claude Code doesn't expose a per-category token breakdown, so we reconstruct it. The system uses three tiers of precision, automatically selecting the best available.

### Tier 1: First-Turn Anchoring + Differential Counting (best)

When both JSONL session data and the real API total are available:

1. **Overhead** (system prompt + tools + skills + CLAUDE.md) is measured from the first API turn's real `usage` data in the JSONL — no hardcoded constants needed
2. **Messages** = `actual_total - overhead` — exact, no tokenizer involved
3. **Total** = real API value — always exact

The overhead is computed once per session and cached. Within overhead, `sys` vs `tools` is split using a fixed ratio (~22.5% / 77.5%); `skills` and `CLAUDE.md` are measured directly from files.

### Tier 2: First-Turn Anchoring + tiktoken Messages

When JSONL is available but the real API total is not (e.g., session just started):

- Overhead: measured (same as Tier 1)
- Messages: tokenized from JSONL with `cl100k_base` (~5% error for English, larger for CJK)

### Tier 3: Hardcoded Constants + Proportional Calibration (fallback)

When JSONL is unavailable:

- System prompt: hardcoded ~5,600 tokens
- Built-in tools: hardcoded ~19,300 tokens
- If the real API total is available, all categories are scaled proportionally to match it

### Accuracy summary

| Source | Tier 1 | Tier 2 | Tier 3 |
|---|---|---|---|
| **Total** | Exact (API) | Estimated | Estimated or calibrated |
| **Overhead** (sys+tools) | <1% (measured) | <1% (measured) | ±10–20% (hardcoded) |
| **Messages** | Exact (differential) | ~5–20% (tiktoken) | ~5–20% (tiktoken) |
| **Skills / CLAUDE.md** | Low (reads files) | Low (reads files) | Low (reads files) |
| **MCP tools** | ~150 tok/server est. | ~150 tok/server est. | ~150 tok/server est. |

### Known limitations

- **Autocompact**: when Claude Code compresses context mid-session, the overhead can change (tools get deferred). The system detects this via a >30% drop in total and falls back to Tier 3
- **Tokenizer**: `cl100k_base` (GPT-4's BPE) is used as a proxy for Claude's tokenizer. In Tier 1, this only affects the user/assistant split within messages — the total is exact regardless
- **MCP tools**: still estimated at ~150 tokens per server. Actual schema sizes vary widely

### Why not use the Anthropic API for exact counts?

`anthropic.beta.messages.count_tokens()` would give exact per-category counts, but it requires a network round-trip on every status line refresh (~500ms+), which makes the status line unusable. The current approach runs in ~200ms locally and caches for 60 seconds.

## How Token Counting Works

### BPE Tokenization via tiktoken (`cl100k_base`)

```python
import tiktoken
enc = tiktoken.get_encoding("cl100k_base")
tokens = len(enc.encode(text))
```

Claude's actual tokenizer is not publicly available for offline use. `cl100k_base` (GPT-4's encoder) is used as a proxy — it shares the same BPE structure and gives reasonable approximations for most content. Falls back to `len(text) // 4` if tiktoken is not installed.

### Three-Channel Context Usage

```
total = input_tokens + cache_creation_input_tokens + cache_read_input_tokens
```

All three channels count toward context consumption. Ignoring cache tokens underestimates actual usage significantly.

### Session Message Parsing

We read `~/.claude/projects/<dir>/*.jsonl` and tokenize each turn:

- **User turns**: plain text + `tool_result` blocks (bash outputs, file reads, etc.)
- **Assistant turns**: text blocks + `tool_use` inputs (JSON-serialized)

### Skill Deduplication

Skills appearing in both `~/.claude/skills/` and `~/.agents/skills/` are deduplicated by name, keeping the larger token count.

### Caching

Results cached 60 seconds in `/tmp/claude/ctx-cache.json` to keep the status line fast.

## Visual Design

- **Nord Aurora** color palette
- **High-density progress bars** using Unicode block elements (`▏▎▍▌▋▊▉█`) — 8 sub-steps per character
- **Color-coded thresholds**: green (<50%) → orange (50–70%) → yellow (70–90%) → red (>90%)

## Dependencies

- `jq` — JSON parsing in shell
- `curl` — rate limit API calls
- `python3` + `tiktoken` — BPE token counting

## License

MIT

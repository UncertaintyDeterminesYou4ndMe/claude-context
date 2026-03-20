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

## Accuracy: What's Precise and What Isn't

Claude Code doesn't expose a per-category token breakdown, so we reconstruct it from external sources. Here's an honest account of where the numbers come from and where they can drift.

### What's accurate

**Total context usage (Line 1)** is read directly from Claude Code's own status line JSON (`input_tokens + cache_creation_input_tokens + cache_read_input_tokens`). This is the official API value and is always correct.

**Proportional calibration**: we pass the real total into the breakdown script as a scaling anchor. All per-category values are multiplied by `actual_total / estimated_total`, so the breakdown always sums to the true total. Per-category proportions are estimates; the sum is not.

### Known sources of error

| Source | Method | Typical error |
|---|---|---|
| **System prompt** | Hardcoded ~5,600 tokens | ±10–20% — changes with Claude Code version |
| **Built-in tools** | Hardcoded ~19,300 tokens | ±10–20% — changes with Claude Code version |
| **MCP tools** | ~150 tokens per server, flat estimate | High variance — actual schema size varies widely |
| **Skills** | Parsed from `SKILL.md`, tokenized | Low — reads actual files |
| **CLAUDE.md** | Full file tokenized | Low — reads actual files |
| **Messages** | JSONL session log parsed and tokenized | Low–medium — see below |
| **Tokenizer** | `cl100k_base` (GPT-4's BPE) not Claude's | ~5% for English/code, larger for CJK |
| **Autocompact buffer** | Fixed 16.5% of context window | Unknown — internal to Claude Code |

**On message counting**: we parse Claude Code's JSONL session log and tokenize user text, assistant text, `tool_use` inputs, and `tool_result` outputs. Large tool outputs (bash results, file reads) are the main source of drift — they're present in the JSONL but may be truncated or formatted differently than what Claude Code actually sends to the API.

**Practical accuracy**: in testing, the uncalibrated message estimate is typically within 20–30% of the true API value. After proportional calibration, the total is exact, and per-category numbers are reasonable for a visual breakdown.

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

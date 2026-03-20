# claude-context

A precise context window monitor for [Claude Code](https://docs.anthropic.com/en/docs/claude-code). Replaces the default status line with a detailed, real-time breakdown of exactly where your tokens are going.

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

## Token Counting: How We Get Precision

The core challenge is that Claude Code doesn't expose a per-category token breakdown. We reconstruct it by reading the same sources Claude Code reads, and counting tokens with the same algorithm the model uses.

### 1. BPE Tokenization via tiktoken (`cl100k_base`)

We use OpenAI's [tiktoken](https://github.com/openai/tiktoken) library with the `cl100k_base` encoding — the same byte-pair encoding (BPE) tokenizer that Claude-family models use. This gives us exact token counts rather than the naive `len(text) / 4` heuristic (which can be off by 2-3x for CJK text, code, or structured data).

```python
import tiktoken
enc = tiktoken.get_encoding("cl100k_base")
tokens = len(enc.encode(text))  # exact count
```

If tiktoken is not installed, we fall back to the `chars / 4` approximation — but the README and installer strongly recommend installing it.

### 2. Three-Channel Context Usage

Claude Code reports token usage across three channels. We aggregate all three for accurate total context consumption:

```
total = input_tokens + cache_creation_input_tokens + cache_read_input_tokens
```

- `input_tokens` — freshly processed tokens
- `cache_creation_input_tokens` — tokens written into the prompt cache this turn
- `cache_read_input_tokens` — tokens served from the prompt cache

Ignoring cache tokens (a common mistake) would dramatically undercount actual context usage.

### 3. Multi-Source Context Reconstruction

We enumerate every source that occupies space in the context window:

| Category | Method |
|---|---|
| **System prompt** | Fixed calibration (~5,600 tokens) — measured against Claude Code's `/context` output |
| **Built-in tools** | Fixed calibration (~19,300 tokens) — tool schemas are stable across versions |
| **MCP tools** | Estimated per registered server (~150 tokens each) from `~/.claude/settings.json` |
| **Skills** | Parsed from `SKILL.md` files — name + description + path, tokenized with tiktoken |
| **CLAUDE.md** | All `CLAUDE.md` / `CLAUDE.local.md` files (global + project + parent dir) tokenized |
| **Messages** | Session JSONL parsed: user text, assistant text, `tool_use` inputs, `tool_result` outputs |
| **Autocompact buffer** | 16.5% of context window reserved for Claude's autocompact mechanism |
| **Free** | `context_window - used - buffer` |

### 4. Session Message Parsing

We read Claude Code's JSONL session log (`~/.claude/projects/<dir>/*.jsonl`) and tokenize each message component:

- **User turns**: plain text and multi-block content arrays
- **Assistant turns**: text blocks, `tool_use` blocks (JSON-serialized input), and `tool_result` blocks (text or content arrays)

This captures the actual conversation footprint, not just a rough estimate.

### 5. Skill Deduplication

Skills may exist in both `~/.claude/skills/` and `~/.agents/skills/`. When the same skill name appears in both directories, we keep only the larger token count to avoid double-counting.

### 6. Caching

Token counting runs `python3` + tiktoken which takes ~200ms. To keep the status line responsive, results are cached for 60 seconds in `/tmp/claude/ctx-cache.json`. Rate limit API responses are similarly cached.

## Visual Design

- **Nord Aurora** color palette for consistent, readable theming
- **High-density progress bars** using Unicode block elements (`▏▎▍▌▋▊▉█`) — 8 sub-steps per character for smooth visual precision
- **Color-coded thresholds**: green (<50%) → orange (50-70%) → yellow (70-90%) → red (>90%)

## Dependencies

- `jq` — JSON parsing in shell
- `curl` — rate limit API calls
- `python3` + `tiktoken` — BPE token counting

## License

MIT

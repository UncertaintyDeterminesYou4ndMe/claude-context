#!/usr/bin/env python3
"""
Count tokens for Claude Code context breakdown categories.
Outputs JSON with estimated token counts per category.
Uses tiktoken cl100k_base encoding for precise counting.

Usage: python3 count_tokens.py <project_cwd>
"""

import json
import os
import re
import sys
import glob
from pathlib import Path

try:
    import tiktoken
    enc = tiktoken.get_encoding("cl100k_base")
    def count(text: str) -> int:
        return len(enc.encode(text))
except ImportError:
    # Fallback: ~4 chars per token
    def count(text: str) -> int:
        return max(1, len(text) // 4)


def read_file(path: str) -> str:
    try:
        with open(path, "r", errors="replace") as f:
            return f.read()
    except Exception:
        return ""


def count_skills() -> dict:
    """Count tokens for skill descriptions loaded into system prompt."""
    skills = {}
    total = 0
    xml_overhead_tokens = 20  # <skill><name>...</name><description>...</description><location>...</location></skill>

    for base in [
        os.path.expanduser("~/.claude/skills"),
        os.path.expanduser("~/.agents/skills"),
    ]:
        if not os.path.isdir(base):
            continue
        for skill_dir in sorted(os.listdir(base)):
            skill_md = os.path.join(base, skill_dir, "SKILL.md")
            if not os.path.isfile(skill_md):
                continue

            content = read_file(skill_md)
            name = skill_dir
            location = skill_md

            # Extract description from SKILL.md
            desc = ""
            m = re.search(r'description[:\s]*["\'](.+?)["\']', content, re.IGNORECASE)
            if m:
                desc = m.group(1)
            else:
                m = re.search(r'description[:\s]*(.+?)$', content, re.MULTILINE | re.IGNORECASE)
                if m:
                    desc = m.group(1).strip().strip("\"'")

            # Claude Code loads: name + description + location path
            entry_text = f"{name}\n{desc}\n{location}"
            entry_tokens = count(entry_text) + xml_overhead_tokens
            total += entry_tokens

            # Deduplicate by name (keep larger)
            if name in skills:
                skills[name] = max(skills[name], entry_tokens)
            else:
                skills[name] = entry_tokens

    # available_skills wrapper
    wrapper_tokens = count("<available_skills></available_skills>")
    total_deduped = sum(skills.values()) + wrapper_tokens

    return {"total": total_deduped, "count": len(skills), "details": skills}


def count_claude_md(project_cwd: str) -> dict:
    """Count tokens for CLAUDE.md files (global + project)."""
    files = {}
    total = 0

    candidates = [
        os.path.expanduser("~/.claude/CLAUDE.md"),
        os.path.expanduser("~/.claude/CLAUDE.local.md"),
    ]

    if project_cwd:
        candidates.append(os.path.join(project_cwd, "CLAUDE.md"))
        candidates.append(os.path.join(project_cwd, "CLAUDE.local.md"))
        # Check parent directories too
        parent = os.path.dirname(project_cwd)
        if parent and parent != project_cwd:
            candidates.append(os.path.join(parent, "CLAUDE.md"))

    for f in candidates:
        if os.path.isfile(f):
            content = read_file(f)
            tokens = count(content)
            files[f] = tokens
            total += tokens

    return {"total": total, "files": files}


def count_mcp_tools(project_cwd: str) -> dict:
    """Estimate MCP tool definition tokens."""
    total = 0
    servers = {}

    # Read MCP config from settings
    settings_path = os.path.expanduser("~/.claude/settings.json")
    if os.path.isfile(settings_path):
        try:
            with open(settings_path) as f:
                settings = json.load(f)
            mcp = settings.get("mcpServers", {})
            # Each MCP server typically registers tools with name + description
            # Estimate ~60 tokens per tool, ~2-3 tools per server
            for name, config in mcp.items():
                est = 150  # rough estimate per server
                servers[name] = est
                total += est
        except Exception:
            pass

    # Project-level MCP
    if project_cwd:
        proj_settings = os.path.join(project_cwd, ".claude", "settings.json")
        if os.path.isfile(proj_settings):
            try:
                with open(proj_settings) as f:
                    ps = json.load(f)
                for name, config in ps.get("mcpServers", {}).items():
                    if name not in servers:
                        est = 150
                        servers[name] = est
                        total += est
            except Exception:
                pass

    return {"total": total, "servers": servers}


def count_session_messages(project_cwd: str) -> dict:
    """Count tokens for conversation messages from JSONL session file."""
    if not project_cwd:
        return {"total": 0, "turns": 0, "user_tokens": 0, "assistant_tokens": 0}

    # Find the project JSONL directory
    dir_name = project_cwd.replace("/", "-")
    project_path = os.path.expanduser(f"~/.claude/projects/{dir_name}")

    if not os.path.isdir(project_path):
        return {"total": 0, "turns": 0, "user_tokens": 0, "assistant_tokens": 0}

    # Find latest JSONL
    jsonl_files = sorted(
        glob.glob(os.path.join(project_path, "*.jsonl")),
        key=os.path.getmtime,
        reverse=True,
    )

    if not jsonl_files:
        return {"total": 0, "turns": 0, "user_tokens": 0, "assistant_tokens": 0}

    latest = jsonl_files[0]
    user_tokens = 0
    assistant_tokens = 0
    user_turns = 0
    assistant_turns = 0

    try:
        with open(latest, "r") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue

                msg_type = entry.get("type", "")
                message = entry.get("message", {})
                content = message.get("content", "")

                if msg_type == "user":
                    user_turns += 1
                    if isinstance(content, str):
                        user_tokens += count(content)
                    elif isinstance(content, list):
                        for block in content:
                            if not isinstance(block, dict):
                                continue
                            btype = block.get("type", "")
                            if btype == "text":
                                user_tokens += count(block.get("text", ""))
                            elif btype == "tool_result":
                                # Tool outputs live here (bash results, file reads, etc.)
                                res = block.get("content", "")
                                if isinstance(res, str):
                                    user_tokens += count(res)
                                elif isinstance(res, list):
                                    for r in res:
                                        if isinstance(r, dict):
                                            user_tokens += count(r.get("text", ""))

                elif msg_type == "assistant":
                    assistant_turns += 1
                    if isinstance(content, str):
                        assistant_tokens += count(content)
                    elif isinstance(content, list):
                        for block in content:
                            if not isinstance(block, dict):
                                continue
                            btype = block.get("type", "")
                            if btype == "text":
                                assistant_tokens += count(block.get("text", ""))
                            elif btype == "tool_use":
                                assistant_tokens += count(json.dumps(block.get("input", {})))
    except Exception:
        pass

    return {
        "total": user_tokens + assistant_tokens,
        "turns": user_turns + assistant_turns,
        "user_tokens": user_tokens,
        "assistant_tokens": assistant_tokens,
    }


# ── System prompt & tools: fixed estimates ──────────────
# These are relatively stable across Claude Code versions.
# Calibrated against /context output:
#   System prompt: ~5.6k tokens
#   System tools: ~19.3k tokens
SYSTEM_PROMPT_TOKENS = 5600
SYSTEM_TOOLS_TOKENS = 19300
AUTOCOMPACT_RATIO = 0.165  # 16.5% of context window


def main():
    project_cwd = sys.argv[1] if len(sys.argv) > 1 else os.getcwd()
    context_size = int(sys.argv[2]) if len(sys.argv) > 2 else 200000
    # Optional: actual total from Claude Code's API usage (input+cache_creation+cache_read).
    # When provided, breakdown categories are scaled proportionally so they sum to this value,
    # eliminating tokenizer drift and hardcoded-constant errors in one step.
    actual_total = int(sys.argv[3]) if len(sys.argv) > 3 else 0

    skills = count_skills()
    claude_md = count_claude_md(project_cwd)
    mcp = count_mcp_tools(project_cwd)
    messages = count_session_messages(project_cwd)

    autocompact_buffer = int(context_size * AUTOCOMPACT_RATIO)

    estimated = (
        SYSTEM_PROMPT_TOKENS
        + SYSTEM_TOOLS_TOKENS
        + mcp["total"]
        + skills["total"]
        + claude_md["total"]
        + messages["total"]
    )

    # Proportional calibration: if the caller gives us the real API total, scale every
    # breakdown category so they sum to it.  This removes tokenizer drift and
    # hardcoded-constant errors at once, while preserving relative proportions.
    if actual_total > 0 and estimated > 0:
        ratio = actual_total / estimated
        sys_tok   = int(SYSTEM_PROMPT_TOKENS * ratio)
        tools_tok = int(SYSTEM_TOOLS_TOKENS  * ratio)
        mcp_tok   = int(mcp["total"]         * ratio)
        sk_tok    = int(skills["total"]      * ratio)
        md_tok    = int(claude_md["total"]   * ratio)
        msg_tok   = int(messages["total"]    * ratio)
        used      = actual_total
        calibrated = True
    else:
        sys_tok   = SYSTEM_PROMPT_TOKENS
        tools_tok = SYSTEM_TOOLS_TOKENS
        mcp_tok   = mcp["total"]
        sk_tok    = skills["total"]
        md_tok    = claude_md["total"]
        msg_tok   = messages["total"]
        used      = estimated
        calibrated = False

    free = max(0, context_size - used - autocompact_buffer)

    result = {
        "context_size": context_size,
        "system_prompt": sys_tok,
        "system_tools": tools_tok,
        "mcp_tools": mcp_tok,
        "mcp_servers": mcp["servers"],
        "skills": sk_tok,
        "skills_count": skills["count"],
        "claude_md": md_tok,
        "claude_md_files": claude_md["files"],
        "messages": msg_tok,
        "messages_turns": messages["turns"],
        "messages_user": messages["user_tokens"],
        "messages_assistant": messages["assistant_tokens"],
        "autocompact_buffer": autocompact_buffer,
        "used": used,
        "free": free,
        "calibrated": calibrated,
    }

    json.dump(result, sys.stdout, indent=2)
    print()


if __name__ == "__main__":
    main()

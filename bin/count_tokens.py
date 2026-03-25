#!/usr/bin/env python3
"""
Count tokens for Claude Code context breakdown categories.

Three-tier precision:
  Tier 1: First-turn overhead from JSONL + differential message counting (exact)
  Tier 2: First-turn overhead from JSONL + tiktoken message estimate
  Tier 3: Hardcoded constants + proportional calibration (fallback)

Usage: python3 count_tokens.py <project_cwd> [context_size] [actual_total] [transcript_hint]
"""

import json
import os
import re
import sys
import glob
import hashlib
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


# ── JSONL resolution ─────────────────────────────────────

def resolve_jsonl_path(project_cwd: str, transcript_hint: str = "") -> str | None:
    """Find the JSONL session file.

    Priority: transcript_hint (full path) > transcript_hint (session id) > latest by mtime.
    """
    # Case 1: full path
    if transcript_hint and transcript_hint.endswith(".jsonl") and os.path.isfile(transcript_hint):
        return transcript_hint

    dir_name = project_cwd.replace("/", "-")
    project_path = os.path.expanduser(f"~/.claude/projects/{dir_name}")

    # Case 2: session id → construct path
    if transcript_hint and not transcript_hint.endswith(".jsonl"):
        candidate = os.path.join(project_path, f"{transcript_hint}.jsonl")
        if os.path.isfile(candidate):
            return candidate

    # Case 3: latest by mtime
    if not os.path.isdir(project_path):
        return None
    jsonl_files = sorted(
        glob.glob(os.path.join(project_path, "*.jsonl")),
        key=os.path.getmtime,
        reverse=True,
    )
    return jsonl_files[0] if jsonl_files else None


def extract_session_id(jsonl_path: str | None, transcript_hint: str = "") -> str:
    """Derive a stable session id for caching."""
    if transcript_hint and not transcript_hint.endswith(".jsonl"):
        return transcript_hint
    if jsonl_path:
        return Path(jsonl_path).stem
    return ""


# ── First-turn overhead extraction ───────────────────────

OVERHEAD_CACHE_DIR = "/tmp/claude"

def get_cached_overhead(session_id: str) -> dict | None:
    cache_path = os.path.join(OVERHEAD_CACHE_DIR, f"overhead-{session_id}.json")
    if os.path.isfile(cache_path):
        try:
            with open(cache_path) as f:
                return json.load(f)
        except Exception:
            return None
    return None


def save_overhead_cache(session_id: str, data: dict):
    os.makedirs(OVERHEAD_CACHE_DIR, exist_ok=True)
    cache_path = os.path.join(OVERHEAD_CACHE_DIR, f"overhead-{session_id}.json")
    try:
        with open(cache_path, "w") as f:
            json.dump(data, f)
    except Exception:
        pass


def clear_overhead_cache(session_id: str):
    cache_path = os.path.join(OVERHEAD_CACHE_DIR, f"overhead-{session_id}.json")
    try:
        os.remove(cache_path)
    except Exception:
        pass


def _count_user_content(content) -> int:
    """Tokenize user message content (text + tool_result blocks)."""
    if isinstance(content, str):
        return count(content)
    if isinstance(content, list):
        total = 0
        for block in content:
            if not isinstance(block, dict):
                continue
            btype = block.get("type", "")
            if btype == "text":
                total += count(block.get("text", ""))
            elif btype == "tool_result":
                res = block.get("content", "")
                if isinstance(res, str):
                    total += count(res)
                elif isinstance(res, list):
                    for r in res:
                        if isinstance(r, dict):
                            total += count(r.get("text", ""))
        return total
    return 0


def extract_first_turn_overhead(jsonl_path: str) -> dict:
    """Measure the 'overhead' (system prompt + tools + skills + CLAUDE.md) from the first API turn.

    Turn 1's total input = overhead + first_user_message.
    We subtract the first user message (tiktoken estimate) to isolate overhead.
    """
    result = {"overhead": 0, "first_user_tokens": 0, "valid": False}

    try:
        first_user_content = None
        first_assistant_usage = None

        with open(jsonl_path, "r") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue

                etype = entry.get("type", "")

                if etype == "user" and first_user_content is None:
                    first_user_content = entry.get("message", {}).get("content", "")

                elif etype == "assistant" and first_assistant_usage is None:
                    usage = entry.get("message", {}).get("usage", {})
                    if "input_tokens" in usage:
                        total_in = (
                            usage.get("input_tokens", 0)
                            + usage.get("cache_creation_input_tokens", 0)
                            + usage.get("cache_read_input_tokens", 0)
                        )
                        if total_in > 0:
                            first_assistant_usage = total_in

                if first_user_content is not None and first_assistant_usage is not None:
                    break

        if first_assistant_usage and first_assistant_usage > 0:
            first_user_tokens = _count_user_content(first_user_content) if first_user_content else 0
            overhead = max(0, first_assistant_usage - first_user_tokens)
            result = {
                "overhead": overhead,
                "first_user_tokens": first_user_tokens,
                "valid": True,
            }
    except Exception:
        pass

    return result


# ── Message split ratio ──────────────────────────────────

def compute_message_split_ratio(jsonl_path: str) -> tuple:
    """Return (user_fraction, assistant_fraction) of message tokens.

    Used for splitting the differential message total into user vs assistant.
    """
    user_tokens = 0
    assistant_tokens = 0

    try:
        with open(jsonl_path, "r") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue

                etype = entry.get("type", "")
                content = entry.get("message", {}).get("content", "")

                if etype == "user":
                    user_tokens += _count_user_content(content)
                elif etype == "assistant":
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

    total = user_tokens + assistant_tokens
    if total == 0:
        return (0.5, 0.5)
    return (user_tokens / total, assistant_tokens / total)


# ── Static category counting (unchanged) ─────────────────

def count_skills() -> dict:
    """Count tokens for skill descriptions loaded into system prompt."""
    skills = {}
    total = 0
    xml_overhead_tokens = 20

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

            desc = ""
            m = re.search(r'description[:\s]*["\'](.+?)["\']', content, re.IGNORECASE)
            if m:
                desc = m.group(1)
            else:
                m = re.search(r'description[:\s]*(.+?)$', content, re.MULTILINE | re.IGNORECASE)
                if m:
                    desc = m.group(1).strip().strip("\"'")

            entry_text = f"{name}\n{desc}\n{location}"
            entry_tokens = count(entry_text) + xml_overhead_tokens
            total += entry_tokens

            if name in skills:
                skills[name] = max(skills[name], entry_tokens)
            else:
                skills[name] = entry_tokens

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

    settings_path = os.path.expanduser("~/.claude/settings.json")
    if os.path.isfile(settings_path):
        try:
            with open(settings_path) as f:
                settings = json.load(f)
            mcp = settings.get("mcpServers", {})
            for name, config in mcp.items():
                est = 150
                servers[name] = est
                total += est
        except Exception:
            pass

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
    """Fallback: count message tokens from JSONL via tiktoken (Tier 3)."""
    jsonl_path = resolve_jsonl_path(project_cwd)
    if not jsonl_path:
        return {"total": 0, "turns": 0, "user_tokens": 0, "assistant_tokens": 0}
    return _count_messages_from_path(jsonl_path)


def _count_messages_from_path(jsonl_path: str) -> dict:
    """Count tokens for all messages in a JSONL file."""
    user_tokens = 0
    assistant_tokens = 0
    user_turns = 0
    assistant_turns = 0

    try:
        with open(jsonl_path, "r") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue

                etype = entry.get("type", "")
                content = entry.get("message", {}).get("content", "")

                if etype == "user":
                    user_turns += 1
                    user_tokens += _count_user_content(content)

                elif etype == "assistant":
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


# ── Constants (Tier 3 fallback only) ─────────────────────
SYSTEM_PROMPT_TOKENS = 5600
SYSTEM_TOOLS_TOKENS = 19300
SYS_RATIO = SYSTEM_PROMPT_TOKENS / (SYSTEM_PROMPT_TOKENS + SYSTEM_TOOLS_TOKENS)  # ~0.225
AUTOCOMPACT_RATIO = 0.165  # 16.5% of context window


# ── Main ─────────────────────────────────────────────────

def main():
    project_cwd = sys.argv[1] if len(sys.argv) > 1 else os.getcwd()
    context_size = int(sys.argv[2]) if len(sys.argv) > 2 else 200000
    actual_total = int(sys.argv[3]) if len(sys.argv) > 3 else 0
    transcript_hint = sys.argv[4] if len(sys.argv) > 4 else ""

    # Resolve JSONL
    jsonl_path = resolve_jsonl_path(project_cwd, transcript_hint)
    session_id = extract_session_id(jsonl_path, transcript_hint)

    # Try to load first-turn overhead (cached per session)
    overhead_data = None
    if session_id:
        overhead_data = get_cached_overhead(session_id)
    if not overhead_data and jsonl_path:
        overhead_data = extract_first_turn_overhead(jsonl_path)
        if overhead_data.get("valid") and session_id:
            save_overhead_cache(session_id, overhead_data)

    # Autocompact detection: if actual_total drops >30%, invalidate overhead cache
    if overhead_data and overhead_data.get("valid") and actual_total > 0:
        last = overhead_data.get("last_actual_total", 0)
        if last > 0 and actual_total < last * 0.7:
            overhead_data = None
            if session_id:
                clear_overhead_cache(session_id)

    # Static categories (always computed)
    skills = count_skills()
    claude_md = count_claude_md(project_cwd)
    mcp = count_mcp_tools(project_cwd)
    autocompact_buffer = int(context_size * AUTOCOMPACT_RATIO)

    # ── Tier selection ────────────────────────────────────
    if overhead_data and overhead_data.get("valid") and actual_total > 0:
        # TIER 1: Differential counting (best precision)
        measured_overhead = overhead_data["overhead"]
        msg_tok = max(0, actual_total - measured_overhead)

        # Split overhead: skills + claude_md + mcp are estimated; sys+tools is the remainder
        known_overhead = skills["total"] + claude_md["total"] + mcp["total"]
        sys_tools = max(0, measured_overhead - known_overhead)
        sys_tok = int(sys_tools * SYS_RATIO)
        tools_tok = sys_tools - sys_tok
        sk_tok = skills["total"]
        md_tok = claude_md["total"]
        mcp_tok = mcp["total"]
        used = actual_total
        overhead_source = "anchored"

        # User/assistant split from JSONL ratio
        if jsonl_path:
            u_frac, _ = compute_message_split_ratio(jsonl_path)
            user_tokens = int(msg_tok * u_frac)
            assistant_tokens = msg_tok - user_tokens
        else:
            user_tokens = msg_tok // 2
            assistant_tokens = msg_tok - user_tokens

        turns = 0
        if jsonl_path:
            try:
                with open(jsonl_path) as f:
                    for line in f:
                        line = line.strip()
                        if line:
                            try:
                                e = json.loads(line)
                                if e.get("type") in ("user", "assistant"):
                                    turns += 1
                            except:
                                pass
            except:
                pass

        # Update cache with last_actual_total for autocompact detection
        if session_id:
            overhead_data["last_actual_total"] = actual_total
            save_overhead_cache(session_id, overhead_data)

    elif overhead_data and overhead_data.get("valid"):
        # TIER 2: Have overhead but no actual_total
        measured_overhead = overhead_data["overhead"]
        messages = _count_messages_from_path(jsonl_path) if jsonl_path else count_session_messages(project_cwd)

        known_overhead = skills["total"] + claude_md["total"] + mcp["total"]
        sys_tools = max(0, measured_overhead - known_overhead)
        sys_tok = int(sys_tools * SYS_RATIO)
        tools_tok = sys_tools - sys_tok
        sk_tok = skills["total"]
        md_tok = claude_md["total"]
        mcp_tok = mcp["total"]
        msg_tok = messages["total"]
        user_tokens = messages["user_tokens"]
        assistant_tokens = messages["assistant_tokens"]
        turns = messages["turns"]
        used = measured_overhead + msg_tok
        overhead_source = "anchored"

    else:
        # TIER 3: Fallback — hardcoded constants + proportional calibration
        messages = count_session_messages(project_cwd)

        estimated = (
            SYSTEM_PROMPT_TOKENS
            + SYSTEM_TOOLS_TOKENS
            + mcp["total"]
            + skills["total"]
            + claude_md["total"]
            + messages["total"]
        )

        if actual_total > 0 and estimated > 0:
            ratio = actual_total / estimated
            sys_tok   = int(SYSTEM_PROMPT_TOKENS * ratio)
            tools_tok = int(SYSTEM_TOOLS_TOKENS  * ratio)
            mcp_tok   = int(mcp["total"]         * ratio)
            sk_tok    = int(skills["total"]      * ratio)
            md_tok    = int(claude_md["total"]   * ratio)
            msg_tok   = int(messages["total"]    * ratio)
            used      = actual_total
            overhead_source = "calibrated"
        else:
            sys_tok   = SYSTEM_PROMPT_TOKENS
            tools_tok = SYSTEM_TOOLS_TOKENS
            mcp_tok   = mcp["total"]
            sk_tok    = skills["total"]
            md_tok    = claude_md["total"]
            msg_tok   = messages["total"]
            used      = estimated
            overhead_source = "estimated"

        user_tokens = messages["user_tokens"]
        assistant_tokens = messages["assistant_tokens"]
        turns = messages["turns"]

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
        "messages_turns": turns,
        "messages_user": user_tokens,
        "messages_assistant": assistant_tokens,
        "autocompact_buffer": autocompact_buffer,
        "used": used,
        "free": free,
        "overhead_source": overhead_source,
    }

    json.dump(result, sys.stdout, indent=2)
    print()


if __name__ == "__main__":
    main()

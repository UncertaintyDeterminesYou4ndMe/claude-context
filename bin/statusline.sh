#!/bin/bash
set -f

input=$(cat)
[ -z "$input" ] && { printf "Claude"; exit 0; }

# ── Colors (Nord Aurora) ────────────────────────────────
blue='\033[38;2;136;192;208m'
sky='\033[38;2;129;161;193m'
green='\033[38;2;163;190;140m'
cyan='\033[38;2;143;188;187m'
red='\033[38;2;191;97;106m'
yellow='\033[38;2;235;203;139m'
white='\033[38;2;216;222;233m'
magenta='\033[38;2;180;142;173m'
orange='\033[38;2;208;135;112m'
dim='\033[2m'
reset='\033[0m'
sep=" ${dim}·${reset} "

# ── Helpers ─────────────────────────────────────────────
fmt() {
    local n=$1; [ -z "$n" ] && n=0
    if [ "$n" -ge 1000000 ]; then awk "BEGIN{printf \"%.1fm\",$n/1000000}"
    elif [ "$n" -ge 1000 ]; then  awk "BEGIN{printf \"%.1fk\",$n/1000}"
    else printf "%d" "$n"; fi
}

color_for_pct() {
    local p=$1
    if   [ "$p" -ge 90 ]; then printf "$red"
    elif [ "$p" -ge 70 ]; then printf "$yellow"
    elif [ "$p" -ge 50 ]; then printf "$orange"
    else printf "$green"; fi
}

# High-density bar: uses ▏▎▍▌▋▊▉█ for 8 sub-steps per char
# width=5 → 40 precision levels
build_bar() {
    local pct=$1 width=$2
    [ "$pct" -lt 0 ] 2>/dev/null && pct=0
    [ "$pct" -gt 100 ] 2>/dev/null && pct=100

    local bar_color; bar_color=$(color_for_pct "$pct")
    local subs=("" "▏" "▎" "▍" "▌" "▋" "▊" "▉")
    local total_units=$(( width * 8 ))
    local filled_units=$(( pct * total_units / 100 ))
    local full_chars=$(( filled_units / 8 ))
    local partial=$(( filled_units % 8 ))
    local empty_chars=$(( width - full_chars - (partial > 0 ? 1 : 0) ))

    local bar=""
    for ((i=0; i<full_chars; i++)); do bar+="█"; done
    [ "$partial" -gt 0 ] && bar+="${subs[$partial]}"
    local pad=""
    for ((i=0; i<empty_chars; i++)); do pad+="░"; done

    printf "${bar_color}${bar}${dim}${pad}${reset}"
}

iso_to_epoch() {
    local s="$1"; local e
    e=$(date -d "$s" +%s 2>/dev/null) && { echo "$e"; return; }
    local st="${s%%.*}"; st="${st%%Z}"; st="${st%%+*}"; st="${st%%-[0-9][0-9]:[0-9][0-9]}"
    if [[ "$s" == *"Z"* ]] || [[ "$s" == *"+00:00"* ]]; then
        e=$(env TZ=UTC date -j -f "%Y-%m-%dT%H:%M:%S" "$st" +%s 2>/dev/null)
    else
        e=$(date -j -f "%Y-%m-%dT%H:%M:%S" "$st" +%s 2>/dev/null)
    fi
    [ -n "$e" ] && echo "$e"
}

fmt_reset() {
    local s="$1" style="$2"
    [ -z "$s" ] || [ "$s" = "null" ] && return
    local e; e=$(iso_to_epoch "$s"); [ -z "$e" ] && return
    case "$style" in
        time) date -j -r "$e" +"%l:%M%p" 2>/dev/null | sed 's/^ //;s/\.//g' | tr '[:upper:]' '[:lower:]' ;;
        *) date -j -r "$e" +"%b %-d" 2>/dev/null | tr '[:upper:]' '[:lower:]' ;;
    esac
}

# ── Parse input ─────────────────────────────────────────
model=$(echo "$input" | jq -r '.model.display_name // "Claude"')
size=$(echo "$input" | jq -r '.context_window.context_window_size // 200000')
[ "$size" -eq 0 ] 2>/dev/null && size=200000
itok=$(echo "$input" | jq -r '.context_window.current_usage.input_tokens // 0')
cctok=$(echo "$input" | jq -r '.context_window.current_usage.cache_creation_input_tokens // 0')
crtok=$(echo "$input" | jq -r '.context_window.current_usage.cache_read_input_tokens // 0')
cur=$(( itok + cctok + crtok ))
pct=$(( size > 0 ? cur * 100 / size : 0 ))

cwd=$(echo "$input" | jq -r '.cwd // ""')
[ -z "$cwd" ] || [ "$cwd" = "null" ] && cwd=$(pwd)
dir=$(basename "$cwd")

gb=""; gd=""
if git -C "$cwd" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    gb=$(git -C "$cwd" symbolic-ref --short HEAD 2>/dev/null)
    [ -n "$(git -C "$cwd" status --porcelain 2>/dev/null)" ] && gd="*"
fi

dur=""
ss=$(echo "$input" | jq -r '.session.start_time // empty')
if [ -n "$ss" ] && [ "$ss" != "null" ]; then
    se=$(iso_to_epoch "$ss")
    if [ -n "$se" ]; then
        el=$(( $(date +%s) - se ))
        if   [ "$el" -ge 3600 ]; then dur="$(( el/3600 ))h$(( (el%3600)/60 ))m"
        elif [ "$el" -ge 60 ];   then dur="$(( el/60 ))m"
        else dur="${el}s"; fi
    fi
fi

eff="default"
[ -f "$HOME/.claude/settings.json" ] && eff=$(jq -r '.effortLevel // "default"' "$HOME/.claude/settings.json" 2>/dev/null)

# ── Context breakdown (cached 60s) ──────────────────────
SD="$(cd "$(dirname "$0")" && pwd)"
mkdir -p /tmp/claude
cc="/tmp/claude/ctx-cache.json"; cd=""
[ -f "$cc" ] && { ca=$(( $(date +%s) - $(stat -c %Y "$cc" 2>/dev/null || stat -f %m "$cc" 2>/dev/null) )); [ "$ca" -lt 60 ] && cd=$(cat "$cc"); }
[ -z "$cd" ] && { cd=$(python3 "$SD/count_tokens.py" "$cwd" "$size" 2>/dev/null); [ -n "$cd" ] && echo "$cd" | jq -e '.used' >/dev/null 2>&1 && echo "$cd" > "$cc"; }

# ── Rate limit (cached 60s) ────────────────────────────
uc="/tmp/claude/usage-cache.json"; ud=""
[ -f "$uc" ] && { ca=$(( $(date +%s) - $(stat -c %Y "$uc" 2>/dev/null || stat -f %m "$uc" 2>/dev/null) )); [ "$ca" -lt 60 ] && ud=$(cat "$uc"); }
if [ -z "$ud" ]; then
    tk=""
    if command -v security >/dev/null 2>&1; then
        bl=$(security find-generic-password -s "Claude Code-credentials" -w 2>/dev/null)
        [ -n "$bl" ] && tk=$(echo "$bl" | jq -r '.claudeAiOauth.accessToken // empty' 2>/dev/null)
    fi
    [ -z "$tk" ] && [ -f "$HOME/.claude/.credentials.json" ] && tk=$(jq -r '.claudeAiOauth.accessToken // empty' "$HOME/.claude/.credentials.json" 2>/dev/null)
    if [ -n "$tk" ] && [ "$tk" != "null" ]; then
        rsp=$(curl -s --max-time 5 -H "Authorization: Bearer $tk" -H "anthropic-beta: oauth-2025-04-20" -H "User-Agent: claude-code/2.1.34" "https://api.anthropic.com/api/oauth/usage" 2>/dev/null)
        echo "$rsp" | jq -e '.five_hour' >/dev/null 2>&1 && { ud="$rsp"; echo "$rsp" > "$uc"; }
    fi
    [ -z "$ud" ] && [ -f "$uc" ] && ud=$(cat "$uc")
fi

# ── LINE 1 ──────────────────────────────────────────────
pc=$(color_for_pct "$pct")
bar=$(build_bar "$pct" 5)

L1="${blue}${model}${reset} ${bar} ${pc}${pct}%${reset} ${dim}$(fmt $cur)/$(fmt $size)${reset}"
L1+="${sep}${cyan}${dir}${reset}"
[ -n "$gb" ] && L1+=" ${green}(${gb}${red}${gd}${green})${reset}"
[ -n "$dur" ] && L1+="${sep}${dim}${dur}${reset}"
[ "$eff" != "default" ] && L1+="${sep}${magenta}${eff}${reset}"

# Inline rate limits
if [ -n "$ud" ] && echo "$ud" | jq -e '.five_hour' >/dev/null 2>&1; then
    rp=$(echo "$ud" | jq -r '.five_hour.utilization // 0' | awk '{printf "%.0f", $1}')
    rc=$(color_for_pct "$rp")
    rb=$(build_bar "$rp" 3)
    rt=$(fmt_reset "$(echo "$ud" | jq -r '.five_hour.resets_at // empty')" "time")
    L1+="  ${rb} ${rc}${rp}%${reset}"
    [ -n "$rt" ] && L1+=" ${dim}${rt}${reset}"

    wp=$(echo "$ud" | jq -r '.seven_day.utilization // 0' | awk '{printf "%.0f", $1}')
    wc=$(color_for_pct "$wp")
    wb=$(build_bar "$wp" 3)
    L1+="  ${wb} ${wc}${wp}%${reset}${dim}w${reset}"
fi

# ── LINE 2: compact breakdown ───────────────────────────
L2=""
if [ -n "$cd" ] && echo "$cd" | jq -e '.used' >/dev/null 2>&1; then
    sp=$(echo "$cd" | jq '.system_prompt // 0')
    st=$(echo "$cd" | jq '.system_tools // 0')
    mc=$(echo "$cd" | jq '.mcp_tools // 0')
    sk=$(echo "$cd" | jq '.skills // 0')
    cm=$(echo "$cd" | jq '.claude_md // 0')
    ms=$(echo "$cd" | jq '.messages // 0')
    fr=$(echo "$cd" | jq '.free // 0')
    ab=$(echo "$cd" | jq '.autocompact_buffer // 0')

    s2="${dim} · ${reset}"
    L2="  ${sky}sys $(fmt $sp)${reset}"
    L2+="${s2}${blue}tools $(fmt $st)${reset}"
    [ "$mc" -gt 0 ] && L2+="${s2}${magenta}mcp $(fmt $mc)${reset}"
    L2+="${s2}${cyan}skills $(fmt $sk)${reset}"
    [ "$cm" -gt 0 ] && L2+="${s2}${green}mem $(fmt $cm)${reset}"
    L2+="${s2}${yellow}msg $(fmt $ms)${reset}"
    L2+="${s2}${dim}free $(fmt $fr)${reset}"
    L2+="${s2}${dim}buf $(fmt $ab)${reset}"
fi

# ── WARN (only >=80%) ──────────────────────────────────
W=""
[ "$pct" -ge 80 ] && W+="${red}! ctx ${pct}% -- /compact${reset}"
if [ -n "$ud" ] && echo "$ud" | jq -e '.five_hour' >/dev/null 2>&1; then
    rw=$(echo "$ud" | jq -r '.five_hour.utilization // 0' | awk '{printf "%.0f", $1}')
    if [ "$rw" -ge 80 ]; then
        [ -n "$W" ] && W+="  "
        W+="${red}! rate ${rw}% -- $(fmt_reset "$(echo "$ud" | jq -r '.five_hour.resets_at // empty')" "time")${reset}"
    fi
fi

# ── Output ──────────────────────────────────────────────
printf "%b" "$L1"
[ -n "$L2" ] && printf "\n%b" "$L2"
[ -n "$W" ] && printf "\n%b" "$W"
exit 0

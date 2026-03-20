#!/usr/bin/env node

const fs = require("fs");
const path = require("path");
const os = require("os");

const CLAUDE_DIR = path.join(os.homedir(), ".claude");
const SETTINGS_FILE = path.join(CLAUDE_DIR, "settings.json");
const STATUSLINE_SRC = path.resolve(__dirname, "statusline.sh");
const STATUSLINE_DEST = path.join(CLAUDE_DIR, "statusline.sh");
const TOKENCOUNT_SRC = path.resolve(__dirname, "count_tokens.py");
const TOKENCOUNT_DEST = path.join(CLAUDE_DIR, "count_tokens.py");

const blue = "\x1b[38;2;136;192;208m";
const green = "\x1b[38;2;163;190;140m";
const red = "\x1b[38;2;191;97;106m";
const yellow = "\x1b[38;2;235;203;139m";
const dim = "\x1b[2m";
const reset = "\x1b[0m";

function log(msg) { console.log(`  ${msg}`); }
function success(msg) { console.log(`  ${green}+${reset} ${msg}`); }
function warn(msg) { console.log(`  ${yellow}!${reset} ${msg}`); }
function fail(msg) { console.error(`  ${red}x${reset} ${msg}`); }

function checkDeps() {
  const { execSync } = require("child_process");
  const missing = [];
  try { execSync("which jq", { stdio: "ignore" }); } catch { missing.push("jq"); }
  try { execSync("which curl", { stdio: "ignore" }); } catch { missing.push("curl"); }
  try { execSync("which python3", { stdio: "ignore" }); } catch { missing.push("python3"); }
  try { execSync("python3 -c 'import tiktoken'", { stdio: "ignore" }); } catch { missing.push("tiktoken (pip3 install tiktoken)"); }
  return missing;
}

function uninstall() {
  console.log();
  console.log(`  ${blue}Claude Context Uninstaller${reset}`);
  console.log(`  ${dim}──────────────────────────${reset}`);
  console.log();

  const backup = STATUSLINE_DEST + ".bak";
  if (fs.existsSync(backup)) {
    fs.copyFileSync(backup, STATUSLINE_DEST);
    fs.unlinkSync(backup);
    success(`Restored previous statusline from ${dim}statusline.sh.bak${reset}`);
  } else if (fs.existsSync(STATUSLINE_DEST)) {
    fs.unlinkSync(STATUSLINE_DEST);
    success(`Removed ${dim}statusline.sh${reset}`);
  }

  if (fs.existsSync(TOKENCOUNT_DEST)) {
    fs.unlinkSync(TOKENCOUNT_DEST);
    success(`Removed ${dim}count_tokens.py${reset}`);
  }

  if (fs.existsSync(SETTINGS_FILE)) {
    try {
      const settings = JSON.parse(fs.readFileSync(SETTINGS_FILE, "utf-8"));
      if (settings.statusLine) {
        delete settings.statusLine;
        fs.writeFileSync(SETTINGS_FILE, JSON.stringify(settings, null, 2) + "\n");
        success(`Removed statusLine from ${dim}settings.json${reset}`);
      }
    } catch {
      fail(`Could not parse ${SETTINGS_FILE}`);
      process.exit(1);
    }
  }

  console.log();
  log(`${green}Done!${reset} Restart Claude Code to apply.`);
  console.log();
}

function run() {
  if (process.argv.slice(2).some(a => a === "--uninstall" || a === "-u")) {
    return uninstall();
  }

  console.log();
  console.log(`  ${blue}Claude Context Installer${reset}`);
  console.log(`  ${dim}────────────────────────${reset}`);
  console.log();

  const missing = checkDeps();
  if (missing.length > 0) {
    fail(`Missing: ${missing.join(", ")}`);
    process.exit(1);
  }
  success("All dependencies found");

  if (!fs.existsSync(CLAUDE_DIR)) fs.mkdirSync(CLAUDE_DIR, { recursive: true });

  const backup = STATUSLINE_DEST + ".bak";
  if (fs.existsSync(STATUSLINE_DEST)) {
    fs.copyFileSync(STATUSLINE_DEST, backup);
    warn(`Backed up existing statusline to ${dim}statusline.sh.bak${reset}`);
  }

  fs.copyFileSync(STATUSLINE_SRC, STATUSLINE_DEST);
  fs.chmodSync(STATUSLINE_DEST, 0o755);
  success(`Installed ${dim}statusline.sh${reset}`);

  fs.copyFileSync(TOKENCOUNT_SRC, TOKENCOUNT_DEST);
  fs.chmodSync(TOKENCOUNT_DEST, 0o755);
  success(`Installed ${dim}count_tokens.py${reset}`);

  let settings = {};
  if (fs.existsSync(SETTINGS_FILE)) {
    try { settings = JSON.parse(fs.readFileSync(SETTINGS_FILE, "utf-8")); } catch {
      fail(`Could not parse ${SETTINGS_FILE}`);
      process.exit(1);
    }
  }

  const cfg = { type: "command", command: 'bash "$HOME/.claude/statusline.sh"' };
  if (settings.statusLine?.command === cfg.command) {
    success("Settings already configured");
  } else {
    settings.statusLine = cfg;
    fs.writeFileSync(SETTINGS_FILE, JSON.stringify(settings, null, 2) + "\n");
    success(`Updated ${dim}settings.json${reset}`);
  }

  console.log();
  log(`${green}Done!${reset} Restart Claude Code to see your status line.`);
  console.log();
  log(`Features:`);
  log(`  ${dim}-${reset} Context breakdown (system prompt / tools / skills / messages)`);
  log(`  ${dim}-${reset} Precise token counting via tiktoken cl100k_base`);
  log(`  ${dim}-${reset} Rate limit bars (5h / 7d / extra) with reset times`);
  log(`  ${dim}-${reset} Warnings when context or rate limit >= 80%`);
  console.log();
}

run();

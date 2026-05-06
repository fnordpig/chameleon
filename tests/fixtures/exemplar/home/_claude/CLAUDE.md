# Global Claude Instructions

Be concise. Prefer editing existing files over creating new ones.

Do not take a shortcut, do not gravitate towards the easiest path towards done.  Be principled and do the Right thing in the Right way.

## Shell Aliases

Interactive shell aliases — use the right-hand tool directly in scripts and commands:

| Alias | Actual Tool | Notes |
|-------|------------|-------|
| `grep` | `rg` (ripgrep) | No `-E` flag (extended regex is default). `grep -oE` → `rg -o`. `grep -q` → `rg -q`. |
| `find` | `fd` | Different syntax: `fd pattern` not `find . -name pattern`. |
| `cat` | `bat --plain` | Use `bat` or `cat` — `--plain` disables decoration. |
| `ls` | `eza` | Rust replacement for ls. |
| `ll` | `eza -la --git --icons` | Long list with git status. |
| `lt` | `eza -la --git --icons --sort=modified` | Sort by modification time. |
| `la` | `eza -a` | Show hidden files. |
| `tree` | `eza --tree` | Tree view. |
| `screen` | `zellij` | Terminal multiplexer. |
| `rsync` | `rsync -avz --progress` | Adds archive, verbose, compress, progress. |

Other shell tools (not aliases, but active via `eval`):
- `starship` — prompt
- `zoxide` — `cd` replacement (`z` command)
- `direnv` — auto-loads `.envrc`
- `fnm` — fast Node manager
- `uv` — Python package manager (shell completion)

## Interactive UI Tools

`gum` and `fzf` are installed on all hosts. Use them when asking the user to make choices or provide input:

| Tool | Best For | Example |
|------|----------|---------|
| `gum choose` | Single selection from a list | `gum choose "option1" "option2" "option3"` |
| `gum filter` | Fuzzy-searchable selection | `echo "$items" \| gum filter` |
| `gum confirm` | Yes/no confirmation | `gum confirm "Deploy to production?"` |
| `gum input` | Single-line text input | `gum input --placeholder "Enter name"` |
| `gum write` | Multi-line text input | `gum write --placeholder "Describe the issue"` |
| `gum spin` | Show spinner during long ops | `gum spin --title "Building..." -- make build` |
| `fzf` | Fuzzy file/history selection | `fd -t f \| fzf --preview 'bat --color=always {}'` |
| `fzf --multi` | Multi-select from list | `git branch \| fzf --multi` |

**When to use which**: `gum` for structured prompts (menus, confirmations, styled input). `fzf` for fuzzy-searching large lists (files, git branches, history). Prefer these over bare `read` prompts or numbered menus.

### Claude Code Bash tool constraint

The Bash tool has **no TTY/stdin** — interactive tools (`gum choose`, `gum confirm`, `gum input`, `fzf` interactive, `dialog`) will hang or fail. Use this split:

**Works in Bash tool (non-interactive):**
| Tool | Use For | Example |
|------|---------|---------|
| `gum style` | Bordered/colored banners | `gum style --border double --padding 1 "✅ Done"` |
| `gum format` | Render markdown/code/emoji | `echo "# Results" \| gum format` |
| `gum table` | Format CSV/TSV as tables | `gum table < data.csv` |
| `gum join` | Combine text blocks | `gum join --horizontal block1 block2` |
| `gum log` | Structured logging | `gum log --level info "Step complete"` |
| `gum spin` | Spinner during subprocess | `gum spin --title "Testing..." -- make test` |
| `fzf --filter` | Non-interactive fuzzy match | `fd -t f \| fzf --filter "main"` |
| `bat` | Syntax-highlighted code | `bat -n --color=always --line-range 10:30 file.py` |
| `rich` | Markdown/tables/panels | `rich --markdown --panel rounded file.md` |

**Needs real TTY — use `AskUserQuestion` tool instead:**
`gum choose`, `gum confirm`, `gum input`, `gum write`, `gum filter` (interactive), `fzf` (interactive)

## Plannotator

Plannotator is installed as a Claude Code plugin with an `ExitPlanMode` hook — plans are automatically sent to the Plannotator UI for review before implementation begins.

- When producing or editing `.md` files (specs, design docs, plans, READMEs), offer to run `/plannotator-annotate <file>` so the user can review and annotate the content interactively.
- Use `/plannotator-review` to open code review UI for uncommitted changes or a PR.
- Use `/plannotator-last` to let the user annotate the most recent assistant message.
- Plan archives live at `~/.plannotator/plans/`. Use `/plannotator-compound` to analyze denial patterns across the archive.


***CRITICAL***
NEVER EVER: Do not include attribution to Claude in commit messages. Do not add Co-Authored-By lines.

@RTK.md

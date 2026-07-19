#!/usr/bin/env zsh

# Optional ZSH wrapper for a local Felix clone (poetry env).
# Source from the repo (or set FELIX_DIR to the clone root):
#   source /path/to/felix/zsh_function.sh
#   # or:
#   export FELIX_DIR=/path/to/felix && source "$FELIX_DIR/zsh_function.sh"
#
# Do not hardcode personal home directories in this file.

# Captured when sourced — $0 is the script path then, not when `felix` runs.
typeset -g _FELIX_REPO_DIR="${FELIX_DIR:-${0:A:h}}"

felix() {
    local FELIX_DIR="${FELIX_DIR:-$_FELIX_REPO_DIR}"

    if [[ ! -d "$FELIX_DIR" ]]; then
        echo "Felix directory not found at $FELIX_DIR (set FELIX_DIR or source this file from the repo)" >&2
        return 1
    fi

    # Run from the project env; keep the caller's cwd so relative paths work.
    command poetry -C "$FELIX_DIR" run felix "$@"
}

# Completion function for felix
_felix() {
    local -a commands
    commands=(
        'run:Diagnose, fix, and verify the described problem'
        'doctor:Check the Felix <> AgentForge integration'
        'last:Show the report for the latest run (or RUN_ID)'
        'explain:Explain the root cause and evidence of a run'
        'replay:Replay a run'\''s commands through the dry-run pipeline'
        'undo:Revert a run'\''s file changes via revert_file'
        'skills:Manage the skill fleet (acquire, index, retrieve)'
        'permissions:Manage AgentForge shell/SSH command permissions'
        'help:Show help information'
    )

    local -a skills_subcommands
    skills_subcommands=(
        'list:List the acquired catalog with provenance'
        'find:Search the skills.sh registry for skills matching a query'
        'add:Pull specific skills (owner/repo@skill) into the catalog'
        'pull:Acquire Agent Skills from the sources manifest'
        'discover:Search skills.sh, pull matches, and index them'
        'index:Chunk the catalog and index it into Qdrant'
        'retrieve:Preview which skills would be injected for a problem'
        'vet:Vet a skill without acquiring it'
        'quarantine:List quarantined skills or show findings'
        'approve:Release a quarantined skill into the catalog'
    )

    local -a run_options
    run_options=(
        '--dry-run:Trace the plan; execute nothing'
        '--apply:Auto-apply read-only + low-risk fixes'
        '--yes:Auto-confirm medium-risk (high-risk still prompts)'
        '--read-only:Diagnose and propose only; block all changes'
        '--deep:Deeper investigation (more areas/rounds)'
        '--discover:Search skills.sh for task-relevant skills first'
        '-v:More terminal detail'
        '-vv:Tool outputs and iterations'
        '--help:Show help'
    )

    if [[ ${#words[@]} -eq 2 ]]; then
        _describe 'command' commands
        _describe 'run options' run_options
    elif [[ ${#words[@]} -ge 3 ]]; then
        case ${words[2]} in
            skills)
                if [[ ${#words[@]} -eq 3 ]]; then
                    _describe 'skills command' skills_subcommands
                fi
                ;;
            run)
                _describe 'run options' run_options
                ;;
        esac
    fi
}

# Register completion
if command -v compdef >/dev/null 2>&1; then
    compdef _felix felix
fi

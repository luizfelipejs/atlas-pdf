#!/usr/bin/env bash
# NUL-delimited PDF paths. Call through bash; never evaluate a discovered name.
set -uo pipefail

if (( $# == 0 )); then
    printf '%s\n' 'Informe pelo menos uma pasta para busca.' >&2
    exit 2
fi

result=0
for root in "$@"; do
    if [[ ! -d "$root" || -L "$root" ]]; then
        printf 'Pasta indisponível ou link simbólico: %q\n' "$root" >&2
        result=1
        continue
    fi
    # -P keeps symbolic links out of traversal. Volatile and cache trees are
    # excluded by name even when the user chooses their parent directory.
    find -P "$root" \
        \( -type d \( \
            -name .cache -o -name .git -o -name .Trash -o -name '.Trash-*' \
            -o -path '*/.local/share/Trash' \
            -o -name node_modules -o -name .venv -o -name __pycache__ \
            -o -name proc -o -name sys -o -name dev -o -name lost+found \
        \) -prune \) -o \
        \( -type f -iname '*.pdf' -print0 \) || result=1
done
exit "$result"

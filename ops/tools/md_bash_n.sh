#!/usr/bin/env bash
# Синтаксис команд в файле Markdown: каждый блок ```sh / ```bash — отдельно через `bash -n`.
#
#   bash ops/tools/md_bash_n.sh <файл.md>
#
# Печатает строку на блок («блок N, строка L: ok» или ошибку bash) и итог; код 1, если хоть один блок не разобрался.
set -uo pipefail
[ $# = 1 ] && [ -r "$1" ] || { echo "использование: md_bash_n.sh <файл.md>" >&2; exit 64; }
tmp=$(mktemp -d "${TMPDIR:-/tmp}/md_bash_n.XXXXXX") || exit 1
trap 'rm -rf "$tmp"' EXIT
awk -v dir="$tmp" '
    /^```(sh|bash)[[:space:]]*$/ { n++; on = 1; file = sprintf("%s/%03d_%d.sh", dir, n, NR + 1); next }
    /^```/ && on                 { on = 0; close(file); next }
    on                           { print > file }
' "$1"
total=0; bad=0
for f in "$tmp"/*.sh; do
    [ -e "$f" ] || continue
    total=$((total + 1))
    base=${f##*/}; num=${base%%_*}; line=${base#*_}; line=${line%.sh}
    if err=$(bash -n "$f" 2>&1); then
        echo "блок $((10#$num)), строка $line: ok"
    else
        bad=$((bad + 1))
        echo "блок $((10#$num)), строка $line: ОШИБКА — ${err//$f/блок}"
    fi
done
echo "итог: блоков $total, с ошибкой $bad"
[ "$bad" = 0 ]

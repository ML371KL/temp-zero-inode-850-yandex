#!/usr/bin/env bash
# Сухой прогон первой выкладки на голых репозиториях во временной папке — без сети.
#
#   PY=<питон 3.12 модели> bash ops/tools/deploy_dryrun.sh <коммит> [выпуск.json | --build] [папка]
#
#   <коммит>   кандидат, чьё дерево уйдёт первым push
#   выпуск     готовый выпуск (схема yandex-v1, честный хэш); --build (по умолчанию) —
#              собрать в клоне: ops/build_release.py --book --fast
#   папка      по умолчанию var/deploy-dryrun (var/ в .gitignore)
#
# Что проверяет:
#   код   — ops/tools/first_push.sh в голый code.git: в нём одна ветка main, один коммит
#           без родителя, дерево = дерево кандидата, автор и коммиттер из списка
#           разрешённых, в рабочем дереве клона нет CRLF у текстовых файлов;
#   данные — ops/publish.py из клона кода в голый data.git: init (и повтор — «уже есть»),
#           публикация выпуска, повтор без изменений содержания, откат; в ветке release
#           только .nojekyll, latest.json, releases/<sha>.json; все авторы разрешены;
#           отметка release.pushed несёт коммит кода = корневой коммит;
#   сеть  — сверка --verify --once на заведомо закрытый адрес: доходит до шага сети и
#           отвечает кодом 3, отметку release.commit не пишет.
# Итог — строка «СУХОЙ ПРОГОН: пройден» и код 0; любой провал — код 1.
set -euo pipefail

die() { echo "СУХОЙ ПРОГОН: ПРОВАЛ — $*" >&2; exit 1; }
ok() { echo "  ok  $*"; }
win() { if command -v cygpath >/dev/null 2>&1; then cygpath -m "$1"; else printf '%s\n' "$1"; fi; }

[ $# -ge 1 ] || die "использование: deploy_dryrun.sh <коммит> [выпуск.json | --build] [папка]"
top=$(git rev-parse --show-toplevel)
commit=$(git rev-parse --verify "$1^{commit}")
release=${2:---build}
[ "$release" = --build ] || release=$(cd "$(dirname "$release")" && pwd)/$(basename "$release")   # до cd в клон
work=${3:-$top/var/deploy-dryrun}
PY=${PY:-python}
"$PY" -c 'import sys; assert sys.version_info[:2] == (3, 12), sys.version' \
    || die "нужен Python 3.12 (PY=$PY)"

allow=$(git show "$commit:ops/commit-emails.allow" | sed -e 's/#.*//' -e 's/[[:space:]]//g' | sed '/^$/d')
# Автор — тот же, что у ветки release на сервере (env.example дерева кандидата).
envx=$(git show "$commit:ops/env.example")
NAME=$(printf '%s\n' "$envx" | sed -n 's/^YANDEX_GIT_NAME=//p')
EMAIL=$(printf '%s\n' "$envx" | sed -n 's/^YANDEX_GIT_EMAIL=//p')
allowed() { printf '%s\n' "$allow" | grep -qxF -- "$1"; }

# Папка стирается целиком: только своя, с именем deploy-dryrun* (опечатка в третьем аргументе не снесёт чужое).
case $(basename -- "$work") in
    deploy-dryrun*) ;;
    *) die "папка должна называться deploy-dryrun* (дано: $work)" ;;
esac
rm -rf -- "$work"
mkdir -p "$work"
git init --bare --quiet --initial-branch=main "$work/code.git"     # как пустой репозиторий GitHub: ветка по умолчанию — первая отправленная
git init --bare --quiet --initial-branch=release "$work/data.git"
echo "папка $work; кандидат $commit"

# ---------------------------------------------------------------- код
echo "== код: первый push корневым коммитом"
FIRST_PUSH_NAME=$NAME FIRST_PUSH_EMAIL=$EMAIL bash "$top/ops/tools/first_push.sh" "$commit" "$(win "$work/code.git")" main
root=$(git rev-parse refs/first-push/main)
refs=$(git -C "$work/code.git" for-each-ref --format='%(refname)')
[ "$refs" = refs/heads/main ] || die "в code.git ветки: $refs"
ok "в code.git одна ветка: main"
[ "$(git -C "$work/code.git" rev-list --all --count)" = 1 ] || die "в code.git больше одного коммита"
[ -z "$(git -C "$work/code.git" rev-list --max-parents=0 --min-parents=1 --all)" ] || die "у коммита есть родитель"
ok "в code.git один коммит $root, без родителя"
[ "$(git -C "$work/code.git" rev-parse 'main^{tree}')" = "$(git rev-parse "$commit^{tree}")" ] \
    || die "дерево main в code.git не равно дереву кандидата"
ok "дерево = дерево кандидата $(git rev-parse --short "$commit^{tree}")"
for who in $(git -C "$work/code.git" log --all --format='%ae %ce'); do
    allowed "$who" || die "адрес $who не в ops/commit-emails.allow"
done
ok "автор и коммиттер: $NAME <$EMAIL> — в списке разрешённых"
git clone --quiet --branch main "$(win "$work/code.git")" "$work/code"
crlf=$(git -C "$work/code" ls-files --eol | awk '$2 ~ /^w\/crlf/ && $3 !~ /^attr\/-text/ {print $NF}' \
       | grep -vE '^(data/assumptions/worlds_source\.|common/)' || true)
[ -z "$crlf" ] || die "CRLF в рабочем дереве клона: $(echo $crlf)"
ok "клон: $(git -C "$work/code" ls-files | wc -l | tr -d ' ') файлов, текст — LF (кроме побайтовых копий common/ и записи миров)"
for f in $(git -C "$work/code" ls-files '*.sh'); do bash -n "$work/code/$f" || die "bash -n $f"; done
ok "bash -n всех *.sh дерева"

# ---------------------------------------------------------------- данные
echo "== данные: ops/publish.py против голого data.git"
state=$work/state
mkdir -p "$state/release"
export YANDEX_STATE_DIR=$(win "$state")
export YANDEX_RELEASE_REMOTE=$(win "$work/data.git")
export YANDEX_GIT_NAME=$NAME YANDEX_GIT_EMAIL=$EMAIL
export PYTHONIOENCODING=utf-8 PYTHONUTF8=1                   # вывод питона — UTF-8 и в Git Bash на Windows
export YANDEX_PUBLIC_URL=http://127.0.0.1:9/api/model     # закрытый порт: сеть не трогаем
export YANDEX_VERIFY_TIMEOUT=5 YANDEX_VERIFY_PAUSE=1 YANDEX_PUSH_BACKOFF=0
cd "$work/code"

out=$("$PY" ops/publish.py init) || die "publish.py init"
echo "$out" | grep -q "ветка release создана" || die "init: $out"
out=$("$PY" ops/publish.py init) || die "publish.py init (повтор)"
echo "$out" | grep -q "уже есть" || die "init (повтор): $out"
ok "init: ветка release создана; повтор — «уже есть»"

if [ "$release" = --build ]; then
    t0=$(date +%s)
    "$PY" ops/build_release.py --book --fast --out "$YANDEX_STATE_DIR/release/latest.json" >"$work/build.log" 2>&1 \
        || { tail -20 "$work/build.log"; die "сборка --book --fast"; }
    ok "выпуск собран в клоне: --book --fast за $(( $(date +%s) - t0 )) с"
else
    cp "$release" "$state/release/latest.json"
fi
sha=$("$PY" -c 'import json,sys; print(json.load(open(sys.argv[1],encoding="utf-8"))["meta"]["payload_sha256"])' \
      "$YANDEX_STATE_DIR/release/latest.json")

"$PY" ops/publish.py >"$work/publish-1.log" 2>&1 || { cat "$work/publish-1.log"; die "публикация"; }
ok "публикация: выпуск ${sha:0:12}"
"$PY" ops/publish.py >"$work/publish-2.log" 2>&1 || { cat "$work/publish-2.log"; die "повтор публикации"; }
grep -q "уже в ветке — не переписываю" "$work/publish-2.log" || die "повтор переписал releases/${sha:0:12}"
ok "повтор публикации: releases/${sha:0:12}….json не переписан, указатель — новым published_at"
"$PY" ops/publish.py rollback "${sha:0:12}" >"$work/rollback.log" 2>&1 || { cat "$work/rollback.log"; die "откат"; }
grep -q "откат: указатель смотрит на ${sha:0:12}" "$work/rollback.log" || die "откат: $(cat "$work/rollback.log")"
ok "откат на ${sha:0:12}: прошёл"

refs=$(git -C "$work/data.git" for-each-ref --format='%(refname)')
[ "$refs" = refs/heads/release ] || die "в data.git ветки: $refs"
files=$(git -C "$work/data.git" ls-tree -r --name-only release | tr '\n' ' ')
[ "$files" = ".nojekyll latest.json releases/$sha.json " ] || die "в ветке release: $files"
ok "в data.git одна ветка release: $files"
n=$(git -C "$work/data.git" rev-list --count release)
[ "$(git -C "$work/data.git" rev-list --max-parents=0 release | wc -l | tr -d ' ')" = 1 ] || die "в release больше одного корня"
for who in $(git -C "$work/data.git" log --all --format='%ae %ce'); do
    allowed "$who" || die "адрес $who в ветке release"
done
ok "в release $n коммитов от одного корня; все авторы — $EMAIL"
served=$(git -C "$work/data.git" show release:latest.json | "$PY" -c 'import json,sys; m=json.load(sys.stdin)["meta"]; print(m["payload_sha256"], m.get("published_at",""))')
[ "${served%% *}" = "$sha" ] && [ -n "${served#* }" ] || die "latest.json: $served"
ok "latest.json: хэш ${sha:0:12}, published_at ${served#* }"
mark=$("$PY" -c 'import json,sys; m=json.load(open(sys.argv[1],encoding="utf-8")); print(m["kind"], m["code_commit"] or "-")' \
       "$YANDEX_STATE_DIR/release.pushed")
[ "$mark" = "rollback -" ] || die "release.pushed после отката: $mark"
first_mark=$(git -C "$work/data.git" log --reverse --format=%s release | sed -n 2p)
ok "release.pushed — откат; первый коммит выпуска: «$first_mark»"

# Коммит кода в отметке публикации = корневой коммит (повтор публикации пишет вид publish).
"$PY" ops/publish.py >"$work/publish-3.log" 2>&1 || { cat "$work/publish-3.log"; die "публикация после отката"; }
cc=$("$PY" -c 'import json,sys; print(json.load(open(sys.argv[1],encoding="utf-8"))["code_commit"])' "$YANDEX_STATE_DIR/release.pushed")
[ "$cc" = "$root" ] || die "release.pushed: коммит кода $cc, ожидался корневой $root"
ok "release.pushed: коммит кода = корневой коммит ${root:0:12}"

# ---------------------------------------------------------------- шаг сети
echo "== сверка: до шага сети"
set +e
"$PY" ops/publish.py --verify --once >"$work/verify.log" 2>&1
rc=$?
set -e
[ "$rc" = 3 ] || { cat "$work/verify.log"; die "сверка на закрытом адресе: код $rc, ожидался 3"; }
[ ! -e "$YANDEX_STATE_DIR/release.commit" ] || die "release.commit записан без сверки"
ok "сверка дошла до двери ($YANDEX_PUBLIC_URL), код 3, release.commit не записан"

echo "СУХОЙ ПРОГОН: пройден — корневой ${root:0:12}, выпуск ${sha:0:12}"

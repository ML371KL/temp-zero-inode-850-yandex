#!/usr/bin/env bash
# Первый push публичного репозитория кода: ОДИН свежий корневой коммит без истории.
#
#   bash ops/tools/first_push.sh <коммит> <адрес репозитория> [ветка]     (ветка по умолчанию main)
#
# Берёт ДЕРЕВО коммита (не историю), собирает над ним корневой коммит с автором
# и коммиттером из окружения, проверяет и отправляет в <адрес>:<ветка>.
#
# Окружение:
#   FIRST_PUSH_NAME, FIRST_PUSH_EMAIL  автор и коммиттер; адрес обязан быть в
#                                      ops/commit-emails.allow ДЕРЕВА коммита
#   FIRST_PUSH_MESSAGE                 сообщение коммита (есть значение по умолчанию)
#   FIRST_PUSH_DATE                    дата автора и коммиттера (git-формат), по умолчанию — сейчас
#   NO_PUSH=1                          только собрать и проверить, без push
#   HYGIENE_WORDS                      файл личных слов ВНЕ репозитория (по одному на строку), необязательно
#
# Проверки до push (любая не прошла — код 1, ничего не отправлено):
#   адрес автора в списке разрешённых; в дереве нет путей ноутбука, закрытых ключей,
#   токенов, адресов IPv4 кроме 127.0.0.1 и 0.0.0.0, первички (pdf, xlsx, zip, docx);
#   корневой коммит — один, без родителя, его дерево = дерево коммита.
# После push: ветка удалённого репозитория указывает ровно на корневой коммит.
set -euo pipefail

die() { echo "first_push: ОТКАЗ — $*" >&2; exit 1; }

[ $# -ge 2 ] || die "использование: first_push.sh <коммит> <адрес репозитория> [ветка]"
commit=$1
remote=$2
branch=${3:-main}
name=${FIRST_PUSH_NAME:-}
email=${FIRST_PUSH_EMAIL:-}
message=${FIRST_PUSH_MESSAGE:-"Модель «Яндекс 850»: первый выпуск кода"}

[ -n "$name" ] && [ -n "$email" ] || die "не заданы FIRST_PUSH_NAME / FIRST_PUSH_EMAIL"
tree=$(git rev-parse --verify --quiet "${commit}^{tree}") || die "нет коммита $commit"

# 1. Адрес автора — из списка разрешённых самого дерева.
allow=$(git show "${commit}:ops/commit-emails.allow" 2>/dev/null | sed -e 's/#.*//' -e 's/[[:space:]]//g' | sed '/^$/d') \
    || die "в дереве нет ops/commit-emails.allow"
printf '%s\n' "$allow" | grep -qxF -- "$email" || die "адрес $email не в ops/commit-emails.allow"

# 2. Гигиена дерева (git grep по дереву, а не по рабочей папке).
hits=$(git grep -I -n -E \
    -e '[A-Za-z]:[\\/]+Users[\\/]' -e '/c/Users/' -e '/home/[a-z]' \
    -e 'BEGIN [A-Z ]*PRIVATE KEY' -e '(ghp|gho|ghs|github_pat)_[A-Za-z0-9]{16,}' \
    "$tree" -- . ':(exclude)ops/tools/first_push.sh' 2>/dev/null | cut -c1-200 || true)   # сам скрипт несёт образцы
[ -z "$hits" ] || die "в дереве пути ноутбука, ключи или токены:
$hits"
ips=$(git grep -I -h -o -E '\b([0-9]{1,3}\.){3}[0-9]{1,3}\b' "$tree" -- . 2>/dev/null \
      | grep -vxE '127\.0\.0\.1|0\.0\.0\.0' | sort -u || true)
[ -z "$ips" ] || die "в дереве адреса IPv4: $(echo $ips)"
primary=$(git ls-tree -r --name-only "$tree" | grep -iE '\.(pdf|xlsx|xls|zip|docx)$' || true)
[ -z "$primary" ] || die "в дереве первичка: $(echo $primary)"
if [ -n "${HYGIENE_WORDS:-}" ]; then
    [ -r "$HYGIENE_WORDS" ] || die "файл личных слов $HYGIENE_WORDS не читается"
    words=$(git grep -I -l -i -F -f "$HYGIENE_WORDS" "$tree" -- . 2>/dev/null || true)
    [ -z "$words" ] || die "личные слова в файлах: $(echo $words)"
fi

# 3. Корневой коммит: без родителя, автор и коммиттер — из окружения.
export GIT_AUTHOR_NAME=$name GIT_AUTHOR_EMAIL=$email GIT_COMMITTER_NAME=$name GIT_COMMITTER_EMAIL=$email
if [ -n "${FIRST_PUSH_DATE:-}" ]; then
    export GIT_AUTHOR_DATE=$FIRST_PUSH_DATE GIT_COMMITTER_DATE=$FIRST_PUSH_DATE
fi
root=$(printf '%s\n' "$message" | git commit-tree "$tree")
[ "$(git rev-list --count "$root")" = 1 ] || die "у коммита $root есть история"
[ "$(git rev-parse "${root}^{tree}")" = "$tree" ] || die "дерево корневого коммита не то"
for who in $(git log -1 --format='%ae %ce' "$root"); do
    printf '%s\n' "$allow" | grep -qxF -- "$who" || die "в коммите адрес $who"
done
git update-ref "refs/first-push/$branch" "$root"
echo "корневой коммит $root (дерево $tree, файлов $(git ls-tree -r --name-only "$tree" | wc -l | tr -d ' '))"
echo "  автор и коммиттер: $name <$email>; ссылка refs/first-push/$branch"

if [ "${NO_PUSH:-0}" = 1 ]; then
    echo "NO_PUSH=1 — push не делался"
    exit 0
fi

# 4. Push и сверка: ветка удалённого репозитория = корневой коммит.
git push --quiet "$remote" "$root:refs/heads/$branch"
seen=$(git ls-remote "$remote" "refs/heads/$branch" | cut -f1)
[ "$seen" = "$root" ] || die "после push $remote $branch = ${seen:-нет}, ожидался $root"
echo "отправлено: $remote $branch = $root"

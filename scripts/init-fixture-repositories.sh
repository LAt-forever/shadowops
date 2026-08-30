#!/bin/sh
set -eu

configure_repository() {
    repository="$1"
    if [ ! -d "$repository/.git" ]; then
        git -C "$repository" init --quiet
        git -C "$repository" config user.name "ShadowOps Fixtures"
        git -C "$repository" config user.email "fixtures@shadowops.local"
    fi
}

commit_staged() {
    repository="$1"
    message="$2"
    if ! git -C "$repository" diff --cached --quiet; then
        GIT_AUTHOR_DATE="2026-08-26T00:00:00Z" \
        GIT_COMMITTER_DATE="2026-08-26T00:00:00Z" \
            git -C "$repository" commit --quiet --message "$message"
    fi
}

noop="tests/fixtures/repositories/projects/m1-noop-demo"
configure_repository "$noop"
git -C "$noop" add --all
commit_staged "$noop" "fixture: linear Alembic repository"

for repository in \
    tests/fixtures/repositories/projects/safe-add-column \
    tests/fixtures/repositories/projects/dangerous-drop \
    tests/fixtures/repositories/projects/broken-upgrade \
    tests/fixtures/repositories/projects/slow-upgrade \
    tests/fixtures/repositories/projects/unique-conflict \
    tests/fixtures/repositories/projects/irreversible-roundtrip \
    tests/fixtures/repositories/projects/type-conversion-failure \
    tests/fixtures/repositories/projects/unsupported-type
do
    configure_repository "$repository"
    if ! git -C "$repository" rev-parse --verify HEAD >/dev/null 2>&1; then
        git -C "$repository" add alembic.ini migrations/env.py migrations/versions/001_base.py
        commit_staged "$repository" "fixture: baseline schema"
    fi
done

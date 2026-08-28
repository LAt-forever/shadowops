#!/bin/sh
set -eu

repository="tests/fixtures/repositories/projects/m1-noop-demo"

if [ ! -d "$repository/.git" ]; then
    git -C "$repository" init --quiet
    git -C "$repository" config user.name "ShadowOps Fixtures"
    git -C "$repository" config user.email "fixtures@shadowops.local"
fi

git -C "$repository" add --all
if ! git -C "$repository" diff --cached --quiet; then
    GIT_AUTHOR_DATE="2026-08-26T00:00:00Z" \
    GIT_COMMITTER_DATE="2026-08-26T00:00:00Z" \
        git -C "$repository" commit --quiet --message "fixture: linear Alembic repository"
fi

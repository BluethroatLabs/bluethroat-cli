release:
    #!/usr/bin/env bash
    set -euo pipefail

    if [[ "$(git branch --show-current)" != "main" ]]; then
        echo "error: releases must be created from main" >&2
        exit 1
    fi

    if [[ -n "$(git status --porcelain)" ]]; then
        echo "error: commit or stash all changes before releasing" >&2
        exit 1
    fi

    git fetch origin main --tags
    if [[ "$(git rev-parse HEAD)" != "$(git rev-parse origin/main)" ]]; then
        echo "error: local main must exactly match origin/main" >&2
        exit 1
    fi

    version="$(uv version --short)"
    tag="v${version}"
    if git show-ref --verify --quiet "refs/tags/${tag}"; then
        echo "error: tag ${tag} already exists" >&2
        exit 1
    fi

    uv sync --locked --all-groups
    uv run ruff format --check .
    uv run ruff check .
    uv run pytest
    uv build

    git tag --annotate "${tag}" --message "${tag}"
    git push origin "${tag}"
    echo "Pushed ${tag}; the Release workflow will publish it."

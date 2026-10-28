#!/usr/bin/env bash
# Fetch the pinned third-party sources of tools/vmcu/deps.lock into <dir> (shallow fetch of the exact commit).
set -euo pipefail
dst=${1:?usage: fetch_deps.sh <dir>}
mkdir -p "$dst"
grep -v '^#' "$(dirname "$0")/deps.lock" | while read -r name url _ref commit; do
  [ -z "$name" ] && continue
  if [ -d "$dst/$name/.git" ]; then echo "$name: present"; continue; fi
  git init -q "$dst/$name"
  git -C "$dst/$name" remote add origin "$url"
  git -C "$dst/$name" fetch -q --depth 1 origin "$commit"
  git -C "$dst/$name" checkout -q FETCH_HEAD
  echo "$name: $(git -C "$dst/$name" rev-parse HEAD)"
done

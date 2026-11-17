#!/bin/sh
CHIEF_PROJECT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$CHIEF_PROJECT_DIR" || exit 1
exec ./chief demo

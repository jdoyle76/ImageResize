#!/usr/bin/env bash
# Rename or copy files to random hex filenames, preserving extension.
#
# In-place mode (rename):
#   ./randomize-names.sh <source-dir> [--dry-run]
#
# Copy mode (source files untouched):
#   ./randomize-names.sh <source-dir> --output <target-dir> [--dry-run]

set -e

usage() {
    echo "Usage:" >&2
    echo "  $(basename "$0") <source-dir> [--dry-run]" >&2
    echo "  $(basename "$0") <source-dir> --output <target-dir> [--dry-run]" >&2
    exit 1
}

SOURCE_DIR=""
OUTPUT_DIR=""
DRY_RUN=false

# Parse arguments
while [[ $# -gt 0 ]]; do
    case "$1" in
        --output) OUTPUT_DIR="${2:-}"; shift 2 ;;
        --dry-run) DRY_RUN=true; shift ;;
        -*) echo "Unknown option: $1" >&2; usage ;;
        *) SOURCE_DIR="$1"; shift ;;
    esac
done

[[ -z "$SOURCE_DIR" ]] && usage

if [[ ! -d "$SOURCE_DIR" ]]; then
    echo "Error: '$SOURCE_DIR' is not a directory." >&2
    exit 1
fi

SOURCE_DIR="$(cd "$SOURCE_DIR" && pwd)"

if [[ -n "$OUTPUT_DIR" ]]; then
    if [[ "$(cd "$OUTPUT_DIR" 2>/dev/null && pwd)" == "$SOURCE_DIR" ]]; then
        echo "Error: source and output directories must be different." >&2
        exit 1
    fi
    if ! $DRY_RUN; then
        mkdir -p "$OUTPUT_DIR"
    fi
    OUTPUT_DIR="$(cd "$OUTPUT_DIR" 2>/dev/null && pwd || echo "$OUTPUT_DIR")"
fi

count=0

while IFS= read -r -d '' file; do
    base="$(basename "$file")"
    if [[ "$base" == *.* ]]; then
        ext="${base##*.}"
        new_name="$(openssl rand -hex 16).${ext}"
    else
        new_name="$(openssl rand -hex 16)"
    fi

    if [[ -n "$OUTPUT_DIR" ]]; then
        # Preserve subdirectory structure under output dir
        rel_dir="$(dirname "${file#"$SOURCE_DIR"/}")"
        dest_dir="$OUTPUT_DIR/$rel_dir"
        new_path="$dest_dir/$new_name"

        if $DRY_RUN; then
            echo "[dry-run] cp $file -> $new_path"
        else
            mkdir -p "$dest_dir"
            cp "$file" "$new_path"
            echo "$file -> $new_path"
        fi
    else
        new_path="$(dirname "$file")/$new_name"

        if $DRY_RUN; then
            echo "[dry-run] mv $file -> $new_path"
        else
            mv "$file" "$new_path"
            echo "$file -> $new_path"
        fi
    fi

    ((count++))
done < <(find "$SOURCE_DIR" -type f -print0)

echo ""
if [[ -n "$OUTPUT_DIR" ]]; then
    echo "Done. $count file(s) copied to '$OUTPUT_DIR' with randomized names."
else
    echo "Done. $count file(s) renamed in place."
fi

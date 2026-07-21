#!/usr/bin/env bash
set -euo pipefail

# cleanup_nodes.sh
# Backup e pulizia automatica dei record 'nodes' contenenti un pattern
# Uso:
#   ./scripts/cleanup_nodes.sh [--db PATH] [--pattern PAT] [--backup-dir DIR] [--no-dry-run] [--delete] [--replace NODE_ID:NEW_EP] [--container NAME]
# Esempi:
#   ./scripts/cleanup_nodes.sh --pattern ngrok           # mostra i nodi che corrispondono (dry-run)
#   ./scripts/cleanup_nodes.sh --pattern ngrok --delete --no-dry-run
#   ./scripts/cleanup_nodes.sh --replace 85968...:http://10.0.0.1:8084 --no-dry-run
#   ./scripts/cleanup_nodes.sh --container hyperspace_control_plane --pattern ngrok --delete --no-dry-run

DB_PATH="${DB_PATH:-./data/hyperspace.db}"
BACKUP_DIR="${BACKUP_DIR:-./data/backups}"
PATTERN="${PATTERN:-ngrok}"
DRY_RUN=1
DELETE=0
REPLACE=""
CONTAINER=""

usage(){
  sed -n '1,120p' "$0" | sed -n '1,120p'
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --db) DB_PATH="$2"; shift 2;;
    --backup-dir) BACKUP_DIR="$2"; shift 2;;
    --pattern) PATTERN="$2"; shift 2;;
    --no-dry-run) DRY_RUN=0; shift;;
    --dry-run) DRY_RUN=1; shift;;
    --delete) DELETE=1; shift;;
    --replace) REPLACE="$2"; shift 2;;
    --container) CONTAINER="$2"; shift 2;;
    -h|--help) usage; exit 0;;
    *) echo "Unknown arg: $1"; usage; exit 2;;
  esac
done

timestamp(){ date +%Y%m%dT%H%M%S }

run_sql_local(){
  local sql="$1"
  sqlite3 "$DB_PATH" "$sql"
}

run_sql_docker(){
  local sql="$1"
  docker exec -i "$CONTAINER" sh -c "sqlite3 '$DB_PATH' \"$sql\""
}

echo "DB_PATH=$DB_PATH"
echo "PATTERN=$PATTERN"
if [[ -n "$CONTAINER" ]]; then
  echo "MODE=docker (container=$CONTAINER)"
else
  echo "MODE=local"
fi

# Ensure sqlite3 available locally when needed
if [[ -z "$CONTAINER" ]]; then
  if ! command -v sqlite3 >/dev/null 2>&1; then
    echo "Error: sqlite3 CLI not found on PATH. Install sqlite3 or use --container." >&2
    exit 3
  fi
  if [[ ! -f "$DB_PATH" ]]; then
    echo "Error: DB file not found at $DB_PATH" >&2
    exit 3
  fi
fi

# Backup
if [[ -n "$CONTAINER" ]]; then
  echo "Creating backup inside container..."
  bakname="${DB_PATH}.bak-$(timestamp)"
  docker exec "$CONTAINER" sh -c "cp '$DB_PATH' '$bakname' && echo '$bakname'" || true
  echo "Backup created inside container: $bakname"
else
  mkdir -p "$BACKUP_DIR"
  bakfile="$BACKUP_DIR/hyperspace.db.$(timestamp).bak"
  cp -v "$DB_PATH" "$bakfile"
  echo "Backup saved to $bakfile"
fi

# Preview matching rows
SQL_SELECT="SELECT node_id || '|' || COALESCE(endpoint,'') || '|' || status || '|' || last_seen FROM nodes WHERE endpoint LIKE '%$PATTERN%';"
if [[ -n "$CONTAINER" ]]; then
  echo "\nMatches (docker):"
  docker exec -i "$CONTAINER" sh -c "sqlite3 '$DB_PATH' \"$SQL_SELECT\"" || true
else
  echo "\nMatches (local):"
  sqlite3 "$DB_PATH" "$SQL_SELECT" || true
fi

if [[ $DRY_RUN -eq 1 ]]; then
  echo "\nDry-run mode: no destructive changes will be performed. Use --no-dry-run to apply changes."
fi

# Perform delete if requested
if [[ $DELETE -eq 1 ]]; then
  if [[ $DRY_RUN -eq 1 ]]; then
    echo "\n[DRY] Would run: DELETE FROM nodes WHERE endpoint LIKE '%$PATTERN%';"
  else
    echo "\nDeleting nodes matching pattern '$PATTERN'..."
    SQL_COUNT_BEFORE="SELECT COUNT(*) FROM nodes WHERE endpoint LIKE '%$PATTERN%';"
    if [[ -n "$CONTAINER" ]]; then
      before=$(run_sql_docker "$SQL_COUNT_BEFORE") || before=0
      docker exec -i "$CONTAINER" sh -c "sqlite3 '$DB_PATH' \"DELETE FROM nodes WHERE endpoint LIKE '%$PATTERN%';\""
      after=$(run_sql_docker "$SQL_COUNT_BEFORE") || after=0
    else
      before=$(run_sql_local "$SQL_COUNT_BEFORE" ) || before=0
      run_sql_local "DELETE FROM nodes WHERE endpoint LIKE '%$PATTERN%';"
      after=$(run_sql_local "$SQL_COUNT_BEFORE" ) || after=0
    fi
    echo "Deleted: $((before - after)) rows (before=$before after=$after)"
  fi
fi

# Perform replace if requested: format NODEID:NEW_ENDPOINT
if [[ -n "$REPLACE" ]]; then
  IFS=":" read -r nid newep <<< "$REPLACE"
  if [[ -z "$nid" || -z "$newep" ]]; then
    echo "Invalid --replace argument. Expect NODE_ID:NEW_ENDPOINT" >&2
    exit 4
  fi
  if [[ $DRY_RUN -eq 1 ]]; then
    echo "\n[DRY] Would run: UPDATE nodes SET endpoint='$newep', last_seen=datetime('now') WHERE node_id='$nid';"
  else
    echo "\nUpdating node $nid -> $newep"
    SQL_UPD="UPDATE nodes SET endpoint='$newep', last_seen=datetime('now') WHERE node_id='$nid';"
    if [[ -n "$CONTAINER" ]]; then
      run_sql_docker "$SQL_UPD"
    else
      run_sql_local "$SQL_UPD"
    fi
    echo "Updated."
  fi
fi

echo "\nDone. Review backup and logs before restarting services if you made changes." 

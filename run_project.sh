#!/usr/bin/env bash
# run_project.sh - run the CN mini-project experiments (Mininet + Ryu) and analyze them.
#
# Usage:  ./run_project.sh [local|static|adaptive|all|analyze|clean]
#   local     UDP sender/receiver on loopback (no Mininet/Ryu needed; quick sanity check)
#   static    Mininet, primary 5 Mbps path only, no controller   (topology.py --standalone)
#   adaptive  Mininet + Ryu congestion_controller.py (reroutes to the 20 Mbps path)
#   all       static + adaptive + analyze   (default)
#   analyze   only run analyze_results.py on whatever is in results/
#   clean     delete results/<scenario> folders and clean up Mininet
#
# Needs for static/adaptive: mininet, openvswitch, ryu (ryu-manager), sudo.
set -u

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RESULTS="$ROOT/results"
PYTHON="${PYTHON:-python3}"
MODE="${1:-all}"

PORT=5005
H2_IP="10.0.0.2"
GAP=3                      # seconds between workloads

# name:rate(pps):size(bytes):duration(s)
# HIGH stays under the 5 Mbps limit; CONGESTION (~5 Mbps) is what triggers the reroute
# (the controller reroutes after 2 polls at >= 80% of 5 Mbps).
WORKLOADS=(
  "LOW:5:256:10"
  "MODERATE:50:512:10"
  "HIGH:200:1024:10"
  "CONGESTION:450:1400:20"
)

SUDO=""; [ "$(id -u)" -ne 0 ] && SUDO="sudo"
OWNER="${SUDO_USER:-$(id -un)}"
RYU_PID=""

log()  { echo -e "\n[run_project] $*"; }
die()  { echo "[run_project] ERROR: $*" >&2; exit 1; }
need() { command -v "$1" >/dev/null 2>&1 || die "'$1' not found. $2"; }

cleanup() {
  [ -n "$RYU_PID" ] && kill "$RYU_PID" 2>/dev/null
  pkill -f udp_receiver.py 2>/dev/null
  if command -v mn >/dev/null 2>&1; then $SUDO mn -c >/dev/null 2>&1; fi
  [ -d "$RESULTS" ] && $SUDO chown -R "$OWNER" "$RESULTS" 2>/dev/null
}
trap cleanup EXIT

# ---------------------------------------------------------------- local (loopback)
run_local() {
  local out="$RESULTS/local"
  need "$PYTHON" "Install Python 3."
  rm -rf "$out"; mkdir -p "$out"
  log "LOCAL run (loopback) -> $out"

  ( cd "$out" && exec "$PYTHON" "$ROOT/udp_receiver.py" > receiver.log 2>&1 ) &
  local rpid=$!
  sleep 1

  local w name rate size dur
  for w in "${WORKLOADS[@]}"; do
    IFS=: read -r name rate size dur <<< "$w"
    log "$name: $rate pps x $size B for ${dur}s"
    "$PYTHON" "$ROOT/udp_sender.py" --host 127.0.0.1 --port "$PORT" \
        --rate "$rate" --size "$size" --duration "$dur" \
        --stats "$out/sender_stats.csv" > "$out/sender_$name.log" 2>&1
    sleep 1
  done
  kill "$rpid" 2>/dev/null
}

# ---------------------------------------------------------------- mininet scenarios
run_mininet() {
  local scenario="$1"           # static | adaptive
  local out="$RESULTS/$scenario"
  local cli="$out/mininet_cmds.txt"
  local topo_args=""

  need mn      "Install Mininet (sudo apt install mininet openvswitch-switch)."
  need "$PYTHON" "Install Python 3."
  rm -rf "$out"; mkdir -p "$out"
  $SUDO mn -c >/dev/null 2>&1

  if [ "$scenario" = "static" ]; then
    topo_args="--standalone"
  else
    need ryu-manager "Install Ryu (pip install ryu; may need an older Python/eventlet)."
    log "Starting Ryu controller"
    ryu-manager "$ROOT/congestion_controller.py" > "$out/controller.log" 2>&1 &
    RYU_PID=$!
    sleep 3
    kill -0 "$RYU_PID" 2>/dev/null || die "Ryu failed to start; see $out/controller.log"
  fi

  # Build the commands that get typed into the Mininet CLI
  {
    echo "h2 cd $out && $PYTHON $ROOT/udp_receiver.py > $out/receiver.log 2>&1 &"
    echo "sh sleep 1"
    if [ "$scenario" = "adaptive" ]; then
      echo "sh sleep 4"                       # let switches connect + rules install
    fi
    echo "h1 ping -c 3 -W 2 $H2_IP > $out/ping.log 2>&1"
    local w name rate size dur
    for w in "${WORKLOADS[@]}"; do
      IFS=: read -r name rate size dur <<< "$w"
      echo "h1 $PYTHON $ROOT/udp_sender.py --host $H2_IP --port $PORT --rate $rate --size $size --duration $dur --stats $out/sender_stats.csv > $out/sender_$name.log 2>&1"
      echo "sh sleep $GAP"
    done
    echo "h2 pkill -f udp_receiver.py"
    echo "exit"
  } > "$cli"

  log "Running '$scenario' experiment in Mininet (about $(( ${#WORKLOADS[@]} * 15 + 20 )) s)"
  ( cd "$ROOT" && $SUDO "$PYTHON" topology.py $topo_args < "$cli" > "$out/mininet.log" 2>&1 )

  if [ -n "$RYU_PID" ]; then kill "$RYU_PID" 2>/dev/null; RYU_PID=""; fi
  $SUDO chown -R "$OWNER" "$out" 2>/dev/null

  [ -s "$out/receiver_stats.csv" ] || echo "[run_project] WARNING: no receiver_stats.csv for $scenario - check $out/mininet.log and $out/receiver.log"
  grep -E "bytes from|packet loss" "$out/ping.log" 2>/dev/null | tail -2
}

run_analyze() {
  log "Analyzing results"
  "$PYTHON" "$ROOT/analyze_results.py" --results "$RESULTS"
}

case "$MODE" in
  local)    run_local;    run_analyze ;;
  static)   run_mininet static;   run_analyze ;;
  adaptive) run_mininet adaptive; run_analyze ;;
  all)      run_mininet static; run_mininet adaptive; run_analyze ;;
  analyze)  run_analyze ;;
  clean)    rm -rf "$RESULTS/local" "$RESULTS/static" "$RESULTS/adaptive"; log "cleaned" ;;
  *)        sed -n '2,13p' "${BASH_SOURCE[0]}"; exit 1 ;;
esac

# Settings for congestion_controller.py.
# Change values here instead of editing the controller.

# ---------- hosts ----------
H1_IP = '10.0.0.1'
H2_IP = '10.0.0.2'

# ---------- switches and ports ----------
# Datapath IDs: s1=1, s2=2, s3=3, s4=4.
# Port numbers come from the link order in topology.py
# (verify with `net` in the Mininet CLI if the topology changes).
S1, S2, S3, S4 = 1, 2, 3, 4
S1_HOST_PORT = 1        # s1 -> h1
S1_PATH_A_PORT = 2      # s1 -> s2
S1_PATH_B_PORT = 3      # s1 -> s3
MID_TO_S1_PORT = 1      # s2 and s3 -> s1
MID_TO_S4_PORT = 2      # s2 and s3 -> s4
S4_PATH_A_PORT = 1      # s4 -> s2
S4_PATH_B_PORT = 2      # s4 -> s3
S4_HOST_PORT = 3        # s4 -> h2

# ---------- link capacities (must match bw= in topology.py) ----------
PATH_A_CAPACITY = 5_000_000     # bits per second
PATH_B_CAPACITY = 20_000_000

# ---------- monitoring ----------
POLL_INTERVAL = 2               # seconds between port-statistics requests

# ---------- congestion detection (Path A -> Path B) ----------
CONGESTION_THRESHOLD = 0.80     # Path A utilization that counts as congested
CONGESTION_SAMPLES = 2          # readings in a row needed to reroute

# ---------- switching back (Path B -> Path A) ----------
# Demand = traffic entering s1 from h1, measured as a share of Path A capacity.
# Switch back only when that demand would fit comfortably on Path A.
SWITCH_BACK_THRESHOLD = 0.40    # demand at or below 40% of Path A (2 Mbps)
SWITCH_BACK_SAMPLES = 3         # readings in a row needed to switch back

# ---------- anti-flapping ----------
MIN_HOLD_TIME = 10              # seconds to stay on a path before any switch

# ---------- output files (created in this folder, one pair per run) ----------
RESULTS_DIR = 'results'

"""Quick check: NodeStatsPoller parses the node's /metrics output."""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.tui.bot_process import NodeStatsPoller

poller = NodeStatsPoller()
time.sleep(1.5)  # one poll cycle
print("available:", poller.available)
print("memory_mb:", round(poller.memory_mb, 1))
print("sessions:", poller.sessions)
print("engines_str:", poller.engines_str)
assert poller.available, "poller should reach the running node"
assert poller.memory_mb > 0
print("POLLER OK")

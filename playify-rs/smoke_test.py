"""Quick smoke test for the playify-rs control plane (no Discord needed)."""

import asyncio
import json
import sys

import aiohttp


async def main():
    async with aiohttp.ClientSession() as s:
        async with s.ws_connect("ws://127.0.0.1:8791") as ws:
            await ws.send_json({"op": "ping", "request_id": 1})
            reply = await asyncio.wait_for(ws.receive_json(), timeout=5)
            print("REPLY:", json.dumps(reply))
            assert reply["ok"] and reply["data"]["pong"], reply

            await ws.send_json({"op": "status", "guild_id": 123, "request_id": 2})
            reply = await asyncio.wait_for(ws.receive_json(), timeout=5)
            print("REPLY:", json.dumps(reply))
            assert reply["ok"] and reply["data"]["connected"] is False, reply

            await ws.send_json({"op": "bogus", "request_id": 3})
            reply = await asyncio.wait_for(ws.receive_json(), timeout=5)
            print("REPLY:", json.dumps(reply))
            assert not reply["ok"], reply

            await ws.send_json({"op": "stats", "request_id": 4})
            reply = await asyncio.wait_for(ws.receive_json(), timeout=5)
            print("REPLY:", json.dumps(reply))
            data = reply["data"]
            assert reply["ok"] and data["sessions"] == 0, reply
            assert "memory_bytes" in data and "uptime_seconds" in data, reply

        # Prometheus endpoint (separate port, plain HTTP)
        async with s.get("http://127.0.0.1:8792/metrics") as response:
            assert response.status == 200, response.status
            text = await response.text()
            print("METRICS:", text.splitlines()[2])
            assert "playify_node_sessions 0" in text, text
            assert 'playify_node_engine_tracks{engine="dsp"} 0' in text, text

        async with s.get("http://127.0.0.1:8792/nope") as response:
            assert response.status == 404, response.status

    print("SMOKE TEST OK")


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

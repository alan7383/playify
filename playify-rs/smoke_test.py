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

    print("SMOKE TEST OK")


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

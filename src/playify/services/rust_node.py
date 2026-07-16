"""Client for the playify-rs audio node.

When PLAYIFY_RUST_NODE is enabled, voice connections are handled by a local
Rust process (playify-rs) built on songbird. discord.py keeps the gateway and
hands the voice credentials to the node over a local WebSocket; the node owns
the UDP voice connection, decoding, Opus encoding and the 20 ms send loop.

RustVoiceClient implements the subset of the discord.VoiceClient surface that
the rest of the codebase uses (play/stop/pause/resume/is_playing/is_paused/
source.volume/move_to/disconnect), so call sites stay identical whichever
backend is active.
"""

import asyncio
import itertools
import json
import os
import platform as _platform
import subprocess
import sys
import time

import aiohttp
import discord

from ..core import PROJECT_ROOT, logger

USE_RUST_NODE = os.getenv("PLAYIFY_RUST_NODE", "").strip().lower() in (
    "1",
    "true",
    "yes",
    "on",
)
NODE_PORT = int(os.getenv("PLAYIFY_NODE_PORT", "8791"))
NODE_SECRET = os.getenv("PLAYIFY_NODE_SECRET", "").strip() or None
NODE_URL = f"ws://127.0.0.1:{NODE_PORT}"


def _node_binary_candidates():
    exe = "playify-rs.exe" if _platform.system() == "Windows" else "playify-rs"
    return [
        PROJECT_ROOT / "bin" / exe,
        PROJECT_ROOT / "playify-rs" / "target" / "release" / exe,
        PROJECT_ROOT / "playify-rs" / "target" / "debug" / exe,
    ]


class NodeClient:
    """Singleton WebSocket client for the audio node.

    Handles request/response correlation, event dispatch to voice clients,
    reconnection with backoff, and (optionally) spawning the node binary.
    """

    def __init__(self):
        self._ws = None
        self._session = None
        self._listen_task = None
        self._request_id = itertools.count(1)
        self._pending: dict[int, asyncio.Future] = {}
        self._guild_listeners: dict[int, "RustVoiceClient"] = {}
        self._lock = asyncio.Lock()
        self._proc = None

    # -- lifecycle ---------------------------------------------------------

    async def ensure_connected(self):
        async with self._lock:
            if self._ws is not None and not self._ws.closed:
                return
            await self._connect_with_spawn()

    async def _connect_with_spawn(self):
        try:
            await self._open_ws()
            return
        except (aiohttp.ClientError, OSError, asyncio.TimeoutError):
            logger.info("Audio node not reachable, attempting to start it...")

        self._spawn_node()
        last_error = None
        for attempt in range(10):
            await asyncio.sleep(0.5 * (attempt + 1))
            try:
                await self._open_ws()
                return
            except (aiohttp.ClientError, OSError, asyncio.TimeoutError) as e:
                last_error = e
        raise RuntimeError(
            f"Cannot reach the playify-rs audio node on {NODE_URL}. "
            f"Build it with `cargo build --release` in playify-rs/ or disable "
            f"PLAYIFY_RUST_NODE. Last error: {last_error}"
        )

    def _spawn_node(self):
        if self._proc is not None and self._proc.poll() is None:
            return  # already running under our supervision
        for candidate in _node_binary_candidates():
            if candidate.exists():
                logger.info(f"Starting audio node: {candidate}")
                kwargs = {}
                if _platform.system() == "Windows":
                    kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
                env = os.environ.copy()
                env["PLAYIFY_NODE_PORT"] = str(NODE_PORT)
                self._proc = subprocess.Popen(
                    [str(candidate)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    env=env,
                    **kwargs,
                )
                return
        logger.warning(
            "No playify-rs binary found (looked in bin/ and playify-rs/target/). "
            "Expecting an externally managed node."
        )

    async def _open_ws(self):
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        self._ws = await self._session.ws_connect(
            NODE_URL, heartbeat=20, timeout=aiohttp.ClientWSTimeout(ws_close=5)
        )
        if NODE_SECRET:
            request_id = next(self._request_id)
            await self._ws.send_json(
                {"op": "auth", "secret": NODE_SECRET, "request_id": request_id}
            )
            reply = await asyncio.wait_for(self._ws.receive_json(), timeout=5)
            if not reply.get("ok"):
                raise RuntimeError("Audio node authentication failed")
        if self._listen_task is None or self._listen_task.done():
            self._listen_task = asyncio.create_task(self._listen())
        logger.info(f"Connected to playify-rs audio node at {NODE_URL}")

    async def _listen(self):
        while True:
            ws = self._ws
            if ws is None:
                return
            try:
                msg = await ws.receive()
            except Exception as e:
                logger.error(f"Audio node listener error: {e}")
                msg = None

            if msg is None or msg.type in (
                aiohttp.WSMsgType.CLOSED,
                aiohttp.WSMsgType.CLOSE,
                aiohttp.WSMsgType.ERROR,
            ):
                logger.warning("Lost connection to the audio node, reconnecting...")
                for future in self._pending.values():
                    if not future.done():
                        future.set_exception(
                            ConnectionError("audio node connection lost")
                        )
                self._pending.clear()
                self._ws = None
                # Voice sessions survive node-side; retry in the background.
                for attempt in range(30):
                    await asyncio.sleep(min(2 * (attempt + 1), 15))
                    try:
                        async with self._lock:
                            if self._ws is None or self._ws.closed:
                                await self._connect_with_spawn()
                        return  # _open_ws started a fresh listener
                    except Exception:
                        continue
                logger.error("Could not reconnect to the audio node, giving up.")
                return

            if msg.type != aiohttp.WSMsgType.TEXT:
                continue
            try:
                data = json.loads(msg.data)
            except json.JSONDecodeError:
                continue

            if data.get("op") == "response":
                future = self._pending.pop(data.get("request_id"), None)
                if future is not None and not future.done():
                    future.set_result(data)
            elif data.get("op") == "event":
                guild_id = data.get("guild_id")
                listener = self._guild_listeners.get(guild_id)
                if listener is not None:
                    try:
                        listener._handle_node_event(data)
                    except Exception as e:
                        logger.error(f"[{guild_id}] Node event handler error: {e}")

    # -- API ---------------------------------------------------------------

    async def request(self, op: str, timeout: float = 20, **payload):
        await self.ensure_connected()
        request_id = next(self._request_id)
        future = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        try:
            await self._ws.send_json(
                {"op": op, "request_id": request_id, **payload}
            )
            reply = await asyncio.wait_for(future, timeout=timeout)
        finally:
            self._pending.pop(request_id, None)
        if not reply.get("ok"):
            raise RuntimeError(f"Audio node error ({op}): {reply.get('error')}")
        return reply.get("data") or {}

    def register(self, guild_id: int, vc: "RustVoiceClient"):
        self._guild_listeners[guild_id] = vc

    def unregister(self, guild_id: int, vc: "RustVoiceClient"):
        if self._guild_listeners.get(guild_id) is vc:
            self._guild_listeners.pop(guild_id, None)


node_client = NodeClient()


class _NodeVolumeSource(discord.PCMVolumeTransformer):
    """Duck-typed stand-in for PCMVolumeTransformer.

    Call sites do `isinstance(vc.source, discord.PCMVolumeTransformer)` and
    then assign `vc.source.volume`; this subclass forwards the assignment to
    the node instead of scaling PCM locally. It deliberately does not call
    super().__init__ (there is no local PCM stream to wrap) and has no
    `original` attribute, so safe_stop()'s unwrap loop passes through cleanly.
    """

    def __init__(self, vc: "RustVoiceClient", volume: float):
        self._vc = vc
        self._volume = volume

    @property
    def volume(self) -> float:
        return self._volume

    @volume.setter
    def volume(self, value: float):
        self._volume = max(0.0, value)
        self._vc._send_volume(self._volume)

    def read(self):  # pragma: no cover - never used, audio lives in the node
        return b""

    def cleanup(self):
        pass


class RustVoiceClient(discord.VoiceProtocol):
    """discord.VoiceProtocol backed by the playify-rs node.

    discord.py still performs the gateway voice handshake (we ask it to join
    the channel), but the resulting session_id/token/endpoint are forwarded
    to the Rust node, which establishes and owns the actual voice connection.
    """

    def __init__(self, client: discord.Client, channel: discord.abc.Connectable):
        super().__init__(client, channel)
        self.client = client
        self.channel = channel
        self.guild = channel.guild
        self._connected = False
        self._paused = False
        self._current_track_id = None
        self._after = None
        self._source = None
        self._session_id = None
        self._server_payload = None
        self._handshake = asyncio.Event()
        self._voice_state_received = False

    # -- gateway handshake (called by discord.py) --------------------------

    async def on_voice_state_update(self, data: dict):
        self._session_id = data.get("session_id")
        channel_id = data.get("channel_id")
        if channel_id is None:
            # External disconnect (kick, channel deleted, /disconnect).
            was_connected = self._connected
            self._connected = False
            if was_connected:
                asyncio.create_task(self._node_disconnect_quiet())
            self.cleanup()
            node_client.unregister(self.guild.id, self)
            return
        new_channel = self.guild.get_channel(int(channel_id))
        if new_channel is not None:
            self.channel = new_channel
        self._voice_state_received = True
        self._maybe_finish_handshake()

    async def on_voice_server_update(self, data: dict):
        self._server_payload = data
        self._maybe_finish_handshake()

    def _maybe_finish_handshake(self):
        if self._voice_state_received and self._server_payload is not None:
            self._handshake.set()

    async def connect(
        self,
        *,
        timeout: float = 30.0,
        reconnect: bool = True,
        self_deaf: bool = False,
        self_mute: bool = False,
    ):
        node_client.register(self.guild.id, self)
        await self.channel.guild.change_voice_state(
            channel=self.channel, self_deaf=self_deaf, self_mute=self_mute
        )
        try:
            await asyncio.wait_for(self._handshake.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            node_client.unregister(self.guild.id, self)
            self.cleanup()
            raise

        endpoint = self._server_payload.get("endpoint") or ""
        await node_client.request(
            "connect",
            guild_id=self.guild.id,
            user_id=self.client.user.id,
            session_id=self._session_id,
            token=self._server_payload.get("token"),
            endpoint=endpoint,
            channel_id=self.channel.id,
        )
        self._connected = True
        logger.info(f"[{self.guild.id}] Rust node voice connection established.")

    async def disconnect(self, *, force: bool = False):
        self._connected = False
        self._current_track_id = None
        self._source = None
        await self._node_disconnect_quiet()
        try:
            await self.channel.guild.change_voice_state(channel=None)
        except Exception:
            pass
        node_client.unregister(self.guild.id, self)
        self.cleanup()

    async def _node_disconnect_quiet(self):
        try:
            await node_client.request("disconnect", guild_id=self.guild.id, timeout=10)
        except Exception as e:
            logger.warning(f"[{self.guild.id}] Node disconnect failed: {e}")

    async def move_to(self, channel):
        await self.channel.guild.change_voice_state(channel=channel)
        # A VOICE_SERVER_UPDATE follows if the endpoint changes; songbird
        # keeps the existing session for same-endpoint moves. The new
        # credentials are re-forwarded by _handle_moved_endpoint below.
        self._handshake.clear()
        self._server_payload = None
        try:
            await asyncio.wait_for(self._handshake.wait(), timeout=10)
            await node_client.request(
                "connect",
                guild_id=self.guild.id,
                user_id=self.client.user.id,
                session_id=self._session_id,
                token=self._server_payload.get("token"),
                endpoint=self._server_payload.get("endpoint") or "",
                channel_id=self.channel.id if channel is None else channel.id,
            )
        except asyncio.TimeoutError:
            # Same-endpoint move: no new VOICE_SERVER_UPDATE is emitted.
            pass
        if channel is not None:
            self.channel = channel

    # -- VoiceClient-compatible surface ------------------------------------

    def is_connected(self) -> bool:
        return self._connected

    def is_playing(self) -> bool:
        return self._current_track_id is not None and not self._paused

    def is_paused(self) -> bool:
        return self._current_track_id is not None and self._paused

    @property
    def source(self):
        return self._source

    def play(self, source, after=None):
        """Compatibility shim: reject local PCM sources loudly.

        With the Rust node active, all playback must go through play_remote().
        Reaching this method means a call site was missed during integration.
        """
        raise RuntimeError(
            "RustVoiceClient.play() called with a local audio source. "
            "Use play_remote() or disable PLAYIFY_RUST_NODE."
        )

    async def play_remote(
        self,
        url: str,
        *,
        after=None,
        volume: float = 1.0,
        seek: float = 0.0,
        filters: str = "",
        is_local_file: bool = False,
        force_ffmpeg: bool = False,
    ):
        """Starts playback on the node, picking the cheapest capable source.

        Plain network streams and local files are decoded natively by the
        node (symphonia) with no FFmpeg process at all; FFmpeg is only used
        when a filter chain, a seek into a network stream, or an unusual
        container requires it.
        """
        needs_ffmpeg = bool(filters) or seek > 0 or force_ffmpeg
        if needs_ffmpeg:
            source_type = "ffmpeg"
        elif is_local_file:
            source_type = "file"
        else:
            source_type = "http"

        self._current_track_id = None  # stale events are ignored from here on
        self._after = after
        self._paused = False

        data = await node_client.request(
            "play",
            guild_id=self.guild.id,
            url=url,
            source_type=source_type,
            volume=volume,
            seek=seek,
            seek_pre_input=is_local_file,
            filters=filters or None,
            reconnect_flags=not is_local_file,
        )
        self._current_track_id = data.get("track_id")
        self._source = _NodeVolumeSource(self, volume)

    def stop(self):
        self._fire_and_forget("stop")
        # track_end from the node will fire the after callback, mirroring
        # discord.py where stop() leads to the player thread calling after.

    def pause(self):
        self._paused = True
        self._fire_and_forget("pause")

    def resume(self):
        self._paused = False
        self._fire_and_forget("resume")

    def _send_volume(self, volume: float):
        self._fire_and_forget("set_volume", volume=volume)

    def _fire_and_forget(self, op: str, **payload):
        async def _run():
            try:
                await node_client.request(op, guild_id=self.guild.id, **payload)
            except Exception as e:
                logger.warning(f"[{self.guild.id}] Node op '{op}' failed: {e}")

        asyncio.create_task(_run())

    # -- node events --------------------------------------------------------

    def _handle_node_event(self, data: dict):
        event = data.get("event")
        if event in ("track_end", "track_error"):
            track_id = data.get("track_id")
            if track_id != self._current_track_id or track_id is None:
                return  # stale event from a replaced track
            self._current_track_id = None
            self._paused = False
            after, self._after = self._after, None
            self._source = None
            if after is not None:
                error = (
                    RuntimeError(data.get("reason", "track error"))
                    if event == "track_error"
                    else None
                )
                try:
                    after(error)
                except Exception as e:
                    logger.error(f"[{self.guild.id}] after callback failed: {e}")
        elif event == "driver_disconnect":
            reason = data.get("reason", "")
            # Io = network loss; songbird retries by itself. Anything else
            # (requested/system) already flows through on_voice_state_update.
            logger.info(f"[{self.guild.id}] Node driver disconnect: {reason}")
        elif event == "driver_reconnect":
            logger.info(f"[{self.guild.id}] Node driver reconnected.")


async def connect_voice(channel, **kwargs):
    """Connects to a voice channel with the active backend.

    Drop-in replacement for `await channel.connect()` at every call site.
    """
    if USE_RUST_NODE:
        return await channel.connect(cls=RustVoiceClient, **kwargs)
    return await channel.connect(**kwargs)

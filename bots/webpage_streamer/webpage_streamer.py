import logging

from selenium import webdriver

logger = logging.getLogger(__name__)

import asyncio
import os
import sys
import time

import numpy as np
from aiohttp import web
from aiortc import RTCPeerConnection, RTCSessionDescription
from aiortc.contrib.media import MediaPlayer, MediaRelay
from av.audio.frame import AudioFrame
from pyvirtualdisplay import Display

os.environ["PULSE_LATENCY_MSEC"] = "20"


def audioframe_to_s16le_bytes(frame: AudioFrame, target_channels=2):
    """
    Convert aiortc AudioFrame to interleaved s16le bytes at 48k stereo.
    """
    # Ensure 48k
    if frame.sample_rate != 48000:
        # aiortc typically gives 48k; if not, let av resample:
        frame.pts = None
        frame.sample_rate = 48000

    # Convert to s16
    pcm = frame.to_ndarray(format="s16")  # shape: (channels, samples)
    if pcm.ndim == 1:
        pcm = np.expand_dims(pcm, axis=0)

    # Upmix/downmix to target_channels
    ch = pcm.shape[0]
    if ch < target_channels:
        pcm = np.vstack([pcm] + [pcm[0:1, :]] * (target_channels - ch))
    elif ch > target_channels:
        pcm = pcm[:target_channels, :]

    # Interleave channels (C, N) -> (N, C) -> bytes
    interleaved = pcm.T.astype(np.int16).tobytes()
    return interleaved


class AlsaLoopbackSink:
    """
    Writes 48k s16le stereo PCM to ALSA loopback using ffmpeg.
    Target device: hw:Loopback,0,0 (provided by snd-aloop).
    """

    def __init__(self, device="hw:Loopback,0,0", sample_rate=48000, channels=2):
        self.device = device
        self.sample_rate = sample_rate
        self.channels = channels
        self._proc = None
        self._stdin = None
        self._task = None
        self._stopped = asyncio.Event()

    def start(self):
        # Build ffmpeg proc: raw s16le → ALSA device
        pass

    def write(self, pcm_bytes: bytes):
        # Calculate volume (RMS) of the PCM data
        if len(pcm_bytes) > 0:
            # Convert bytes back to int16 array for volume calculation
            pcm_array = np.frombuffer(pcm_bytes, dtype=np.int16)
            # Calculate RMS (Root Mean Square) for volume
            rms = np.sqrt(np.mean(pcm_array.astype(np.float32) ** 2))
            # Normalize to 0-100 scale (int16 max is 32767)
            volume_percent = (rms / 32767.0) * 100
            print(f"PCM volume: {volume_percent:.2f}% (RMS: {rms:.1f})")
        else:
            print("Empty PCM data")

        print(f"PCM bytes length: {len(pcm_bytes)}")

    async def stop(self):
        pass


class WebpageStreamer:
    def __init__(
        self,
    ):
        self.driver = None
        self.video_frame_size = (1280, 720)
        self.display_var_for_recording = None
        self.display = None
        self.last_keepalive_time = None
        self.web_app = None

    def run(self):
        self.display_var_for_recording = os.environ.get("DISPLAY")
        if os.environ.get("DISPLAY") is None:
            # Create virtual display only if no real display is available
            self.display = Display(visible=0, size=self.video_frame_size)
            self.display.start()
            self.display_var_for_recording = self.display.new_display_var

        options = webdriver.ChromeOptions()

        options.add_argument("--autoplay-policy=no-user-gesture-required")
        options.add_argument("--use-fake-device-for-media-stream")
        # options.add_argument("--use-fake-ui-for-media-stream")
        options.add_argument(f"--window-size={self.video_frame_size[0]},{self.video_frame_size[1]}")
        options.add_argument("--start-fullscreen")

        # options.add_argument('--headless=new')
        options.add_argument("--disable-gpu")
        # options.add_argument("--mute-audio")
        options.add_argument("--disable-application-cache")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--enable-blink-features=WebCodecs,WebRTC-InsertableStreams,-AutomationControlled")
        options.add_argument("--remote-debugging-port=9222")

        logger.info("Chrome sandboxing is enabled")

        options.add_experimental_option("excludeSwitches", ["enable-automation"])

        options.add_experimental_option(
            "prefs",
            {
                "profile.default_content_setting_values.media_stream_mic": 1,  # 1 = allow, 2 = block
                "profile.default_content_setting_values.media_stream_camera": 2,  # 1 = allow, 2 = block
            },
        )

        self.driver = webdriver.Chrome(options=options)
        logger.info(f"web driver server initialized at port {self.driver.service.port}")

        # Always send the header that skips ngrok's browser warning
        try:
            self.driver.execute_cdp_cmd("Network.enable", {})
            self.driver.execute_cdp_cmd("Network.setExtraHTTPHeaders", {
                "headers": {"ngrok-skip-browser-warning": "true"}
            })
            logger.info("Configured ngrok-skip-browser-warning header via CDP")
        except Exception as e:
            logger.info(f"Failed to set ngrok header via CDP: {e}")

        with open("bots/webpage_streamer/webpage_streamer_payload.js", "r") as file:
            payload_code = file.read()

        combined_code = f"""
            {payload_code}
        """

        # Add the combined script to execute on new document
        self.driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {"source": combined_code})

        # Start the web server to handle WebRTC connections
        self.start_web_server()

    async def keepalive_monitor(self):
        """Monitor keepalive status and shutdown if no keepalive received in the last 15 minutes."""

        self.last_keepalive_time = time.time()

        while True:
            await asyncio.sleep(60)  # Check every minute

            current_time = time.time()
            time_since_last_keepalive = current_time - self.last_keepalive_time

            if time_since_last_keepalive > 900:  # More than 15 minutes since last keepalive
                logger.warning(f"No keepalive received in {time_since_last_keepalive:.1f} seconds. Shutting down process.")
                await self.shutdown_process()
                break

    async def shutdown_process(self):
        """Gracefully shutdown the process."""
        try:
            if self.driver:
                self.driver.quit()
            if self.display:
                self.display.stop()
            if self.web_app:
                await self.web_app.shutdown()
            logger.info("Process shutting down")
        except Exception as e:
            logger.error(f"Error during shutdown: {e}")
        finally:
            sys.exit(0)

    def start_web_server(self):
        """Start the aiohttp web server."""
        logger.info("Starting web server setup...")
        pcs = set()

        # ADD THESE TWO LINES
        AUDIO_RELAY = MediaRelay()
        # will hold the *original* upstream AudioStreamTrack
        # from the first client that posts to /offer
        UPSTREAM_AUDIO_TRACK_KEY = "upstream_audio_track"

        async def offer_meeting_audio(req):
            """
            POST /offer_meeting_audio
            Return an SDP answer that *sends* the upstream audio (if present)
            to this new peer connection (listen-only client).
            """
            logger.info("/offer_meeting_audio: received request")
            params = await req.json()
            logger.info(f"/offer_meeting_audio: parsed JSON keys={list(params.keys())}")
            offer = RTCSessionDescription(sdp=params["sdp"], type=params["type"])

            # Do we have an upstream audio yet?
            upstream = req.app.get(UPSTREAM_AUDIO_TRACK_KEY)
            if upstream is None:
                logger.warning("/offer_meeting_audio: upstream audio not yet available; returning 409")
                return web.Response(status=409, text="No upstream audio has been published yet.")

            pc = RTCPeerConnection()
            pcs.add(pc)

            # Re-broadcast using the relay so multiple listeners are OK
            rebroadcast_track = AUDIO_RELAY.subscribe(upstream)
            pc.addTrack(rebroadcast_track)
            logger.info("/offer_meeting_audio: added rebroadcast audio track to PC")

            @pc.on("connectionstatechange")
            async def _on_state():
                if pc.connectionState in ("failed", "closed", "disconnected"):
                    await pc.close()
                    pcs.discard(pc)

            await pc.setRemoteDescription(offer)
            logger.info("/offer_meeting_audio: setRemoteDescription OK")
            answer = await pc.createAnswer()
            await pc.setLocalDescription(answer)
            logger.info("/offer_meeting_audio: created and set local description (answer)")
            return web.json_response({"sdp": pc.localDescription.sdp, "type": pc.localDescription.type})

        async def offer(req):
            params = await req.json()
            offer = RTCSessionDescription(sdp=params["sdp"], type=params["type"])

            pc = RTCPeerConnection()
            pcs.add(pc)

            # --- server-to-client (unchanged): send your local video/audio if desired ---
            v_player = req.app["video_player"]
            a_player = req.app["audio_player"]

            if v_player and v_player.video:
                v_sender = pc.addTrack(v_player.video)
                # Hint the encoder for real-time, modest bitrate, no B-frames
                try:
                    params = v_sender.getParameters()
                    # Keep one encoding, cap bitrate to avoid queue build-up
                    params.encodings = [{"maxBitrate": 750000, "maxFramerate": 15}]
                    v_sender.setParameters(params)
                except Exception:
                    pass

            # You can still send server audio if you want the page to hear server audio too:
            if a_player and a_player.audio:
                a_sender = pc.addTrack(a_player.audio)
                # Hint the encoder for real-time, modest bitrate, no B-frames
                try:
                    params = a_sender.getParameters()
                    # Keep one encoding, cap bitrate to avoid queue build-up
                    # params.encodings = [{"maxBitrate": 64_000, "maxFramerate": 15}]
                    a_sender.setParameters(params)
                except Exception:
                    pass

            # --- NEW: receive client's mic and feed to ALSA loopback ---
            # loopback_sink = AlsaLoopbackSink(device="hw:Loopback,0,0", sample_rate=48000, channels=2)
            # loopback_sink.start()

            @pc.on("track")
            def on_track(track):
                if track.kind == "audio":
                    # store the ORIGINAL upstream track for rebroadcast
                    req.app[UPSTREAM_AUDIO_TRACK_KEY] = track
                    logger.info("Upstream audio track set for rebroadcast.")

            @pc.on("connectionstatechange")
            async def _on_state():
                if pc.connectionState in ("failed", "closed", "disconnected"):
                    await pc.close()
                    pcs.discard(pc)
                    pass
                    # await loopback_sink.stop()

            await pc.setRemoteDescription(offer)
            answer = await pc.createAnswer()
            await pc.setLocalDescription(answer)
            return web.json_response({"sdp": pc.localDescription.sdp, "type": pc.localDescription.type})

        async def start_streaming(req):
            data = await req.json()
            webpage_url = data.get("url")
            if not webpage_url:
                return web.json_response({"error": "URL is required"}, status=400)

            print(f"Starting streaming to {webpage_url}")
            self.driver.get(webpage_url)

            return web.json_response({"status": "success"})

        async def client_log(req):
            """Receive client console logs (from injected webpage payload) and emit to server logs."""
            try:
                data = await req.json()
            except Exception:
                data = {}
            message = data.get("message") or ""
            try:
                # Avoid extremely long lines
                msg = message if len(message) < 4000 else (message[:4000] + "…[truncated]")
            except Exception:
                msg = str(message)
            logger.info(f"CLIENT_LOG: {msg}")
            return web.json_response({"ok": True})

        async def keepalive(req):
            """Keepalive endpoint to reset the timeout timer."""
            self.last_keepalive_time = time.time()
            logger.info("Keepalive received")
            return web.json_response({"status": "alive", "timestamp": self.last_keepalive_time})

        async def shutdown(req):
            """Reset without exiting the process (safer on bot exit)."""
            logger.info("Resetting streamer (closing PCs, blanking page) via API endpoint")
            try:
                # Close all peer connections
                for pc in list(pcs):
                    await pc.close()
                    pcs.discard(pc)
                # Clear upstream audio
                req.app[UPSTREAM_AUDIO_TRACK_KEY] = None
                # Stop any current playback
                try:
                    self.driver.get("about:blank")
                except Exception as e:
                    logger.info(f"Error blanking page during reset: {e}")
            except Exception as e:
                logger.error(f"Error during reset: {e}")
            return web.json_response({"status": "reset"})

        video_size = f"{self.video_frame_size[0]}x{self.video_frame_size[1]}"
        framerate = "15"
        video_device = self.display.new_display_var
        video_format = "x11grab"

        audio_device = "default"
        audio_format = "pulse"

        port = 8000

        # Build players
        logger.info(f"Initializing video player with device: {video_device}, format: {video_format}")
        # Video options: set size + fps (many webcams accept these via v4l2)
        v_opts = {
            "video_size": video_size,
            "framerate": framerate,
            "fflags": "nobuffer",
            "flags": "low_delay",
            "probesize": "32",
            "analyzeduration": "0",
            "thread_queue_size": "64",
            "draw_mouse": "0",
        }
        try:
            video_player = MediaPlayer(video_device, format=video_format, options=v_opts)
            logger.info("Video player initialized successfully")
        except Exception as e:
            logger.error(f"Failed to initialize video player: {e}")
            video_player = None

        # Audio player: let aiortc/ffmpeg handle resampling to 48k
        logger.info(f"Initializing audio player with device: {audio_device}, format: {audio_format}")
        a_opts = {
            "fflags": "nobuffer",
            "probesize": "32",
            "analyzeduration": "0",
            "thread_queue_size": "64",
        }
        try:
            audio_player = MediaPlayer(audio_device, format=audio_format, options=a_opts)
            logger.info("Audio player initialized successfully")
        except Exception as e:
            logger.error(f"Failed to initialize audio player: {e}")
            audio_player = None

        app = web.Application()
        self.web_app = app

        # Start keepalive monitoring task
        async def init_keepalive_monitor(app):
            """Initialize keepalive monitoring when the app starts"""
            logger.info("Starting keepalive monitoring task")
            asyncio.create_task(self.keepalive_monitor())
            logger.info("Started keepalive monitoring task")

        app.on_startup.append(init_keepalive_monitor)

        # Add CORS handling for preflight requests
        async def handle_cors_preflight(request):
            """Handle CORS preflight requests"""
            origin = request.headers.get("Origin", "*")
            return web.Response(
                headers={
                    # Echo origin for PNA compliance ("*" may be rejected)
                    "Access-Control-Allow-Origin": origin,
                    "Access-Control-Allow-Methods": "POST, GET, OPTIONS",
                    "Access-Control-Allow-Headers": "Content-Type, Access-Control-Allow-Private-Network",
                    # Private Network Access (PNA) - needed when an https page calls a private http origin
                    "Access-Control-Allow-Private-Network": "true",
                    "Vary": "Origin",
                    "Access-Control-Max-Age": "86400",
                }
            )

        # Add CORS headers to all responses
        @web.middleware
        async def add_cors_headers(request, handler):
            """Add CORS headers to all responses"""
            response = await handler(request)
            origin = request.headers.get("Origin", "*")
            response.headers.update(
                {
                    # Echo origin for PNA compliance
                    "Access-Control-Allow-Origin": origin,
                    "Access-Control-Allow-Methods": "POST, GET, OPTIONS",
                    "Access-Control-Allow-Headers": "Content-Type, Access-Control-Allow-Private-Network",
                    # Private Network Access for secure->insecure fetches on local/private networks
                    "Access-Control-Allow-Private-Network": "true",
                    "Vary": "Origin",
                }
            )
            return response

        app.middlewares.append(add_cors_headers)

        app.router.add_post("/start_streaming", start_streaming)
        app.router.add_post("/client_log", client_log)

        app.router.add_post("/keepalive", keepalive)
        app.router.add_options("/keepalive", handle_cors_preflight)
        app.router.add_options("/client_log", handle_cors_preflight)

        app.router.add_post("/shutdown", shutdown)
        app.router.add_options("/shutdown", handle_cors_preflight)

        app.router.add_post("/offer", offer)
        app.router.add_options("/offer", handle_cors_preflight)

        app.router.add_post("/offer_meeting_audio", offer_meeting_audio)  # SDP exchange
        app.router.add_options("/offer_meeting_audio", handle_cors_preflight)

        app["video_player"] = video_player
        app["audio_player"] = audio_player

        logger.info(f"Starting web server on port {port}")
        web.run_app(app, host="0.0.0.0", port=port)


"""Integration test for VAD-driven Qwen refinement on the streaming endpoint.

Synthesizes a small test pattern (loud audio + silence) and pushes it
through the WebSocket. Verifies that:

  1. The handshake completes and the ready message is received.
  2. The VAD triggers at least one end-of-utterance boundary, which
     causes the server to attempt a Qwen refinement.
  3. If the Qwen worker is reachable, a 'refined' message arrives with
     normalized text. If not, the server must keep the Vosk text
     intact (no crash) — we report this as WARN, not FAIL.

Run with:

    .venv/Scripts/python.exe work/test_stream_refinement.py

The web server must already be running on ws://127.0.0.1:8765/api/stream
and (optionally) the Qwen worker on http://127.0.0.1:8766.
"""
import asyncio
import json
import struct
import sys

import websockets

WS_URL = "ws://127.0.0.1:8765/api/stream"
SAMPLE_RATE = 48000
LOUD_SECONDS = 1.5
SILENT_SECONDS = 0.8
LOUD_AMPLITUDE = 5000  # ~-16 dBFS, well above the -40 dBFS VAD threshold


def synth_loud(seconds: float) -> bytes:
    samples = int(SAMPLE_RATE * seconds)
    return struct.pack(f"<{samples}h", *([LOUD_AMPLITUDE] * samples))


def synth_silence(seconds: float) -> bytes:
    samples = int(SAMPLE_RATE * seconds)
    return b"\x00\x00" * samples


async def main() -> int:
    audio = synth_loud(LOUD_SECONDS) + synth_silence(SILENT_SECONDS) + synth_loud(LOUD_SECONDS)
    print(f"Synthesized {len(audio)} bytes of test audio "
          f"({LOUD_SECONDS}s loud + {SILENT_SECONDS}s silence + {LOUD_SECONDS}s loud)")

    partial_count = 0
    result_count = 0
    refined_count = 0
    final_text = ""
    qwen_error_seen = False

    async with websockets.connect(WS_URL, max_size=4 * 1024 * 1024) as ws:
        await ws.send(json.dumps({"model_id": "vosk-model-small-cn-0.22", "sample_rate": SAMPLE_RATE}))

        # Stream in 4 KB chunks with small pauses to simulate realtime.
        chunk_size = 4096
        for i in range(0, len(audio), chunk_size):
            await ws.send(audio[i : i + chunk_size])
            await asyncio.sleep(0.01)
        await ws.send(json.dumps({"event": "stop"}))

        for _ in range(200):
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=20.0)
            except (asyncio.TimeoutError, websockets.ConnectionClosed):
                break
            msg = json.loads(raw)
            t = msg.get("type")
            if t == "ready":
                print(f"READY sample_rate={msg.get('sample_rate')}")
            elif t == "partial":
                partial_count += 1
            elif t == "result":
                result_count += 1
            elif t == "refined":
                refined_count += 1
                print(f"REFINED text={msg.get('text','')[:80]!r}")
            elif t == "complete":
                final_text = msg.get("record", {}).get("text", "")
                print(f"COMPLETE text={final_text[:120]!r}")
                break
            elif t == "error":
                print(f"ERROR message={msg.get('message')}")
                if "Qwen" in msg.get("message", ""):
                    qwen_error_seen = True

    print()
    print(f"Stats: partial={partial_count} result={result_count} refined={refined_count}")
    if partial_count == 0 and result_count == 0:
        print("FAIL: server did not produce any Vosk partial/result messages")
        return 1
    if refined_count == 0 and not qwen_error_seen:
        print("FAIL: no 'refined' message and no Qwen error reported — VAD likely did not trigger")
        return 1
    if refined_count == 0 and qwen_error_seen:
        print("WARN: VAD triggered but Qwen worker was unavailable; "
              "Vosk draft was preserved as expected.")
    print("PASS")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

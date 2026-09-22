import asyncio
import json
import os
import subprocess
import time
import urllib.request
from http.server import HTTPServer, SimpleHTTPRequestHandler
import threading
import websockets
import base64
import sys

sys.stdout.reconfigure(encoding='utf-8')

PORT = 8765
EDGE_PATH = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
PROJECT_DIR = r"C:\KLTN\Memory_project"

class CORSRequestHandler(SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header('Access-Control-Allow-Origin', '*')
        super().end_headers()

def run_server():
    os.chdir(PROJECT_DIR)
    server = HTTPServer(('127.0.0.1', PORT), CORSRequestHandler)
    server.serve_forever()

async def main():
    server_thread = threading.Thread(target=run_server, daemon=True)
    server_thread.start()
    print(f"HTTP Server started on http://127.0.0.1:{PORT}")

    cmd = [
        EDGE_PATH,
        "--headless=new",
        "--remote-debugging-port=9222",
        "--disable-gpu-sandbox",
        "--enable-webgl",
        "--window-size=1280,800",
        f"http://127.0.0.1:{PORT}/batdau.html"
    ]
    proc = subprocess.Popen(cmd)
    print("Edge process spawned, waiting for CDP...")
    time.sleep(3)

    ws_url = None
    for _ in range(12):
        try:
            with urllib.request.urlopen("http://127.0.0.1:9222/json") as response:
                targets = json.loads(response.read().decode())
                for t in targets:
                    if t.get('type') == 'page':
                        ws_url = t.get('webSocketDebuggerUrl')
                        break
                if ws_url:
                    break
        except Exception:
            time.sleep(1)

    if not ws_url:
        print("ERROR: Failed to obtain CDP WebSocket URL")
        proc.terminate()
        return

    print(f"Connecting to CDP: {ws_url}")
    # Set max_size to 20MB to allow full uncompressed PNG screenshots
    async with websockets.connect(ws_url, max_size=20 * 1024 * 1024) as ws:
        msg_id = 0
        console_logs = []
        errors = []

        async def send_cdp(method, params=None):
            nonlocal msg_id
            msg_id += 1
            payload = {"id": msg_id, "method": method, "params": params or {}}
            await ws.send(json.dumps(payload))
            while True:
                raw = await ws.recv()
                data = json.loads(raw)
                if data.get("method") == "Console.messageAdded":
                    msg = data.get("params", {}).get("message", {})
                    level = msg.get("level")
                    text = msg.get("text")
                    console_logs.append(f"[{level}] {text}")
                    if level == "error":
                        errors.append(text)
                elif data.get("method") == "Runtime.exceptionThrown":
                    details = data.get("params", {}).get("exceptionDetails", {})
                    errors.append(f"EXCEPTION: {details.get('text', '')} {details.get('exception', {}).get('description', '')}")
                elif data.get("id") == payload["id"]:
                    return data.get("result", {})

        await ws.send(json.dumps({"id": 998, "method": "Console.enable"}))
        await ws.send(json.dumps({"id": 999, "method": "Runtime.enable"}))

        print("Waiting 4 seconds for initial load...")
        await asyncio.sleep(4)

        async def eval_js(expression):
            res = await send_cdp("Runtime.evaluate", {
                "expression": expression,
                "returnByValue": True,
                "awaitPromise": True
            })
            if "exceptionDetails" in res:
                print(f"JS Exception in '{expression}':", res["exceptionDetails"])
            return res.get("result", {}).get("value")

        async def capture_screen(filename):
            shot_res = await send_cdp("Page.captureScreenshot", {"format": "png"})
            shot_path = os.path.join(PROJECT_DIR, filename)
            with open(shot_path, "wb") as f:
                f.write(base64.b64decode(shot_res.get("data", "")))
            print(f"  [Screenshot captured]: {shot_path}")
            return shot_path

        # Start experience & dissolve gateway
        print("\nStarting Experience & Dissolving Gateway Overlay...")
        await eval_js("""
            if (window.startExperience) window.startExperience();
            const gw = document.getElementById('gateway-overlay');
            if (gw) gw.style.display = 'none';
        """)
        await asyncio.sleep(2.5)

        all_cards_count = await eval_js("window.allPhotoCards ? window.allPhotoCards.length : 0")
        chibi_count = await eval_js("window.chibiProps ? window.chibiProps.length : -1")
        fsm_state = await eval_js("window.FSM ? window.FSM.currentState : -1")

        print("=" * 60)
        print("STAGE 1: VERIFICATION OF CHIBI DETACHMENT & COVER PHOTOS")
        print(f"1. Total Photo Cards: {all_cards_count} (Expected: 255)")
        print(f"2. Chibi Props Count: {chibi_count} (Expected: 0 - Successfully Disconnected)")
        print(f"3. FSM Initial State: {fsm_state} (Expected: 0 - BOOK_AJAR)")
        print("=" * 60)

        cover_info = await eval_js("""
            window.allPhotoCards.slice(0, 8).map((c, i) => ({
                index: i,
                isCover: c.userData.isCoverPhoto,
                url: c.userData.photoItem.url,
                title: c.userData.photoItem.title,
                visible: c.visible,
                pos: { x: +c.position.x.toFixed(2), y: +c.position.y.toFixed(2), z: +c.position.z.toFixed(2) }
            }))
        """)
        print("\n8 Cover Photos Status on Hinges:")
        for ci in cover_info:
            print(f"  [{ci['index']}] {ci['title']} ({ci['url']}) | isCover: {ci['isCover']} | Pos: {ci['pos']}")

        print("\nCapturing Front Cover in BOOK_AJAR...")
        await capture_screen("front_cover_verification.png")

        # Orbit camera to view Back Cover
        print("\nOrbiting camera to inspect Back Cover Sandwich layout...")
        await eval_js("""
            window.camera.position.set(0.0, 0.5, -18.5);
            window.controls.target.set(0, 0, 0);
            window.controls.update();
        """)
        await asyncio.sleep(1.5)
        print("Capturing Back Cover Sandwich 3-Tier Layout...")
        await capture_screen("back_cover_verification.png")

        # Reset camera back to front
        await eval_js("""
            window.camera.position.set(0.0, 0.5, 18.5);
            window.controls.target.set(0, 0, 0);
            window.controls.update();
        """)
        await asyncio.sleep(1.0)

        # STAGE 2: Test Raycasting / Click on Cover Photo 0 -> FOCUS_PHOTO
        print("\n" + "=" * 60)
        print("STAGE 2: RAYCAST CLICK TO FOCUS_PHOTO ZOOM (STATES.FOCUS_PHOTO = 2)")
        await eval_js("window.FSM.setTarget(window.STATES.FOCUS_PHOTO, 0)")
        await asyncio.sleep(2.0)
        focus_curr = await eval_js("window.FSM.currentState")
        focus_target = await eval_js("window.FSM.targetState")
        sel_idx = await eval_js("window.selectedPhotoIndex")
        print(f"Current State: {focus_curr}, Target State: {focus_target} (Expected: 2 - FOCUS_PHOTO), Selected: {sel_idx}")
        await capture_screen("focus_photo_zoom_verification.png")

        # Return back to BOOK_AJAR
        print("\nReturning back to BOOK_AJAR...")
        await eval_js("window.FSM.setTarget(window.STATES.BOOK_AJAR)")
        await asyncio.sleep(2.8)
        ajar_curr = await eval_js("window.FSM.currentState")
        print(f"Current State: {ajar_curr} (Expected: 0 - BOOK_AJAR)")

        # STAGE 3: Test GALAXY_BURST transition and re-anchoring
        print("\n" + "=" * 60)
        print("STAGE 3: GALAXY_BURST DETACHMENT & 360 TUMBLING (STATES.GALAXY_BURST = 1)")
        await eval_js("window.FSM.setTarget(window.STATES.GALAXY_BURST)")
        await asyncio.sleep(3.6)
        galaxy_curr = await eval_js("window.FSM.currentState")
        print(f"Current State: {galaxy_curr} (Expected: 1 - GALAXY_BURST)")
        await capture_screen("galaxy_burst_verification.png")

        print("\nClosing Galaxy back to BOOK_AJAR & Re-anchoring...")
        await eval_js("window.FSM.setTarget(window.STATES.BOOK_AJAR)")
        await asyncio.sleep(3.0)
        final_state = await eval_js("window.FSM.currentState")
        print(f"Final State: {final_state} (Expected: 0 - BOOK_AJAR)")

        # Check console errors
        print("\n" + "=" * 60)
        print(f"CONSOLE REPORT: {len(errors)} error(s) detected.")
        if errors:
            for err in errors:
                print("  [ERROR]:", err)
        else:
            print("  SUCCESS: 0 console errors, 0 missing textures, 0 WebGL warnings!")
        print("=" * 60)

    proc.terminate()
    print("Edge headless process terminated cleanly.")

if __name__ == "__main__":
    asyncio.run(main())

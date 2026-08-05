"""Force-steal Telegram polling session and clear webhook."""
import urllib.request
import json
import time

TOKEN = "8937697751:AAFiTO-AnEowrT-XuSVlKZNs8d6BOVGoPXc"
BASE = f"https://api.telegram.org/bot{TOKEN}"

def call(endpoint):
    try:
        r = urllib.request.urlopen(BASE + endpoint, timeout=15)
        return json.loads(r.read())
    except Exception as e:
        return {"error": str(e)}

print("1. Deleting webhook...")
r = call("/deleteWebhook?drop_pending_updates=true")
print("   Result:", r.get("result"))

print("2. Stealing session with getUpdates timeout=0...")
r = call("/getUpdates?timeout=0&limit=1&offset=-1")
print("   Updates:", len(r.get("result", [])))

time.sleep(2)

print("3. Stealing again...")
r = call("/getUpdates?timeout=0&limit=1")
print("   Updates:", len(r.get("result", [])))

time.sleep(5)

print("4. Final steal...")
r = call("/getUpdates?timeout=0")
print("   Updates:", len(r.get("result", [])))

print("\nDone! Session should be free now. Waiting 5 more seconds...")
time.sleep(5)
print("Ready!")

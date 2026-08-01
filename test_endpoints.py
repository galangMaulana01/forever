"""Smoke test semua endpoint Master Control API.

Cara pakai:
    python3 test_endpoints.py
    python3 test_endpoints.py https://forever-eta-steel.vercel.app
    python3 test_endpoints.py http://localhost:8000

Hanya pakai stdlib, tidak perlu install apa pun.
Setiap request dijeda 5 detik supaya tidak kena rate limit vendor.
"""

import json
import sys
import time
import urllib.error
import urllib.request

BASE_URL = (sys.argv[1] if len(sys.argv) > 1 else "https://forever-eta-steel.vercel.app").rstrip("/")
SLEEP_SECONDS = 5
MAX_BODY_CHARS = 700

results = []


def request(method, path, body=None):
    url = BASE_URL + path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=60) as response:
            return response.status, response.read().decode()
    except urllib.error.HTTPError as error:
        return error.code, error.read().decode()
    except Exception as error:  # noqa: BLE001 - report any transport failure as-is
        return None, f"REQUEST FAILED: {type(error).__name__}: {error}"


def step(title, method, path, body=None, expect=200):
    print(f"\n{'=' * 70}\n{title}\n{method} {path}")
    if body is not None:
        print(f"body: {json.dumps(body)}")
    status, text = request(method, path, body)
    verdict = "PASS" if status == expect else "FAIL"
    results.append((verdict, title, status))
    print(f"-> HTTP {status}  [{verdict}, expected {expect}]")
    shown = text if len(text) <= MAX_BODY_CHARS else text[:MAX_BODY_CHARS] + f"... (dipotong, total {len(text)} chars)"
    print(shown)
    time.sleep(SLEEP_SECONDS)
    try:
        return status, json.loads(text)
    except json.JSONDecodeError:
        return status, None


def main():
    user_code = f"qa_{int(time.time())}"
    print(f"BASE_URL   : {BASE_URL}")
    print(f"user uji   : {user_code}")
    print(f"jeda       : {SLEEP_SECONDS} detik per request")

    # --- USER ---
    step("1. List user (sebelum dibuat)", "GET", "/users")
    step("2. Create user", "POST", "/users", {"user_code": user_code})
    step("3. Create user lagi (harus tetap OK, DUPLICATE_USER diabaikan)", "POST", "/users", {"user_code": user_code})
    step("4. Deposit 10000", "POST", f"/users/{user_code}/deposit", {"amount": 10000})
    step("5. Withdraw 4000", "POST", f"/users/{user_code}/withdraw", {"amount": 4000})
    step("6. Withdraw kelebihan (harus GAGAL, saldo kurang)", "POST", f"/users/{user_code}/withdraw", {"amount": 999999999}, expect=400)
    step("7. Deposit tanpa amount (harus GAGAL, validasi)", "POST", f"/users/{user_code}/deposit", {}, expect=400)

    _, users = step("8. List user (cek total_balance harus 6000)", "GET", "/users")
    if isinstance(users, list):
        found = next((u for u in users if u.get("user_code") == user_code), None)
        print(f"   -> {user_code} total_balance = {found.get('total_balance') if found else 'TIDAK DITEMUKAN'} (harusnya 6000)")

    # --- GAME LIST ---
    _, vendors = step("9. Get vendor list", "GET", "/vendors")
    vendor_code = ""
    if isinstance(vendors, dict) and vendors.get("vendors"):
        vendor_code = vendors["vendors"][0].get("vendorCode", "")
        print(f"   -> pakai vendor pertama: {vendor_code}")

    if vendor_code:
        _, games = step(f"10. Get game list vendor '{vendor_code}'", "GET", f"/games?vendor={vendor_code}")
        if isinstance(games, dict) and games.get("vendorGames"):
            sample = games["vendorGames"][0]
            print("   -> contoh 1 game (cek gameName/imageUrl formatnya JSON string per-bahasa atau bukan):")
            print(f"      {json.dumps(sample)[:400]}")
    else:
        print("\n(lewati test game list: tidak ada vendorCode)")

    # --- CASINO PROXY ---
    step("11. Casino proxy - GetAgentInfo", "POST", "/casino", {"method": "GetAgentInfo"})

    # --- LAUNCH HISTORY ---
    created = step(
        "12. Simpan launch history",
        "POST",
        "/launch-history",
        {
            "user_code": user_code,
            "vendor_code": vendor_code or "slot-pragmatic",
            "game_code": "qa-test-game",
            "response": {"status": 0, "msg": "dummy dari test script"},
        },
    )[1]

    step("13. List launch history", "GET", "/launch-history")

    history_id = created.get("_id") if isinstance(created, dict) else None
    if history_id:
        step(f"14. Hapus launch history {history_id}", "DELETE", f"/launch-history/{history_id}")
        step("15. Hapus lagi id yang sama (harus GAGAL 404)", "DELETE", f"/launch-history/{history_id}", expect=404)
    else:
        print("\n(lewati test delete: tidak dapat _id dari langkah 12)")

    step("16. Hapus id ngawur (harus GAGAL 400)", "DELETE", "/launch-history/bukan-objectid", expect=400)

    # --- RINGKASAN ---
    print(f"\n{'=' * 70}\nRINGKASAN")
    for verdict, title, status in results:
        print(f"  [{verdict}] HTTP {status} - {title}")
    passed = sum(1 for verdict, _, _ in results if verdict == "PASS")
    print(f"\n{passed}/{len(results)} sesuai harapan")


if __name__ == "__main__":
    main()

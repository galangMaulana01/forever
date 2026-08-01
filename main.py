import os
from datetime import datetime, timezone

import httpx
from dotenv import load_dotenv
from fastapi import Body, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pymongo import MongoClient

load_dotenv()

CASINO_BASE_URL = "https://api.aicvgdbi.win/api/casinoapi"
CASINO_TOKEN = os.environ["CASINO_TOKEN"]
CASINO_AGENT = os.environ["CASINO_AGENT"]
CURRENCY_CODE = "IDR"

# Method casino yang mengubah state (bukan sekadar baca) - ini yang dicatat ke
# activity log lewat /casino proxy. Method read-only (GetVendors, ReportByDate
# polling tiap 5 detik, dst) sengaja tidak dicatat supaya log tidak penuh noise.
MUTATING_CASINO_METHODS = {"ApplyFreeRound", "CancelFreeRound"}

mongo_client = MongoClient(os.environ["DATABASE_URL"])
db = mongo_client.get_default_database()

app = FastAPI(title="Master Control API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def serialize(document):
    document["_id"] = str(document["_id"])
    return document


def log_activity(action: str, detail: dict, result: dict = None):
    db.activity_log.insert_one({
        "action": action,
        "detail": detail,
        "result": result,
        "created_at": datetime.now(timezone.utc).isoformat(),
    })


def call_casino(method: str, params: dict):
    body = {"method": method, "token": CASINO_TOKEN, "agentCode": CASINO_AGENT, **params}
    body = {key: value for key, value in body.items() if value not in ("", None)}
    try:
        response = httpx.post(CASINO_BASE_URL, json=body, timeout=15)
        response.raise_for_status()
    except httpx.HTTPError as error:
        raise HTTPException(status_code=502, detail=f"Casino API error: {error}") from error
    return response.json()


@app.post("/users")
def create_user(payload: dict = Body(...)):
    user_code = payload.get("user_code")
    if not user_code:
        raise HTTPException(status_code=400, detail="user_code is required")
    data = call_casino("CreateUser", {"userCode": user_code})
    log_activity("CreateUser", {"userCode": user_code}, data)
    # A user that already exists upstream is fine here - we just want to make
    # sure the vendor knows about them. Vendor returns "Usercode already exist."
    # or the documented DUPLICATE_USER (status 7); accept both.
    status = data.get("status")
    msg = str(data.get("msg", ""))
    already_exists = "already exist" in msg.lower() or "duplicate" in msg.lower()
    if status not in (0, 7) and not already_exists:
        raise HTTPException(status_code=400, detail=msg or "CreateUser failed")
    existing = db.users.find_one({"user_code": user_code})
    if existing:
        return serialize(existing)
    result = db.users.insert_one({"user_code": user_code})
    return serialize(db.users.find_one({"_id": result.inserted_id}))


@app.get("/users")
def list_users():
    users = [serialize(user) for user in db.users.find()]
    try:
        info = call_casino("GetUserInfo", {})
        balances_by_code = {u.get("userCode"): u.get("balances", {}) for u in info.get("users", [])}
    except HTTPException:
        balances_by_code = {}
    for user in users:
        user["total_balance"] = balances_by_code.get(user["user_code"], {}).get(CURRENCY_CODE, 0)
    return users


@app.post("/users/{user_code}/deposit")
def deposit_user(user_code: str, payload: dict = Body(...)):
    amount = payload.get("amount")
    if amount is None:
        raise HTTPException(status_code=400, detail="amount is required")
    data = call_casino("Deposit", {"userCode": user_code, "currencyCode": CURRENCY_CODE, "amount": amount})
    log_activity("Deposit", {"userCode": user_code, "amount": amount}, data)
    if data.get("status") != 0:
        raise HTTPException(status_code=400, detail=data.get("msg", "Deposit failed"))
    return data


@app.post("/users/{user_code}/withdraw")
def withdraw_user(user_code: str, payload: dict = Body(...)):
    amount = payload.get("amount")
    if amount is None:
        raise HTTPException(status_code=400, detail="amount is required")
    data = call_casino("Withdraw", {"userCode": user_code, "currencyCode": CURRENCY_CODE, "amount": amount})
    log_activity("Withdraw", {"userCode": user_code, "amount": amount}, data)
    if data.get("status") != 0:
        raise HTTPException(status_code=400, detail=data.get("msg", "Withdraw failed"))
    return data


@app.get("/vendors")
def get_vendors():
    return call_casino("GetVendors", {})


@app.get("/games")
def get_games(vendor: str = ""):
    return call_casino("GetVendorGames", {"vendorCode": vendor})


@app.post("/casino")
def casino_proxy(payload: dict = Body(...)):
    method = payload.pop("method", None)
    if not method:
        raise HTTPException(status_code=400, detail="method is required")
    data = call_casino(method, payload)
    if method in MUTATING_CASINO_METHODS:
        log_activity(method, payload, data)
    return data


@app.get("/logs")
def list_logs(limit: int = 500):
    return [serialize(item) for item in db.activity_log.find().sort("_id", -1).limit(limit)]


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)

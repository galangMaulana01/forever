import os

import httpx
from bson import ObjectId
from bson.errors import InvalidId
from dotenv import load_dotenv
from fastapi import Body, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pymongo import MongoClient

load_dotenv()

CASINO_BASE_URL = "https://api.aicvgdbi.win/api/casinoapi"
CASINO_TOKEN = os.environ["CASINO_TOKEN"]
CASINO_AGENT = os.environ["CASINO_AGENT"]
CURRENCY_CODE = "IDR"

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


def call_casino(method: str, params: dict):
    body = {"method": method, "token": CASINO_TOKEN, "agentCode": CASINO_AGENT, **params}
    body = {key: value for key, value in body.items() if value not in ("", None)}
    try:
        response = httpx.post(CASINO_BASE_URL, json=body, timeout=15)
        response.raise_for_status()
    except httpx.HTTPError as error:
        raise HTTPException(status_code=502, detail=f"Casino API error: {error}") from error
    return response.json()


def call_casino_or_raise(method: str, params: dict, ignore_status=()):
    data = call_casino(method, params)
    if data.get("status") not in (0, *ignore_status):
        raise HTTPException(status_code=400, detail=data.get("msg", f"{method} failed"))
    return data


@app.post("/users")
def create_user(payload: dict = Body(...)):
    user_code = payload.get("user_code")
    if not user_code:
        raise HTTPException(status_code=400, detail="user_code is required")
    # DUPLICATE_USER (7) means the user already exists upstream - treat as fine, not an error.
    call_casino_or_raise("CreateUser", {"userCode": user_code}, ignore_status=(7,))
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
    return call_casino_or_raise("Deposit", {"userCode": user_code, "currencyCode": CURRENCY_CODE, "amount": amount})


@app.post("/users/{user_code}/withdraw")
def withdraw_user(user_code: str, payload: dict = Body(...)):
    amount = payload.get("amount")
    if amount is None:
        raise HTTPException(status_code=400, detail="amount is required")
    return call_casino_or_raise("Withdraw", {"userCode": user_code, "currencyCode": CURRENCY_CODE, "amount": amount})


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
    return call_casino(method, payload)


@app.post("/launch-history")
def create_launch_history(payload: dict = Body(...)):
    doc = {
        "user_code": payload.get("user_code"),
        "vendor_code": payload.get("vendor_code"),
        "game_code": payload.get("game_code"),
        "response": payload.get("response"),
    }
    result = db.launch_history.insert_one(doc)
    return serialize(db.launch_history.find_one({"_id": result.inserted_id}))


@app.get("/launch-history")
def list_launch_history():
    return [serialize(item) for item in db.launch_history.find().sort("_id", -1)]


@app.delete("/launch-history/{item_id}")
def delete_launch_history(item_id: str):
    try:
        object_id = ObjectId(item_id)
    except InvalidId as error:
        raise HTTPException(status_code=400, detail="Invalid id") from error
    result = db.launch_history.delete_one({"_id": object_id})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Not found")
    return {"status": "success", "message": "Deleted"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)

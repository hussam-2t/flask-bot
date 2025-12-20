import os
import math
from flask import Flask, request, jsonify
from dotenv import load_dotenv
import ccxt

# =========================
# Load ENV
# =========================
load_dotenv()

# ===== ENV TEST (آمن 100%) =====
print("ENV TEST")
print("OKX_API_KEY:", bool(os.getenv("OKX_API_KEY")))
print("OKX_SECRET_KEY:", bool(os.getenv("OKX_SECRET_KEY")))
print("OKX_PASSPHRASE:", bool(os.getenv("OKX_PASSPHRASE")))
print("OKX_DEMO:", os.getenv("OKX_DEMO"))
print("------------------------")

OKX_API_KEY = os.getenv("OKX_API_KEY")
OKX_SECRET_KEY = os.getenv("OKX_SECRET_KEY")
OKX_PASSPHRASE = os.getenv("OKX_PASSPHRASE")
WEBHOOK_PASSPHRASE = os.getenv("WEBHOOK_PASSPHRASE", "")
OKX_DEMO = os.getenv("OKX_DEMO", "1") == "1"

if not all([OKX_API_KEY, OKX_SECRET_KEY, OKX_PASSPHRASE]):
    raise RuntimeError("Missing OKX env vars. Check .env")

# =========================
# OKX via CCXT
# =========================
okx = ccxt.okx({
    "apiKey": OKX_API_KEY,
    "secret": OKX_SECRET_KEY,
    "password": OKX_PASSPHRASE,  # passphrase
    "enableRateLimit": True,
    "options": {
        "defaultType": "swap",   # perpetual futures
    },
})

# Demo / Paper trading
if OKX_DEMO:
    okx.set_sandbox_mode(True)

# =========================
# Flask
# =========================
app = Flask(__name__)

# =========================
# Bot Settings
# =========================
symbol = "BTC/USDT:USDT"
leverage = 5
risk_percent = 0.02
sl_percent = 0.01
tp_percent = 0.015


# =========================
# Helpers
# =========================
def get_usdt_balance() -> float:
    bal = okx.fetch_balance()
    free = bal.get("USDT", {}).get("free")
    if free is None:
        free = bal.get("free", {}).get("USDT", 0)
    return float(free or 0)


def get_last_price() -> float:
    ticker = okx.fetch_ticker(symbol)
    return float(ticker["last"])


def set_leverage():
    okx.set_leverage(leverage, symbol)


def calculate_position_size(balance_usdt: float, price: float) -> float:
    risk_amount = balance_usdt * risk_percent
    sl_amount_per_btc = price * sl_percent
    qty = risk_amount / sl_amount_per_btc

    market = okx.market(symbol)
    precision = market.get("precision", {}).get("amount")
    if precision is not None:
        qty = float(f"{qty:.{precision}f}")

    return qty


def place_order(signal: str):
    balance = get_usdt_balance()
    price = get_last_price()
    qty = calculate_position_size(balance, price)

    if qty <= 0:
        raise RuntimeError("Calculated qty <= 0")

    set_leverage()

    side = "buy" if signal == "buy" else "sell"

    # دخول Market
    entry = okx.create_order(symbol, "market", side, qty)

    # TP / SL
    if signal == "buy":
        sl_price = price * (1 - sl_percent)
        tp_price = price * (1 + tp_percent)
        close_side = "sell"
    else:
        sl_price = price * (1 + sl_percent)
        tp_price = price * (1 - tp_percent)
        close_side = "buy"

    params = {"reduceOnly": True}

    tp = okx.create_order(symbol, "limit", close_side, qty, tp_price, params)

    sl = okx.create_order(
        symbol,
        "stop",
        close_side,
        qty,
        None,
        {
            **params,
            "stopPrice": sl_price,
        },
    )

    return {
        "qty": qty,
        "price": price,
        "entry": entry,
        "tp": tp,
        "sl": sl,
    }


# =========================
# Routes
# =========================
@app.route("/", methods=["GET"])
def home():
    return "OKX Demo Bot (ccxt) is running", 200


@app.route("/webhook", methods=["POST"])
def webhook():
    try:
        data = request.get_json(force=True)
        print("WEBHOOK DATA:", data)

        if WEBHOOK_PASSPHRASE and data.get("passphrase") != WEBHOOK_PASSPHRASE:
            return jsonify({"error": "Invalid passphrase"}), 403

        signal = data.get("signal")
        if signal not in ("buy", "sell"):
            return jsonify({"error": "signal must be buy or sell"}), 400

        result = place_order(signal)
        return jsonify({"success": True, "result": result}), 200

    except Exception as e:
        print("ERROR:", e)
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    port = int(os.getenv("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=False)

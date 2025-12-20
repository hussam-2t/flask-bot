import os
from flask import Flask, request, jsonify
from dotenv import load_dotenv
import ccxt

# =========================
# Load ENV
# =========================
load_dotenv()

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
OKX_TD_MODE = os.getenv("OKX_TD_MODE", "cross")  # cross / isolated

if not all([OKX_API_KEY, OKX_SECRET_KEY, OKX_PASSPHRASE]):
    raise RuntimeError("Missing OKX env vars. Check Render Environment Variables")

# =========================
# OKX via CCXT
# =========================
okx = ccxt.okx({
    "apiKey": OKX_API_KEY,
    "secret": OKX_SECRET_KEY,
    "password": OKX_PASSPHRASE,
    "enableRateLimit": True,
    "options": {"defaultType": "swap"},
})

if OKX_DEMO:
    okx.set_sandbox_mode(True)

okx.load_markets()

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
    free = None
    if isinstance(bal.get("USDT"), dict):
        free = bal["USDT"].get("free")
    if free is None and isinstance(bal.get("free"), dict):
        free = bal["free"].get("USDT")
    return float(free or 0.0)


def get_last_price() -> float:
    ticker = okx.fetch_ticker(symbol)
    return float(ticker["last"])


def set_leverage():
    okx.set_leverage(leverage, symbol)


def calculate_position_size(balance_usdt: float, price: float) -> float:
    risk_amount = balance_usdt * risk_percent
    sl_amount_per_btc = price * sl_percent
    qty = risk_amount / sl_amount_per_btc
    qty = float(okx.amount_to_precision(symbol, qty))  # ✅ صحيح لـ OKX
    return qty


def _place_tp_sl(close_side: str, qty: float, tp_price: float, sl_price: float, reduce_only: bool):
    """
    يحاول وضع TP limit + SL trigger market.
    إذا reduce_only غير مدعوم في حسابك/الـ demo، سنعيد المحاولة بدونه.
    """
    base_params = {"tdMode": OKX_TD_MODE}

    tp_params = dict(base_params)
    sl_params = dict(base_params)

    if reduce_only:
        tp_params["reduceOnly"] = True
        sl_params["reduceOnly"] = True

    # TP Limit
    tp = okx.create_order(symbol, "limit", close_side, qty, tp_price, tp_params)

    # SL Trigger Market (✅) باستخدام triggerPrice
    sl_params["triggerPrice"] = sl_price
    sl = okx.create_order(symbol, "market", close_side, qty, None, sl_params)

    return tp, sl


def place_order(signal: str):
    balance = get_usdt_balance()
    if balance <= 0:
        raise RuntimeError("USDT balance is 0 (or not readable).")

    price = get_last_price()
    qty = calculate_position_size(balance, price)
    if qty <= 0:
        raise RuntimeError("Calculated qty <= 0")

    set_leverage()

    # دخول Market
    side = "buy" if signal == "buy" else "sell"
    entry = okx.create_order(symbol, "market", side, qty, None, {"tdMode": OKX_TD_MODE})

    # حساب TP/SL
    if signal == "buy":
        sl_price = price * (1 - sl_percent)
        tp_price = price * (1 + tp_percent)
        close_side = "sell"
    else:
        sl_price = price * (1 + sl_percent)
        tp_price = price * (1 - tp_percent)
        close_side = "buy"

    tp_price = float(okx.price_to_precision(symbol, tp_price))
    sl_price = float(okx.price_to_precision(symbol, sl_price))

    # 1) جرب مع reduceOnly
    try:
        tp, sl = _place_tp_sl(close_side, qty, tp_price, sl_price, reduce_only=True)
        reduce_only_used = True
    except Exception as e:
        msg = str(e)
        # 51205 Reduce Only is not available -> أعد المحاولة بدون reduceOnly
        if "51205" in msg or "Reduce Only is not available" in msg:
            tp, sl = _place_tp_sl(close_side, qty, tp_price, sl_price, reduce_only=False)
            reduce_only_used = False
        else:
            raise

    return {
        "qty": qty,
        "price": price,
        "sl_price": sl_price,
        "tp_price": tp_price,
        "reduce_only_used": reduce_only_used,
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
        import traceback
        print("ERROR:", e)
        print(traceback.format_exc())
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    port = int(os.getenv("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=False)

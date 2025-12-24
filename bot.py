import os
import time
import traceback
from flask import Flask, request, jsonify
from dotenv import load_dotenv
import ccxt

# =========================
# Load .env / Render env vars
# =========================
load_dotenv()

OKX_API_KEY = os.getenv("OKX_API_KEY", "").strip()
OKX_SECRET_KEY = os.getenv("OKX_SECRET_KEY", "").strip()
OKX_PASSPHRASE = os.getenv("OKX_PASSPHRASE", "").strip()

WEBHOOK_PASSPHRASE = os.getenv("WEBHOOK_PASSPHRASE", "supersecretpass").strip()

OKX_DEMO = os.getenv("OKX_DEMO", "1").strip() == "1"
OKX_TD_MODE = os.getenv("OKX_TD_MODE", "isolated").strip().lower()  # isolated / cross
OKX_LEVERAGE = int(os.getenv("OKX_LEVERAGE", "5").strip())

RISK_PERCENT = float(os.getenv("RISK_PERCENT", "0.02").strip())
SL_PERCENT = float(os.getenv("SL_PERCENT", "0.01").strip())
TP_PERCENT = float(os.getenv("TP_PERCENT", "0.015").strip())

SIGNAL_COOLDOWN_SEC = int(os.getenv("SIGNAL_COOLDOWN_SEC", "60").strip())

OKX_SYMBOL = os.getenv("OKX_SYMBOL", "BTC/USDT:USDT").strip()

# =========================
# ENV sanity (no secrets)
# =========================
print("ENV TEST")
print("OKX_API_KEY:", bool(OKX_API_KEY))
print("OKX_SECRET_KEY:", bool(OKX_SECRET_KEY))
print("OKX_PASSPHRASE:", bool(OKX_PASSPHRASE))
print("OKX_DEMO:", "1" if OKX_DEMO else "0")
print("OKX_TD_MODE:", OKX_TD_MODE)
print("OKX_SYMBOL:", OKX_SYMBOL)
print("------------------------")

if OKX_TD_MODE not in ("isolated", "cross"):
    raise RuntimeError("OKX_TD_MODE must be 'isolated' or 'cross'")

if not (OKX_API_KEY and OKX_SECRET_KEY and OKX_PASSPHRASE):
    raise RuntimeError("Missing OKX env vars. Check Render Env Vars or .env")

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

_last_signal = None
_last_signal_ts = 0.0
_inflight = False


def _now() -> float:
    return time.time()


def get_balance_usdt() -> float:
    bal = okx.fetch_balance()
    free = None
    if isinstance(bal.get("USDT"), dict):
        free = bal["USDT"].get("free")
    if free is None and isinstance(bal.get("free"), dict):
        free = bal["free"].get("USDT")
    return float(free or 0.0)


def get_last_price() -> float:
    t = okx.fetch_ticker(OKX_SYMBOL)
    return float(t["last"])


def set_leverage():
    try:
        okx.set_leverage(OKX_LEVERAGE, OKX_SYMBOL, params={"marginMode": OKX_TD_MODE})
        return
    except Exception:
        pass
    okx.set_leverage(OKX_LEVERAGE, OKX_SYMBOL)


def calculate_qty(balance_usdt: float, price: float) -> float:
    # Risk model: risk_amount / (price*SL%)
    risk_amount = balance_usdt * RISK_PERCENT
    sl_amount_per_1 = price * SL_PERCENT
    qty = risk_amount / sl_amount_per_1
    qty = float(okx.amount_to_precision(OKX_SYMBOL, qty))
    return qty


def has_open_position() -> bool:
    try:
        positions = okx.fetch_positions([OKX_SYMBOL])
    except Exception:
        positions = okx.fetch_positions()

    for p in positions:
        if p.get("symbol") != OKX_SYMBOL:
            continue
        contracts = p.get("contracts")
        if contracts is None:
            contracts = p.get("info", {}).get("pos") or 0
        try:
            c = float(contracts or 0.0)
        except Exception:
            c = 0.0
        if abs(c) > 0:
            return True
    return False


def market_id() -> str:
    return okx.market(OKX_SYMBOL)["id"]  # مثال: BTC-USDT-SWAP


def place_entry_market(signal: str, qty: float) -> dict:
    side = "buy" if signal == "buy" else "sell"
    params = {"tdMode": OKX_TD_MODE}
    return okx.create_order(OKX_SYMBOL, "market", side, qty, None, params)


def place_algo_sl(signal: str, qty: float, trigger_price: float) -> dict:
    inst_id = market_id()
    close_side = "sell" if signal == "buy" else "buy"
    trigger_price = float(okx.price_to_precision(OKX_SYMBOL, trigger_price))

    payload = {
        "instId": inst_id,
        "tdMode": OKX_TD_MODE,
        "side": close_side,
        "ordType": "conditional",
        "sz": str(qty),
        "slTriggerPx": str(trigger_price),
        "slOrdPx": "-1",  # market on trigger
    }
    return okx.privatePostTradeOrderAlgo(payload)


def place_algo_tp(signal: str, qty: float, trigger_price: float) -> dict:
    inst_id = market_id()
    close_side = "sell" if signal == "buy" else "buy"
    trigger_price = float(okx.price_to_precision(OKX_SYMBOL, trigger_price))

    payload = {
        "instId": inst_id,
        "tdMode": OKX_TD_MODE,
        "side": close_side,
        "ordType": "conditional",
        "sz": str(qty),
        "tpTriggerPx": str(trigger_price),
        "tpOrdPx": "-1",  # market on trigger
    }
    return okx.privatePostTradeOrderAlgo(payload)


def execute_trade(signal: str) -> dict:
    if has_open_position():
        return {"skipped": True, "reason": "Position already open. No new trade."}

    balance = get_balance_usdt()
    if balance <= 0:
        raise RuntimeError("USDT balance is 0 (or not readable).")

    price = get_last_price()
    qty = calculate_qty(balance, price)
    if qty <= 0:
        raise RuntimeError("Calculated qty <= 0 (check RISK_PERCENT/SL_PERCENT).")

    set_leverage()

    entry = place_entry_market(signal, qty)

    # نأخذ سعر بعد الدخول (أقرب لسعر الدخول الحقيقي)
    time.sleep(0.8)
    ref_price = get_last_price()

    if signal == "buy":
        sl = ref_price * (1 - SL_PERCENT)
        tp = ref_price * (1 + TP_PERCENT)
    else:
        sl = ref_price * (1 + SL_PERCENT)
        tp = ref_price * (1 - TP_PERCENT)

    # ✅ أهم فرق: نرسل TP و SL كأمرين منفصلين (كلاهما سيظهر في OKX)
    sl_resp = place_algo_sl(signal, qty, sl)
    tp_resp = place_algo_tp(signal, qty, tp)

    return {
        "symbol": OKX_SYMBOL,
        "signal": signal,
        "qty": qty,
        "entry": entry,
        "ref_price": float(okx.price_to_precision(OKX_SYMBOL, ref_price)),
        "sl": float(okx.price_to_precision(OKX_SYMBOL, sl)),
        "tp": float(okx.price_to_precision(OKX_SYMBOL, tp)),
        "sl_algo": sl_resp,
        "tp_algo": tp_resp,
        "skipped": False
    }


@app.route("/", methods=["GET"])
def home():
    return "OKX Bot running ✅", 200


@app.route("/webhook", methods=["POST"])
def webhook():
    global _last_signal, _last_signal_ts, _inflight

    try:
        data = request.get_json(silent=True)
        if not data:
            raw = request.data.decode("utf-8", errors="ignore")
            return jsonify({
                "error": "Request body must be valid JSON",
                "hint": "Send JSON مثل: {\"passphrase\":\"supersecretpass\",\"signal\":\"buy\"}",
                "received_raw": raw[:200]
            }), 400

        if WEBHOOK_PASSPHRASE and data.get("passphrase") != WEBHOOK_PASSPHRASE:
            return jsonify({"error": "Invalid passphrase"}), 403

        signal = (data.get("signal") or "").strip().lower()
        if signal not in ("buy", "sell"):
            return jsonify({"error": "signal must be buy or sell"}), 400

        if _inflight:
            return jsonify({"success": True, "skipped": True, "reason": "Busy (inflight). Try again."}), 200

        ts = _now()
        if _last_signal == signal and (ts - _last_signal_ts) < SIGNAL_COOLDOWN_SEC:
            return jsonify({
                "success": True,
                "skipped": True,
                "reason": f"Duplicate signal blocked (cooldown {SIGNAL_COOLDOWN_SEC}s)",
                "signal": signal
            }), 200

        _inflight = True
        _last_signal = signal
        _last_signal_ts = ts

        result = execute_trade(signal)

        _inflight = False
        return jsonify({"success": True, "result": result}), 200

    except Exception as e:
        _inflight = False
        print("ERROR:", e)
        print(traceback.format_exc())
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    port = int(os.getenv("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=False)

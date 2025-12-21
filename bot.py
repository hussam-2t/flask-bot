import os
import time
import traceback
from flask import Flask, request, jsonify
from dotenv import load_dotenv
import ccxt

# =========================
# Load .env
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

# زوج التداول (تقدر تغيّره لاحقًا لأي عملة)
OKX_SYMBOL = os.getenv("OKX_SYMBOL", "BTC/USDT:USDT").strip()

# =========================
# Quick ENV sanity (no secrets printed)
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
    raise RuntimeError("Missing OKX env vars (OKX_API_KEY/OKX_SECRET_KEY/OKX_PASSPHRASE). Check .env or Render Env Vars.")

# =========================
# OKX via CCXT
# =========================
okx = ccxt.okx({
    "apiKey": OKX_API_KEY,
    "secret": OKX_SECRET_KEY,
    "password": OKX_PASSPHRASE,
    "enableRateLimit": True,
    "options": {
        "defaultType": "swap",  # perpetual swaps
    }
})

if OKX_DEMO:
    okx.set_sandbox_mode(True)

okx.load_markets()

# =========================
# Flask
# =========================
app = Flask(__name__)

# =========================
# Anti-duplicate (in-memory)
# =========================
_last_signal = None
_last_signal_ts = 0.0


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
    """
    OKX يحتاج محيانًا params إضافية.
    نجرب أكثر من شكل لضمان العمل.
    """
    try:
        okx.set_leverage(OKX_LEVERAGE, OKX_SYMBOL, params={"marginMode": OKX_TD_MODE})
        return
    except Exception:
        pass

    try:
        okx.set_leverage(OKX_LEVERAGE, OKX_SYMBOL)
        return
    except Exception as e:
        raise RuntimeError(f"Failed to set leverage: {e}")


def calculate_qty(balance_usdt: float, price: float) -> float:
    risk_amount = balance_usdt * RISK_PERCENT
    sl_amount_per_1 = price * SL_PERCENT  # loss per 1 BTC (approx)
    qty = risk_amount / sl_amount_per_1

    qty = float(okx.amount_to_precision(OKX_SYMBOL, qty))
    return qty


def has_open_position() -> bool:
    """
    يمنع فتح صفقة جديدة إذا يوجد Position مفتوح على نفس الزوج.
    """
    try:
        positions = okx.fetch_positions([OKX_SYMBOL])
    except Exception:
        # إذا فشل، نحاول بدون فلتر
        try:
            positions = okx.fetch_positions()
        except Exception:
            return False

    for p in positions:
        if p.get("symbol") != OKX_SYMBOL:
            continue

        contracts = p.get("contracts")
        if contracts is None:
            contracts = p.get("info", {}).get("pos") or p.get("info", {}).get("position") or 0

        try:
            c = float(contracts or 0.0)
        except Exception:
            c = 0.0

        if abs(c) > 0:
            return True

    return False


def place_market_with_attached_tpsl(signal: str) -> dict:
    """
    يفتح Market + يرفق TP/SL في نفس أمر الدخول
    وهذا يجعل TP/SL "ظاهرة" في واجهة OKX غالبًا.
    """
    # 1) لا تفتح إذا توجد صفقة مفتوحة
    if has_open_position():
        return {
            "skipped": True,
            "reason": "Position already open. No new trades."
        }

    balance = get_balance_usdt()
    if balance <= 0:
        raise RuntimeError("USDT balance is 0 (or not readable).")

    price = get_last_price()
    qty = calculate_qty(balance, price)
    if qty <= 0:
        raise RuntimeError("Calculated qty <= 0 (check risk/SL settings).")

    set_leverage()

    side = "buy" if signal == "buy" else "sell"

    # حساب TP/SL كقيم Trigger
    if signal == "buy":
        sl_trigger = price * (1 - SL_PERCENT)
        tp_trigger = price * (1 + TP_PERCENT)
    else:
        sl_trigger = price * (1 + SL_PERCENT)
        tp_trigger = price * (1 - TP_PERCENT)

    tp_trigger = float(okx.price_to_precision(OKX_SYMBOL, tp_trigger))
    sl_trigger = float(okx.price_to_precision(OKX_SYMBOL, sl_trigger))

    # ✅ Attached TP/SL:
    # - tpTriggerPx/slTriggerPx: سعر التفعيل
    # - tpOrdPx/slOrdPx: سعر التنفيذ عند التفعيل
    #   في OKX يمكن استخدام "-1" كـ "Market" عند التفعيل (شائع)
    params = {
        "tdMode": OKX_TD_MODE,
        "tpTriggerPx": tp_trigger,
        "tpOrdPx": "-1",
        "slTriggerPx": sl_trigger,
        "slOrdPx": "-1",
    }

    entry = okx.create_order(
        OKX_SYMBOL,
        "market",
        side,
        qty,
        None,
        params
    )

    return {
        "skipped": False,
        "symbol": OKX_SYMBOL,
        "side": side,
        "qty": qty,
        "entry_price_ref": price,
        "tp_trigger": tp_trigger,
        "sl_trigger": sl_trigger,
        "entry": entry
    }


# =========================
# Routes
# =========================
@app.route("/", methods=["GET"])
def home():
    return "OKX Bot running ✅", 200


@app.route("/webhook", methods=["POST"])
def webhook():
    global _last_signal, _last_signal_ts

    try:
        data = request.get_json(force=True)

        # passphrase check
        if WEBHOOK_PASSPHRASE and data.get("passphrase") != WEBHOOK_PASSPHRASE:
            return jsonify({"error": "Invalid passphrase"}), 403

        signal = (data.get("signal") or "").strip().lower()
        if signal not in ("buy", "sell"):
            return jsonify({"error": "signal must be buy or sell"}), 400

        # 2) منع تكرار نفس الإشارة خلال cooldown
        ts = _now()
        if _last_signal == signal and (ts - _last_signal_ts) < SIGNAL_COOLDOWN_SEC:
            return jsonify({
                "success": True,
                "skipped": True,
                "reason": f"Duplicate signal blocked (cooldown {SIGNAL_COOLDOWN_SEC}s)",
                "signal": signal
            }), 200

        # حدّث ذاكرة آخر إشارة
        _last_signal = signal
        _last_signal_ts = ts

        # 3) تنفيذ الصفقة (مع TP/SL ظاهرة) + منع صفقة لو فيه Position
        result = place_market_with_attached_tpsl(signal)

        return jsonify({"success": True, "result": result}), 200

    except Exception as e:
        print("ERROR:", e)
        print(traceback.format_exc())
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    # Render يستخدم PORT تلقائيًا
    port = int(os.getenv("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=False)

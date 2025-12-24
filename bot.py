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

# --- Risk mode (optional) ---
RISK_PERCENT = float(os.getenv("RISK_PERCENT", "0.02").strip())  # used only if USE_FULL_BALANCE=0

# --- Full balance mode ---
USE_FULL_BALANCE = os.getenv("USE_FULL_BALANCE", "0").strip() == "1"
BALANCE_UTILIZATION = float(os.getenv("BALANCE_UTILIZATION", "0.95").strip())  # 0.90~0.97 أفضل من 1.00

SL_PERCENT = float(os.getenv("SL_PERCENT", "0.01").strip())
TP_PERCENT = float(os.getenv("TP_PERCENT", "0.015").strip())

SIGNAL_COOLDOWN_SEC = int(os.getenv("SIGNAL_COOLDOWN_SEC", "60").strip())

# Default: BTC perp (OKX USDT-margined swap)
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
print("USE_FULL_BALANCE:", USE_FULL_BALANCE)
print("BALANCE_UTILIZATION:", BALANCE_UTILIZATION)
print("------------------------")

if OKX_TD_MODE not in ("isolated", "cross"):
    raise RuntimeError("OKX_TD_MODE must be 'isolated' or 'cross'")

if not (OKX_API_KEY and OKX_SECRET_KEY and OKX_PASSPHRASE):
    raise RuntimeError("Missing OKX env vars. Check Render Env Vars or .env")

if not (0 < BALANCE_UTILIZATION <= 1.0):
    raise RuntimeError("BALANCE_UTILIZATION must be between 0 and 1")

# =========================
# OKX via CCXT
# =========================
okx = ccxt.okx({
    "apiKey": OKX_API_KEY,
    "secret": OKX_SECRET_KEY,
    "password": OKX_PASSPHRASE,
    "enableRateLimit": True,
    "options": {
        "defaultType": "swap",  # IMPORTANT for perpetuals
    }
})

if OKX_DEMO:
    okx.set_sandbox_mode(True)

okx.load_markets()

# Pre-cache market info
_market = okx.market(OKX_SYMBOL)
CONTRACT_SIZE = float(_market.get("contractSize") or 1.0)  # OKX BTC-USDT-SWAP غالبًا 0.01 BTC


# =========================
# Flask
# =========================
app = Flask(__name__)

# =========================
# Anti-duplicate & anti-parallel
# =========================
_last_signal = None
_last_signal_ts = 0.0
_inflight = False


def _now() -> float:
    return time.time()


def _fetch_balance_swap() -> dict:
    """
    Ensures we read balance from swap account (not spot/funding).
    """
    try:
        return okx.fetch_balance({"type": "swap"})
    except Exception:
        return okx.fetch_balance()


def get_balance_usdt() -> float:
    bal = _fetch_balance_swap()

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
    try:
        okx.set_leverage(OKX_LEVERAGE, OKX_SYMBOL)
        return
    except Exception as e:
        raise RuntimeError(f"Failed to set leverage: {e}")


def base_to_contracts(base_qty: float) -> float:
    contracts = base_qty / CONTRACT_SIZE
    contracts = float(okx.amount_to_precision(OKX_SYMBOL, contracts))
    return contracts


def contracts_to_base(contracts: float) -> float:
    return float(contracts) * CONTRACT_SIZE


def calculate_qty_contracts(balance_usdt: float, price: float) -> float:
    if USE_FULL_BALANCE:
        usable_margin = balance_usdt * BALANCE_UTILIZATION
        max_notional = usable_margin * OKX_LEVERAGE
        base_qty = max_notional / price
        return base_to_contracts(base_qty)

    risk_amount = balance_usdt * RISK_PERCENT
    sl_amount_per_1_base = price * SL_PERCENT
    base_qty = risk_amount / sl_amount_per_1_base
    return base_to_contracts(base_qty)


def has_open_position() -> bool:
    try:
        positions = okx.fetch_positions([OKX_SYMBOL])
    except Exception:
        try:
            positions = okx.fetch_positions()
        except Exception:
            return False

    for p in positions:
        if p.get("symbol") != OKX_SYMBOL:
            continue

        contracts = p.get("contracts")
        if contracts is None:
            contracts = p.get("info", {}).get("pos") or p.get("info", {}).get("availPos") or 0

        try:
            c = float(contracts or 0.0)
        except Exception:
            c = 0.0

        if abs(c) > 0:
            return True
    return False


def _market_id() -> str:
    m = okx.market(OKX_SYMBOL)
    return m["id"]


def place_entry_market(signal: str, qty_contracts: float) -> dict:
    side = "buy" if signal == "buy" else "sell"
    params = {"tdMode": OKX_TD_MODE}
    entry = okx.create_order(OKX_SYMBOL, "market", side, qty_contracts, None, params)
    return entry


def _extract_entry_price_from_order(order: dict):
    avg = order.get("average")
    if avg:
        try:
            return float(avg)
        except Exception:
            pass

    px = order.get("price")
    if px:
        try:
            return float(px)
        except Exception:
            pass

    info = order.get("info") or {}
    for k in ("avgPx", "fillPx", "px"):
        if info.get(k):
            try:
                return float(info.get(k))
            except Exception:
                pass
    return None


def place_tpsl_algo(signal: str, qty_contracts: float, entry_ref_price: float) -> dict:
    """
    ✅ حل جذري: نستخدم OCO Algo (TP+SL معًا)
    - هذا النوع في OKX مصمم ليضع TP و SL في نفس الوقت.
    - إذا OKX رفض صيغة معينة (posSide) نحاول بدونها تلقائيًا.
    """

    inst_id = _market_id()
    close_side = "sell" if signal == "buy" else "buy"
    pos_side = "long" if signal == "buy" else "short"

    if signal == "buy":
        sl_trigger = entry_ref_price * (1 - SL_PERCENT)
        tp_trigger = entry_ref_price * (1 + TP_PERCENT)
    else:
        sl_trigger = entry_ref_price * (1 + SL_PERCENT)
        tp_trigger = entry_ref_price * (1 - TP_PERCENT)

    tp_trigger = okx.price_to_precision(OKX_SYMBOL, tp_trigger)
    sl_trigger = okx.price_to_precision(OKX_SYMBOL, sl_trigger)

    # --- (A) OCO with posSide (أفضل لما يكون الحساب Hedge/Long-Short)
    payload_oco_with_pos = {
        "instId": inst_id,
        "tdMode": OKX_TD_MODE,
        "side": close_side,
        "posSide": pos_side,      # قد يُرفض في وضع net
        "ordType": "oco",
        "sz": str(qty_contracts),
        "tpTriggerPx": str(tp_trigger),
        "tpOrdPx": "-1",
        "slTriggerPx": str(sl_trigger),
        "slOrdPx": "-1",
    }

    # --- (B) OCO بدون posSide (لو حسابك One-way/Net)
    payload_oco_no_pos = {
        "instId": inst_id,
        "tdMode": OKX_TD_MODE,
        "side": close_side,
        "ordType": "oco",
        "sz": str(qty_contracts),
        "tpTriggerPx": str(tp_trigger),
        "tpOrdPx": "-1",
        "slTriggerPx": str(sl_trigger),
        "slOrdPx": "-1",
    }

    last_err = None

    # Try OCO with posSide
    try:
        algo_resp = okx.privatePostTradeOrderAlgo(payload_oco_with_pos)
        return {"algo": algo_resp, "tp_trigger": float(tp_trigger), "sl_trigger": float(sl_trigger), "mode": "OCO_with_posSide"}
    except Exception as e:
        last_err = e

    # Try OCO without posSide
    try:
        algo_resp = okx.privatePostTradeOrderAlgo(payload_oco_no_pos)
        return {"algo": algo_resp, "tp_trigger": float(tp_trigger), "sl_trigger": float(sl_trigger), "mode": "OCO_no_posSide"}
    except Exception as e:
        last_err = e

    # Fallback (نادر): conditional كما كان سابقًا
    payload_cond = {
        "instId": inst_id,
        "tdMode": OKX_TD_MODE,
        "side": close_side,
        "ordType": "conditional",
        "sz": str(qty_contracts),
        "tpTriggerPx": str(tp_trigger),
        "tpOrdPx": "-1",
        "slTriggerPx": str(sl_trigger),
        "slOrdPx": "-1",
    }
    try:
        algo_resp = okx.privatePostTradeOrderAlgo(payload_cond)
        return {"algo": algo_resp, "tp_trigger": float(tp_trigger), "sl_trigger": float(sl_trigger), "mode": "CONDITIONAL_fallback"}
    except Exception as e:
        raise RuntimeError(f"Failed to create TP/SL algo (OCO/conditional). Last error: {last_err} / {e}")


def execute_trade(signal: str) -> dict:
    if has_open_position():
        return {"skipped": True, "reason": "Position already open. No new trade."}

    balance = get_balance_usdt()
    if balance <= 0:
        raise RuntimeError(
            "USDT balance is 0 or not readable in SWAP account. "
            "تأكد ان USDT موجود في حساب العقود (Swap/Futures) وليس Spot/Funding."
        )

    price = get_last_price()
    qty_contracts = calculate_qty_contracts(balance, price)
    if qty_contracts <= 0:
        raise RuntimeError("Calculated qty <= 0 (check env vars).")

    set_leverage()

    entry = place_entry_market(signal, qty_contracts)

    entry_px = _extract_entry_price_from_order(entry)
    ref_price = entry_px if entry_px else get_last_price()

    algo = place_tpsl_algo(signal, qty_contracts, ref_price)

    return {
        "symbol": OKX_SYMBOL,
        "signal": signal,
        "qty_contracts": qty_contracts,
        "qty_base_est": round(contracts_to_base(qty_contracts), 8),
        "balance_usdt_free": balance,
        "mode": "full_balance" if USE_FULL_BALANCE else "risk",
        "entry_price_ref": ref_price,
        "tp": algo.get("tp_trigger"),
        "sl": algo.get("sl_trigger"),
        "tpsl_mode": algo.get("mode"),
        "entry": entry,
        "algo": algo.get("algo"),
        "skipped": False
    }


@app.route("/", methods=["GET"])
def home():
    return "OKX Bot running ✅", 200


@app.route("/status", methods=["GET"])
def status():
    try:
        bal = get_balance_usdt()
        px = get_last_price()
        pos = has_open_position()
        return jsonify({
            "ok": True,
            "symbol": OKX_SYMBOL,
            "price": px,
            "usdt_free_swap": bal,
            "open_position": pos,
            "td_mode": OKX_TD_MODE,
            "leverage": OKX_LEVERAGE,
            "use_full_balance": USE_FULL_BALANCE,
            "balance_utilization": BALANCE_UTILIZATION,
            "contract_size": CONTRACT_SIZE,
        }), 200
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


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

        msg = str(e)
        if "51008" in msg or "insufficient" in msg.lower():
            return jsonify({
                "error": msg,
                "hint": "لو تستخدم 100% غالباً يفشل بسبب الرسوم/الهامش. جرّب BALANCE_UTILIZATION=0.95 "
                        "وتأكد الرصيد موجود في حساب SWAP (Futures) وليس Spot/Funding."
            }), 500

        return jsonify({"error": msg}), 500


if __name__ == "__main__":
    port = int(os.getenv("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=False)

import os
import json
import threading
from flask import Flask, request, jsonify
from dotenv import load_dotenv
from binance.client import Client
from binance.enums import SIDE_BUY, SIDE_SELL, ORDER_TYPE_MARKET, ORDER_TYPE_LIMIT, TIME_IN_FORCE_GTC

# تحميل المفاتيح من .env
load_dotenv()
API_KEY = os.getenv("API_KEY")
API_SECRET = os.getenv("API_SECRET")
client = Client(API_KEY, API_SECRET)

# إعداد Flask
app = Flask(__name__)

# إعدادات
symbol = "BTCUSDT"
leverage = 125
risk_percent = 0.02
tp_percent = 0.02
sl_percent = 0.01
min_qty = 0.001

# قفل التنفيذ
execution_lock = threading.Lock()

# حساب الكمية
def calculate_position_size(balance, price, leverage, risk_percent):
    usd_risk = balance * risk_percent * leverage
    qty = usd_risk / price
    qty = round(qty, 3)
    if qty < min_qty:
        print(f"❌ الكمية {qty} أقل من الحد الأدنى {min_qty} BTC، لن يتم فتح الصفقة.")
        return 0
    return qty

# تنفيذ الصفقة
def execute_trade(signal_type):
    with execution_lock:
        try:
            balance = float([b for b in client.futures_account_balance() if b['asset'] == 'USDT'][0]['balance'])
            price = float(client.futures_symbol_ticker(symbol=symbol)["price"])
            qty = calculate_position_size(balance, price, leverage, risk_percent)
            if qty == 0:
                return

            # التأكد من عدم وجود صفقة مفتوحة مسبقاً
            positions = client.futures_position_information(symbol=symbol)
            position_amt = float(positions[0]['positionAmt'])
            if (signal_type == "buy" and position_amt > 0) or (signal_type == "sell" and position_amt < 0):
                print(f"⚠️ صفقة مفتوحة بالفعل بنفس الاتجاه. لن يتم فتح صفقة جديدة.")
                return

            client.futures_change_leverage(symbol=symbol, leverage=leverage)

            if signal_type == "buy":
                order = client.futures_create_order(
                    symbol=symbol,
                    side=SIDE_BUY,
                    type=ORDER_TYPE_MARKET,
                    quantity=qty
                )
                tp_price = round(price * (1 + tp_percent), 2)
                sl_price = round(price * (1 - sl_percent), 2)
                client.futures_create_order(
                    symbol=symbol,
                    side=SIDE_SELL,
                    type=ORDER_TYPE_LIMIT,
                    quantity=qty,
                    price=str(tp_price),
                    timeInForce=TIME_IN_FORCE_GTC,
                    reduceOnly=True
                )
                client.futures_create_order(
                    symbol=symbol,
                    side=SIDE_SELL,
                    type='STOP_MARKET',
                    stopPrice=str(sl_price),
                    closePosition=True
                )
                print(f"✅ تم تنفيذ صفقة شراء | الكمية: {qty} | السعر: {price} | TP: {tp_price} | SL: {sl_price}")

            elif signal_type == "sell":
                order = client.futures_create_order(
                    symbol=symbol,
                    side=SIDE_SELL,
                    type=ORDER_TYPE_MARKET,
                    quantity=qty
                )
                tp_price = round(price * (1 - tp_percent), 2)
                sl_price = round(price * (1 + sl_percent), 2)
                client.futures_create_order(
                    symbol=symbol,
                    side=SIDE_BUY,
                    type=ORDER_TYPE_LIMIT,
                    quantity=qty,
                    price=str(tp_price),
                    timeInForce=TIME_IN_FORCE_GTC,
                    reduceOnly=True
                )
                client.futures_create_order(
                    symbol=symbol,
                    side=SIDE_BUY,
                    type='STOP_MARKET',
                    stopPrice=str(sl_price),
                    closePosition=True
                )
                print(f"✅ تم تنفيذ صفقة بيع | الكمية: {qty} | السعر: {price} | TP: {tp_price} | SL: {sl_price}")

        except Exception as e:
            print(f"❌ خطأ أثناء التنفيذ: {e}")

# استقبال Webhook
@app.route("/webhook", methods=["POST"])
def webhook():
    try:
        data = json.loads(request.data)
        print(f"📥 Received signal: {data}")

        if data.get('passphrase') != "supersecretpass":
            return jsonify({"code": "error", "message": "Invalid passphrase"}), 403

        if data.get('symbol') and data['symbol'] != symbol:
            print(f"⚠️ إشارة ليست من {symbol}، تم تجاهلها.")
            return jsonify({"code": "ignored", "message": "Signal ignored due to mismatched symbol"}), 200

        signal = data.get('signal')
        if signal == "buy":
            print("🚀 Executing BUY order")
            execute_trade("buy")
        elif signal == "sell":
            print("🔻 Executing SELL order")
            execute_trade("sell")
        else:
            print("⚠️ إشارة غير معروفة")
            return jsonify({"code": "error", "message": "Unknown signal"}), 400

        return jsonify({"code": "success", "message": "Trade executed"}), 200

    except Exception as e:
        print(f"❌ خطأ أثناء المعالجة: {e}")
        return jsonify({"code": "error", "message": str(e)}), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)

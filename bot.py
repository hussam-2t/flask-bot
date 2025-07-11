import os
import json
from flask import Flask, request, jsonify
from dotenv import load_dotenv
from binance.client import Client
from binance.enums import *

# تحميل المفاتيح من .env
load_dotenv()
API_KEY = os.getenv("API_KEY")
API_SECRET = os.getenv("API_SECRET")
if not API_KEY or not API_SECRET:
    raise Exception("API_KEY و API_SECRET غير موجودة في ملف .env")

# إعداد Binance Client
client = Client(API_KEY, API_SECRET)
client.FUTURES_AUTO_TIMESTAMP = True

# إعداد Flask
app = Flask(__name__)

# إعدادات التداول
symbol = "BTCUSDT"
leverage = 5
risk_percent = 0.02
sl_percent = 0.01
tp_percent = 0.02

# حساب الكمية مع ضبط الدقة
def calculate_position_size(balance, price):
    risk_amount = balance * risk_percent
    sl_amount = price * sl_percent
    qty = risk_amount / sl_amount
    qty = qty * leverage
    qty = round(qty, 3)  # BTCUSDT يقبل 3 أرقام عشرية فقط

    if qty < 0.001:
        print(f"❌ الكمية المحسوبة {qty} أقل من الحد الأدنى 0.001 BTC. لن يتم فتح الصفقة.")
        return 0
    return qty

# تنفيذ الصفقة
def execute_trade(signal_type):
    try:
        balance_info = client.futures_account_balance()
        usdt_balance = next((float(b['balance']) for b in balance_info if b['asset'] == 'USDT'), 0)
        if usdt_balance <= 0:
            print("❌ لا يوجد رصيد كافٍ في حساب العقود الآجلة.")
            return

        price = float(client.futures_symbol_ticker(symbol=symbol)["price"])
        quantity = calculate_position_size(usdt_balance, price)
        if quantity == 0:
            return

        client.futures_change_leverage(symbol=symbol, leverage=leverage)

        if signal_type == "buy":
            sl = round(price * (1 - sl_percent), 2)
            tp = round(price * (1 + tp_percent), 2)
            client.futures_create_order(symbol=symbol, side=SIDE_BUY, type=ORDER_TYPE_MARKET, quantity=quantity)
            client.futures_create_order(symbol=symbol, side=SIDE_SELL, type=ORDER_TYPE_STOP_MARKET, stopPrice=sl, closePosition=True)
            client.futures_create_order(symbol=symbol, side=SIDE_SELL, type=ORDER_TYPE_LIMIT, price=tp, timeInForce=TIME_IN_FORCE_GTC, closePosition=True)
            print(f"✅ تم تنفيذ صفقة شراء: الكمية={quantity}, السعر={price}, TP={tp}, SL={sl}")

        elif signal_type == "sell":
            sl = round(price * (1 + sl_percent), 2)
            tp = round(price * (1 - tp_percent), 2)
            client.futures_create_order(symbol=symbol, side=SIDE_SELL, type=ORDER_TYPE_MARKET, quantity=quantity)
            client.futures_create_order(symbol=symbol, side=SIDE_BUY, type=ORDER_TYPE_STOP_MARKET, stopPrice=sl, closePosition=True)
            client.futures_create_order(symbol=symbol, side=SIDE_BUY, type=ORDER_TYPE_LIMIT, price=tp, timeInForce=TIME_IN_FORCE_GTC, closePosition=True)
            print(f"✅ تم تنفيذ صفقة بيع: الكمية={quantity}, السعر={price}, TP={tp}, SL={sl}")

    except Exception as e:
        print(f"❌ خطأ أثناء تنفيذ الصفقة: {str(e)}")

# استقبال webhook
@app.route("/webhook", methods=["POST"])
def webhook():
    try:
        data = json.loads(request.data)
        print(f"📥 Received signal: {data}")

        if data.get('passphrase') != "supersecretpass":
            return jsonify({"code": "error", "message": "Invalid passphrase"}), 403

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
        print(f"❌ خطأ أثناء المعالجة: {str(e)}")
        return jsonify({"code": "error", "message": str(e)}), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)

import os
import json
from flask import Flask, request, jsonify
from dotenv import load_dotenv
from binance.client import Client
from binance.enums import *

# تحميل المفاتيح من ملف .env
load_dotenv()
API_KEY = os.getenv("API_KEY")
API_SECRET = os.getenv("API_SECRET")

# تحقق من وجود المفاتيح
if not API_KEY or not API_SECRET:
    raise Exception("API_KEY و API_SECRET غير موجودة في ملف .env")

# إعداد Binance Client
client = Client(API_KEY, API_SECRET)

# إعداد Flask
app = Flask(__name__)

# إعدادات البوت
symbol = "BTCUSDT"
leverage = 125  # رفع الرافعة لأقصى حد
risk_percent = 0.02
atr_multiplier = 1.5

# حساب حجم الصفقة بناءً على الرصيد
def calculate_position_size(balance, price, sl_percent):
    safe_balance = balance  # استخدام كامل الرصيد
    risk_amount = safe_balance * risk_percent
    sl_amount = price * sl_percent
    qty = risk_amount / sl_amount
    qty = max(round(qty, 3), 0.001)  # التأكد من ألا تقل الكمية عن 0.001 BTC
    return qty

# تنفيذ صفقة
def execute_trade(signal_type):
    try:
        balance_info = client.futures_account_balance()
        usdt_balance = next(item for item in balance_info if item['asset'] == 'USDT')
        balance = float(usdt_balance['balance'])

        price = float(client.futures_symbol_ticker(symbol=symbol)["price"])
        sl_percent = 0.01
        tp_percent = 0.02
        quantity = calculate_position_size(balance, price, sl_percent)

        client.futures_change_leverage(symbol=symbol, leverage=leverage)

        if signal_type == "buy":
            sl = round(price * (1 - sl_percent), 2)
            tp = round(price * (1 + tp_percent), 2)
            client.futures_create_order(
                symbol=symbol,
                side=SIDE_BUY,
                type=ORDER_TYPE_MARKET,
                quantity=quantity
            )
            client.futures_create_order(
                symbol=symbol,
                side=SIDE_SELL,
                type=ORDER_TYPE_STOP_MARKET,
                stopPrice=sl,
                closePosition=True
            )
            client.futures_create_order(
                symbol=symbol,
                side=SIDE_SELL,
                type=ORDER_TYPE_LIMIT,
                price=tp,
                timeInForce=TIME_IN_FORCE_GTC,
                closePosition=True
            )
            print(f"✅ تم تنفيذ صفقة شراء: الكمية={quantity}, السعر={price}, TP={tp}, SL={sl}")

        elif signal_type == "sell":
            sl = round(price * (1 + sl_percent), 2)
            tp = round(price * (1 - tp_percent), 2)
            client.futures_create_order(
                symbol=symbol,
                side=SIDE_SELL,
                type=ORDER_TYPE_MARKET,
                quantity=quantity
            )
            client.futures_create_order(
                symbol=symbol,
                side=SIDE_BUY,
                type=ORDER_TYPE_STOP_MARKET,
                stopPrice=sl,
                closePosition=True
            )
            client.futures_create_order(
                symbol=symbol,
                side=SIDE_BUY,
                type=ORDER_TYPE_LIMIT,
                price=tp,
                timeInForce=TIME_IN_FORCE_GTC,
                closePosition=True
            )
            print(f"✅ تم تنفيذ صفقة بيع: الكمية={quantity}, السعر={price}, TP={tp}, SL={sl}")

    except Exception as e:
        print(f"❌ خطأ أثناء تنفيذ الصفقة: {str(e)}")

# استقبال الإشارة من TradingView
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

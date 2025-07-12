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
leverage = 125
risk_percent = 0.02

# حساب حجم الصفقة بناءً على الرصيد
def calculate_position_size(balance, price, sl_percent):
    risk_amount = balance * risk_percent * leverage
    sl_amount = price * sl_percent
    qty = risk_amount / sl_amount
    qty = round(qty, 3)
    if qty < 0.001:
        print("❌ الكمية المحسوبة أقل من الحد الأدنى 0.001 BTC. لن يتم فتح الصفقة.")
        return 0
    return qty

# تنفيذ صفقة
def execute_trade(signal_type):
    try:
        # التحقق من الصفقات المفتوحة
        position_info = client.futures_position_information(symbol=symbol)
        position_amt = float(position_info[0]['positionAmt'])

        if signal_type == "buy" and position_amt > 0:
            print("⚠️ صفقة شراء مفتوحة بالفعل، لن يتم فتح صفقة جديدة.")
            return
        if signal_type == "sell" and position_amt < 0:
            print("⚠️ صفقة بيع مفتوحة بالفعل، لن يتم فتح صفقة جديدة.")
            return

        balance_info = client.futures_account_balance()
        balance = float([b['balance'] for b in balance_info if b['asset'] == 'USDT'][0])
        price = float(client.futures_symbol_ticker(symbol=symbol)["price"])
        sl_percent = 0.01
        tp_percent = 0.02

        quantity = calculate_position_size(balance, price, sl_percent)
        if quantity == 0:
            return

        client.futures_change_leverage(symbol=symbol, leverage=leverage)

        if signal_type == "buy":
            sl = round(price * (1 - sl_percent), 1)
            tp = round(price * (1 + tp_percent), 1)

            client.futures_create_order(
                symbol=symbol,
                side=SIDE_BUY,
                type=ORDER_TYPE_MARKET,
                quantity=quantity
            )
            client.futures_create_order(
                symbol=symbol,
                side=SIDE_SELL,
                type=FUTURE_ORDER_TYPE_STOP_MARKET,
                stopPrice=sl,
                quantity=quantity,
                reduceOnly=True
            )
            client.futures_create_order(
                symbol=symbol,
                side=SIDE_SELL,
                type=ORDER_TYPE_LIMIT,
                price=tp,
                timeInForce=TIME_IN_FORCE_GTC,
                quantity=quantity,
                reduceOnly=True
            )
            print(f"✅ تم تنفيذ صفقة شراء: السعر={price}, TP={tp}, SL={sl}, الكمية={quantity}")

        elif signal_type == "sell":
            sl = round(price * (1 + sl_percent), 1)
            tp = round(price * (1 - tp_percent), 1)

            client.futures_create_order(
                symbol=symbol,
                side=SIDE_SELL,
                type=ORDER_TYPE_MARKET,
                quantity=quantity
            )
            client.futures_create_order(
                symbol=symbol,
                side=SIDE_BUY,
                type=FUTURE_ORDER_TYPE_STOP_MARKET,
                stopPrice=sl,
                quantity=quantity,
                reduceOnly=True
            )
            client.futures_create_order(
                symbol=symbol,
                side=SIDE_BUY,
                type=ORDER_TYPE_LIMIT,
                price=tp,
                timeInForce=TIME_IN_FORCE_GTC,
                quantity=quantity,
                reduceOnly=True
            )
            print(f"✅ تم تنفيذ صفقة بيع: السعر={price}, TP={tp}, SL={sl}, الكمية={quantity}")

    except Exception as e:
        print(f"❌ خطأ أثناء التنفيذ: {str(e)}")

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

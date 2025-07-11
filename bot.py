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
    raise Exception("❌ API_KEY و API_SECRET غير موجودة في ملف .env")

# إعداد Binance Client
client = Client(API_KEY, API_SECRET)
client.FUTURES_AUTO_TIMESTAMP = True

# إعداد Flask
app = Flask(__name__)

# إعدادات البوت
symbol = "BTCUSDT"
leverage = 125  # يمكنك تغييرها إلى 5 عند الحاجة
risk_percent = 0.02
min_qty = 0.001

# حساب حجم الصفقة بناءً على الرصيد والرافعة والمخاطرة
def calculate_position_size(balance, price, sl_percent):
    risk_amount = balance * risk_percent
    sl_amount = price * sl_percent
    qty = (risk_amount * leverage) / sl_amount
    qty = round(qty, 3)
    return qty

# تنفيذ صفقة
def execute_trade(signal_type):
    try:
        balance_info = client.futures_account_balance()
        if not balance_info:
            print("❌ تعذر الحصول على الرصيد")
            return

        for b in balance_info:
            if b['asset'] == 'USDT':
                balance = float(b['balance'])
                break
        else:
            print("❌ لم يتم العثور على USDT في الرصيد")
            return

        price_info = client.futures_symbol_ticker(symbol=symbol)
        price = float(price_info["price"])

        sl_percent = 0.01
        tp_percent = 0.02
        qty = calculate_position_size(balance, price, sl_percent)

        print(f"📊 الرصيد: {balance} USDT | السعر: {price} | الكمية المحسوبة: {qty} BTC | الرافعة: {leverage}x")

        if qty < min_qty:
            print(f"❌ الكمية المحسوبة {qty} أقل من الحد الأدنى {min_qty} BTC. لن يتم فتح الصفقة.")
            return

        client.futures_change_leverage(symbol=symbol, leverage=leverage)

        if signal_type == "buy":
            sl = round(price * (1 - sl_percent), 2)
            tp = round(price * (1 + tp_percent), 2)

            order = client.futures_create_order(
                symbol=symbol,
                side=SIDE_BUY,
                type=FUTURE_ORDER_TYPE_MARKET,
                quantity=qty
            )
            print(f"✅ تم تنفيذ صفقة شراء: {order}")

        elif signal_type == "sell":
            sl = round(price * (1 + sl_percent), 2)
            tp = round(price * (1 - tp_percent), 2)

            order = client.futures_create_order(
                symbol=symbol,
                side=SIDE_SELL,
                type=FUTURE_ORDER_TYPE_MARKET,
                quantity=qty
            )
            print(f"✅ تم تنفيذ صفقة بيع: {order}")

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

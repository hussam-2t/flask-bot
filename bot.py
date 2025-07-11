import os
import json
from flask import Flask, request, jsonify
from dotenv import load_dotenv
from binance.client import Client
from binance.enums import *
from binance.exceptions import BinanceAPIException

# تحميل المفاتيح
load_dotenv()
API_KEY = os.getenv("API_KEY")
API_SECRET = os.getenv("API_SECRET")

if not API_KEY or not API_SECRET:
    raise Exception("❌ لم يتم العثور على API_KEY أو API_SECRET في .env")

client = Client(API_KEY, API_SECRET)
client.FUTURES_AUTO_TIMESTAMP = True

app = Flask(__name__)

symbol = "BTCUSDT"
leverage = 125
risk_percent = 0.02

def calculate_position_size(balance, price):
    qty = (balance * risk_percent * leverage) / price
    qty = round(qty, 3)
    if qty < 0.001:
        print(f"❌ الكمية المحسوبة {qty} أقل من الحد الأدنى المسموح به 0.001 BTC، لن يتم تنفيذ الصفقة.")
        return 0
    return qty

def execute_trade(signal_type):
    try:
        # التحقق من الرصيد بشكل آمن
        try:
            balance_info = client.futures_account_balance()
            usdt_balance = next((float(b['balance']) for b in balance_info if b['asset'] == 'USDT'), None)
            if usdt_balance is None:
                print("❌ لم يتم العثور على رصيد USDT في الحساب.")
                return
        except Exception as e:
            print(f"❌ خطأ أثناء جلب الرصيد: {e}")
            return

        # التحقق من السعر
        try:
            ticker = client.futures_symbol_ticker(symbol=symbol)
            price = float(ticker['price'])
        except Exception as e:
            print(f"❌ خطأ أثناء جلب السعر: {e}")
            return

        quantity = calculate_position_size(usdt_balance, price)
        if quantity == 0:
            return

        # التحقق من الصفقات المفتوحة لمنع التكرار
        positions = client.futures_position_information(symbol=symbol)
        position_amt = float(positions[0]['positionAmt'])

        if signal_type == "buy" and position_amt > 0:
            print("⚠️ صفقة شراء مفتوحة بالفعل، لن يتم فتح صفقة جديدة.")
            return
        if signal_type == "sell" and position_amt < 0:
            print("⚠️ صفقة بيع مفتوحة بالفعل، لن يتم فتح صفقة جديدة.")
            return

        client.futures_change_leverage(symbol=symbol, leverage=leverage)

        sl_percent = 0.01
        tp_percent = 0.02

        if signal_type == "buy":
            sl = round(price * (1 - sl_percent), 2)
            tp = round(price * (1 + tp_percent), 2)
            client.futures_create_order(
                symbol=symbol, side=SIDE_BUY, type=ORDER_TYPE_MARKET, quantity=quantity
            )
            client.futures_create_order(
                symbol=symbol, side=SIDE_SELL, type="STOP_MARKET", stopPrice=sl, closePosition=True
            )
            client.futures_create_order(
                symbol=symbol, side=SIDE_SELL, type=ORDER_TYPE_LIMIT,
                price=tp, timeInForce=TIME_IN_FORCE_GTC, closePosition=True
            )
            print(f"✅ تم تنفيذ صفقة شراء | الكمية: {quantity} | السعر: {price} | TP: {tp} | SL: {sl}")

        elif signal_type == "sell":
            sl = round(price * (1 + sl_percent), 2)
            tp = round(price * (1 - tp_percent), 2)
            client.futures_create_order(
                symbol=symbol, side=SIDE_SELL, type=ORDER_TYPE_MARKET, quantity=quantity
            )
            client.futures_create_order(
                symbol=symbol, side=SIDE_BUY, type="STOP_MARKET", stopPrice=sl, closePosition=True
            )
            client.futures_create_order(
                symbol=symbol, side=SIDE_BUY, type=ORDER_TYPE_LIMIT,
                price=tp, timeInForce=TIME_IN_FORCE_GTC, closePosition=True
            )
            print(f"✅ تم تنفيذ صفقة بيع | الكمية: {quantity} | السعر: {price} | TP: {tp} | SL: {sl}")

    except BinanceAPIException as e:
        print(f"❌ Binance API Error: {e.message}")
    except Exception as e:
        print(f"❌ خطأ غير متوقع أثناء التنفيذ: {e}")

@app.route("/webhook", methods=["POST"])
def webhook():
    try:
        data = json.loads(request.data)
        print(f"📥 Received signal: {data}")

        if data.get('passphrase') != "supersecretpass":
            return jsonify({"code": "error", "message": "❌ كلمة المرور غير صحيحة"}), 403

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

        return jsonify({"code": "success", "message": "✅ تم تنفيذ الأمر"}), 200

    except Exception as e:
        print(f"❌ خطأ أثناء المعالجة: {e}")
        return jsonify({"code": "error", "message": str(e)}), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)

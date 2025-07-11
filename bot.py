import os
import json
from flask import Flask, request, jsonify
from dotenv import load_dotenv
from binance.client import Client
from binance.enums import *

# تحميل المفاتيح
load_dotenv()
API_KEY = os.getenv("API_KEY")
API_SECRET = os.getenv("API_SECRET")

client = Client(API_KEY, API_SECRET)

app = Flask(__name__)

symbol = "BTCUSDT"
leverage = 125
risk_percent = 0.02

def calculate_position_size(balance, price):
    risk_amount = balance * risk_percent
    max_position_value = risk_amount * leverage
    qty = max_position_value / price
    qty = round(qty, 5)  # لضمان عدم تجاوز الدقة المسموحة
    return qty

def execute_trade(signal_type):
    try:
        balance_info = client.futures_account_balance()
        usdt_balance = next(item for item in balance_info if item['asset'] == 'USDT')
        balance = float(usdt_balance['balance'])
        price = float(client.futures_symbol_ticker(symbol=symbol)["price"])
        qty = calculate_position_size(balance, price)

        print(f"📊 الرصيد: {balance} USDT | السعر: {price} | الكمية المحسوبة: {qty} BTC | الرافعة: {leverage}x")

        if qty < 0.001:
            print(f"❌ الكمية المحسوبة {qty} أقل من الحد الأدنى 0.001 BTC. لن يتم فتح الصفقة.")
            return

        client.futures_change_leverage(symbol=symbol, leverage=leverage)

        if signal_type == "buy":
            client.futures_create_order(
                symbol=symbol,
                side=SIDE_BUY,
                type=ORDER_TYPE_MARKET,
                quantity=qty
            )
            print("✅ تم تنفيذ صفقة شراء بنجاح.")
        elif signal_type == "sell":
            client.futures_create_order(
                symbol=symbol,
                side=SIDE_SELL,
                type=ORDER_TYPE_MARKET,
                quantity=qty
            )
            print("✅ تم تنفيذ صفقة بيع بنجاح.")
    except Exception as e:
        print(f"❌ خطأ أثناء التنفيذ: {e}")

@app.route("/webhook", methods=["POST"])
def webhook():
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

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)

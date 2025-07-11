import os
import json
from flask import Flask, request, jsonify
from dotenv import load_dotenv
from binance.client import Client
from binance.enums import SIDE_BUY, SIDE_SELL, ORDER_TYPE_MARKET, ORDER_TYPE_LIMIT, TIME_IN_FORCE_GTC

# تحميل المفاتيح
load_dotenv()
API_KEY = os.getenv("API_KEY")
API_SECRET = os.getenv("API_SECRET")
client = Client(API_KEY, API_SECRET)

app = Flask(__name__)

symbol = "BTCUSDT"
leverage = 5  # تعديل الرافعة إلى 5x
risk_percent = 0.02

# جلب الدقة المسموح بها تلقائيا من Binance
exchange_info = client.futures_exchange_info()
step_size = 0.001  # افتراضي
for s in exchange_info['symbols']:
    if s['symbol'] == symbol:
        for f in s['filters']:
            if f['filterType'] == 'LOT_SIZE':
                step_size = float(f['stepSize'])
                break

def round_step_size(quantity, step_size):
    return round(quantity - (quantity % step_size), 8)

def calculate_position_size(balance, price, sl_percent):
    risk_amount = balance * risk_percent * leverage
    sl_amount = price * sl_percent
    qty = risk_amount / sl_amount
    qty = round_step_size(qty, step_size)
    if qty < step_size:
        print(f"❌ الكمية المحسوبة {qty} أقل من الحد الأدنى {step_size}. لن يتم فتح الصفقة.")
        return 0
    return qty

def execute_trade(signal_type):
    try:
        balance_info = client.futures_account_balance()
        balance = sum(float(asset['balance']) for asset in balance_info if asset['asset'] in ['USDT', 'BUSD'])
        price = float(client.futures_symbol_ticker(symbol=symbol)["price"])
        sl_percent = 0.01
        tp_percent = 0.02

        client.futures_change_leverage(symbol=symbol, leverage=leverage)
        quantity = calculate_position_size(balance, price, sl_percent)
        if quantity == 0:
            return

        if signal_type == "buy":
            sl = round(price * (1 - sl_percent), 2)
            tp = round(price * (1 + tp_percent), 2)
            client.futures_create_order(symbol=symbol, side=SIDE_BUY, type=ORDER_TYPE_MARKET, quantity=quantity)
            client.futures_create_order(symbol=symbol, side=SIDE_SELL, type="STOP_MARKET", stopPrice=sl, closePosition=True)
            client.futures_create_order(symbol=symbol, side=SIDE_SELL, type=ORDER_TYPE_LIMIT, price=tp, timeInForce=TIME_IN_FORCE_GTC, closePosition=True)
            print(f"✅ شراء: الكمية={quantity}, السعر={price}, TP={tp}, SL={sl}")

        elif signal_type == "sell":
            sl = round(price * (1 + sl_percent), 2)
            tp = round(price * (1 - tp_percent), 2)
            client.futures_create_order(symbol=symbol, side=SIDE_SELL, type=ORDER_TYPE_MARKET, quantity=quantity)
            client.futures_create_order(symbol=symbol, side=SIDE_BUY, type="STOP_MARKET", stopPrice=sl, closePosition=True)
            client.futures_create_order(symbol=symbol, side=SIDE_BUY, type=ORDER_TYPE_LIMIT, price=tp, timeInForce=TIME_IN_FORCE_GTC, closePosition=True)
            print(f"✅ بيع: الكمية={quantity}, السعر={price}, TP={tp}, SL={sl}")

    except Exception as e:
        print(f"❌ خطأ أثناء التنفيذ: {str(e)}")

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
            return jsonify({"code": "error", "message": "Unknown signal"}), 400

        return jsonify({"code": "success", "message": "Trade executed"}), 200

    except Exception as e:
        print(f"❌ خطأ أثناء المعالجة: {str(e)}")
        return jsonify({"code": "error", "message": str(e)}), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)

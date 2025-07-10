import os
import json
from flask import Flask, request
from dotenv import load_dotenv
from binance.client import Client
from binance.enums import *

# تحميل المفاتيح من ملف .env
load_dotenv()
API_KEY = os.getenv("API_KEY")
API_SECRET = os.getenv("API_SECRET")

# إعداد Binance Client
client = Client(API_KEY, API_SECRET)

# إعداد Flask
app = Flask(__name__)

# إعدادات البوت
symbol = "BTCUSDT"
leverage = 5
risk_percent = 0.02
atr_multiplier = 1.5

# حساب حجم الصفقة بناءً على الرصيد
def calculate_position_size(balance, price, sl_percent):
    risk_amount = balance * risk_percent
    sl_amount = price * sl_percent
    qty = risk_amount / sl_amount
    return round(qty, 3)

# تنفيذ صفقة
def execute_trade(signal_type):
    balance = float(client.futures_account_balance()[0]['balance'])
    price = float(client.futures_symbol_ticker(symbol=symbol)["price"])
    sl_percent = 0.01
    tp_percent = 0.015
    quantity = calculate_position_size(balance, price, sl_percent)

    client.futures_change_leverage(symbol=symbol, leverage=leverage)

    if signal_type == "buy":
        sl = round(price * (1 - sl_percent), 2)
        tp = round(price * (1 + tp_percent), 2)
        client.futures_create_order(
            symbol=symbol,
            side=SIDE_BUY,
            type=ORDER_TYPE_MARKET,
            quantity=quantity,
            reduceOnly=False
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

    elif signal_type == "sell":
        sl = round(price * (1 + sl_percent), 2)
        tp = round(price * (1 - tp_percent), 2)
        client.futures_create_order(
            symbol=symbol,
            side=SIDE_SELL,
            type=ORDER_TYPE_MARKET,
            quantity=quantity,
            reduceOnly=False
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

# استقبال الإشارة من TradingView
@app.route("/webhook", methods=["POST"])
def webhook():
    print("Received signal:", request.data)  # طباعة البيانات الخام للمتابعة
    data = json.loads(request.data)
    if data['passphrase'] != "supersecretpass":
        return {"code": "error", "message": "Invalid passphrase"}, 403

    signal = data['signal']
    if signal == "buy":
        print("Executing BUY order")
        execute_trade("buy")
    elif signal == "sell":
        print("Executing SELL order")
        execute_trade("sell")

    return {"code": "success", "message": "Trade executed"}, 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
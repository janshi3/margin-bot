import json, config, requests
from flask import Flask, request
from binance.client import Client
from binance.enums import *
from binance.exceptions import *

app = Flask(__name__)

# Binance Client
client = Client(config.API_KEY, config.API_SECRET)

# *********************************************************************************************
# FUNCTIONS
# *********************************************************************************************


# Send an error report to the specified Discord Group
def send_report(report):
    if config.REPORT:
        message = "@everyone " + report
        requests.post(config.DISCORD_LINK,
                      data={"content": message},
                      headers=config.DISCORD_HEADER)


# Repay Loan
def repay_loan(asset, amount, symbol, isolated):
    try:
        # Isolated margin function
        if isolated:
            transaction = client.repay_margin_loan(asset=asset, amount=amount, isIsolated=isolated, symbol=symbol)
        # Cross margin function
        else:
            transaction = client.repay_margin_loan(asset=asset, amount=amount)
        return transaction

    # Error
    except BinanceAPIException as e:
        send_report(str(e) + "During Repay Loan")
        return False


# Get a loan
def take_loan(asset, amount, symbol, isolated):
    try:
        # Isolated margin function
        if isolated:
            transaction = client.create_margin_loan(asset=asset, amount=amount, isIsolated=isolated, symbol=symbol)

        # Cross margin function
        else:
            transaction = client.create_margin_loan(asset=asset, amount=amount)
        return transaction

    # Error
    except BinanceAPIException as e:
        send_report(str(e) + "During Take Loan")
        return False


# Set Stop-Limit Order
def set_stop_limit(side, order, symbol, precision, stop, stop_diff, step, market, loan=0.0):
    order = False

    # Get the price and amount of the executed Market order
    price = order["price"]
    quantity = order["executedQty"]

    # If there's a loan that has to be repaid, it will not be added to the stop limit order, but repaid later instead
    if loan > 0:
        quantity -= loan

    # If the market order was a long, then the stop loss will be lower
    if side == "BUY":

        # Limit price is stop% lower than the market price, and stop price is a bit lower than the limit price
        limit_price = price / 100 * (100 - stop)
        stop_price = limit_price * (100 - stop_diff) / 100

        # Checking if values are correctly calculated, to avoid instant stop-loss trigger
        if price <= limit_price:
            limit_price = price - step
        if limit_price <= stop_price:
            stop_price = limit_price - step

        # After the market long, the stop-loss should be a sell order
        side = "SELL"

    # If the market order was a short long, then the stop loss will be higher
    elif side == "SELL":

        # Limit price is stop% higher than the market price, and stop price is a bit higher than the limit price
        limit_price = price / 100 * (100 + stop)
        stop_price = limit_price * (100 + stop_diff) / 100

        # Checking if values are correctly calculated, to avoid instant stop-loss trigger
        if price >= limit_price:
            limit_price = price + step
        if limit_price >= stop_price:
            stop_price = limit_price + step

        # After the market short, the stop-loss should be a buy order
        side = "BUY"

    # For Spot market
    if market == "SPOT":
        try:
            # Try placing a stop limit order
            order = client.create_order(symbol=symbol, side=side,
                                        type=ORDER_TYPE_STOP_LOSS_LIMIT, quantity=quantity,
                                        price=limit_price, stopPrice=stop_price,
                                        timeInForce=TIME_IN_FORCE_GTC)
        except BinanceAPIException as e:

            # If encounter a LOT_SIZE error, try again, but round the quantity to fit the min decimal amount
            if str(e) == "Filter failure: LOT_SIZE":
                order = client.create_order(symbol=symbol, side=side,
                                            type=ORDER_TYPE_STOP_LOSS_LIMIT, quantity=round(quantity, precision),
                                            price=limit_price, stopPrice=stop_price,
                                            timeInForce=TIME_IN_FORCE_GTC)
            # If that doesn't work, output an error
            else:
                send_report(str(e) + "During Spot Stop Limit")

    # For Margin
    else:
        try:
            # Try placing a stop limit order
            order = client.create_margin_order(symbol=symbol, side=side,
                                               type=ORDER_TYPE_STOP_LOSS_LIMIT, quantity=quantity,
                                               price=limit_price, stopPrice=stop_price,
                                               timeInForce=TIME_IN_FORCE_GTC)
        except BinanceAPIException as e:

            # If encounter a LOT_SIZE error, try again, but round the quantity to fit the min decimal amount
            if str(e) == "Filter failure: LOT_SIZE":
                order = client.create_margin_order(symbol=symbol, side=side,
                                                   type=ORDER_TYPE_STOP_LOSS_LIMIT, quantity=round(quantity, precision),
                                                   price=limit_price, stopPrice=stop_price,
                                                   timeInForce=TIME_IN_FORCE_GTC)
            # If that doesn't work, output an error
            else:
                send_report(str(e) + "During Margin Stop Limit")

    # Output
    return order


# Send a Spot Market order
def spot_order(side, quantity, base, symbol, precision, equity, step, market,
               stop=config.STOP_LOSS, stop_diff=config.STOP_LIMIT_DIFFERENCE):
    order = False

    # If selling
    if side == "SELL":
        try:
            # Try to execute the order
            order = client.create_order(symbol=symbol, side=side, type=ORDER_TYPE_MARKET, quantity=quantity)
        except BinanceAPIException as e:

            # If encounter a LOT_SIZE error
            if str(e) == "Filter failure: LOT_SIZE":
                try:
                    # Round the numbers to required decimal places
                    if precision >= 0:
                        quantity = round(quantity, precision)

                    # Or round up the integer
                    elif precision < 0:
                        quantity = quantity // (10 ** -precision) * (10 ** -precision)

                    # Try executing the order again
                    order = client.create_order(symbol=symbol, side=side, type=ORDER_TYPE_MARKET, quantity=quantity)

                # Output the error
                except BinanceAPIException as e:
                    send_report(str(e) + "During Spot Order Sell")

            # Error from first attempt
            else:
                send_report(str(e) + "During Spot Sell Order")

    # If buying
    elif side == "BUY":
        # Calculate how much of the asset can you buy with the base currency
        price = float(client.get_margin_price_index(symbol=symbol)['price'])
        quantity = base / price * equity

        # Round the numbers to required decimal places
        if precision >= 0:
            quantity = round(quantity, precision)

        # Or round up the integer
        elif precision < 0:
            quantity = quantity // (10 ** -precision) * (10 ** -precision)

        # Try executing the order again
        try:
            order = client.create_order(symbol=symbol, side=side, type=ORDER_TYPE_MARKET, quantity=quantity)

        # Exit if an error occurs
        except BinanceAPIException as e:
            send_report(str(e) + "During Spot Order Buy")
            return False

        # If stop-loss is enabled, run the stop-limit order function
        if stop:
            stop_order = set_stop_limit(side, order, symbol, precision, stop, stop_diff, step, market)

    # Output
    return order


# Margin Order
def margin_order(side, quantity, symbol, precision, step, market, order_type=ORDER_TYPE_MARKET,
                 stop=config.STOP_LOSS, stop_diff=config.STOP_LIMIT_DIFFERENCE, loan=0.0):
    order = False

    # Round the numbers to required decimal places
    if precision >= 0:
        quantity = round(quantity, precision)

    # Or round up the integer
    elif precision < 0:
        quantity = quantity // (10 ** -precision) * (10 ** -precision)

    try:
        # Execute the order
        order = client.create_margin_order(symbol=symbol, side=side, type=order_type, quantity=quantity)

    # Exit if an error occurs
    except BinanceAPIException as e:
        send_report(str(e) + "During Margin Order")
        return False

    # If stop-loss is enabled, run the stop-limit order function
    if stop:
        stop_order = set_stop_limit(side, order, symbol, precision, stop, stop_diff, step, market, loan)

    return order


# If possible, exit the last trade, sell all the assets, and add them to the current trade
def change_pairs(side, symbol, base, new_symbol, new_base, market):

    asset_name = symbol[:-len(base)]

    new_asset_name = new_symbol[:-len(new_base)]

    if market == "SPOT":

        # Get all user Spot wallet assets
        assets = client.get_account()['balances']

        # Check all open Spot orders
        orders = client.get_open_orders(symbol=symbol)

        # Cancel all orders
        # That is done to get rid of the last stop-loss
        # Don't place limit orders on the same currency pair with a bot active, orders will get canceled!
        for order in orders:

            # Cancel all orders 1 by 1
            try:
                result = client.cancel_order(symbol=symbol, orderId=order['orderId'])

            # Error
            except BinanceAPIException as e:
                send_report(str(e) + "During Change Pairs Spot")

        # Check how much of both currencies are available in the Spot wallet
        for asset in assets:
            if asset['asset'] == asset_name:
                asset_quantity = float(asset['free'])
            if asset['asset'] == base:
                base_quantity = float(asset['free'])
            if asset['asset'] == new_asset_name:
                new_asset_quantity = float(asset['free'])
            if asset['asset'] == new_base:
                new_base_quantity = float(asset['free'])

        if asset_name == new_asset_name:
            side = "BUY"
        elif base == new_base:
            side = "SELL"
        else:
            try:
                symbol = asset_name + new_base
                symbol_info = client.get_symbol_info(symbol)
                side = "SELL"
            except BinanceAPIException:
                try:
                    symbol = new_asset_name + base
                    symbol_info = client.get_symbol_info(symbol)
                    side = "BUY"
                except BinanceAPIException:
                    try:
                        symbol = base + new_base
                        symbol_info = client.get_symbol_info(symbol)
                        side = "SELL"
                    except BinanceAPIException:
                        try:
                            symbol = asset_name + new_asset_name
                            symbol_info = client.get_symbol_info(symbol)
                            side = "SELL"
                        except BinanceAPIException:
                            try:
                                symbol = new_base + base
                                symbol_info = client.get_symbol_info(symbol)
                                side = "SELL"
                                asset_quantity = new_base_quantity
                            except BinanceAPIException:
                                try:
                                    symbol = new_asset_name + asset_name
                                    symbol_info = client.get_symbol_info(symbol)
                                    side = "SELL"
                                    asset_quantity = new_asset_quantity
                                except BinanceAPIException:
                                    return False

        # Get information about the pair
        symbol_info = client.get_symbol_info(symbol)

        # Check the minimum precision

        precision = 0
        for rule in symbol_info['filters']:
            if rule['filterType'] == "LOT_SIZE":
                min_quantity = rule["minQty"]

                # Check the maximum precision
                max_quantity = rule["maxQty"]

                # Calculate the precision from minimum quantity
                i = min_quantity

                # Precision will be positive, if it allows floating numbers
                if i < 0:
                    while i < 0:
                        i *= 10
                        precision += 1

                # Precision will be negative, if it requires rounding up integers
                elif i > 0:
                    while i > 0:
                        i //= 10
                        precision += -1

            # Run the Spot market order function
            order = spot_order(side, asset_quantity, base_quantity, symbol, precision, 1, min_quantity, market)

            # Successful trade
            if order:
                return order
    else:
        assets = client.get_margin_account()['userAssets']

        # Check for open orders
        orders = client.get_open_margin_orders(symbol=symbol)

        # Cancel all orders
        for order in orders:
            try:
                result = client.cancel_margin_order(symbol=symbol, orderId=order['orderId'])

            # Error
            except BinanceAPIException as e:
                send_report(str(e) + "During ")

        # Check information about assets
        for asset in assets:

            if asset['asset'] == asset_name:
                # Borrowed
                asset_loan_amount = float(asset['borrowed'])
                # Free
                asset_amount = float(asset['free'])
            if asset['asset'] == base:
                # Borrowed
                base_loan_amount = float(asset['borrowed'])
                # Free
                base_amount = float(asset['free'])
            if asset['asset'] == new_asset_name:
                # Free
                new_asset_amount = float(asset['free'])
            if asset['asset'] == new_base:
                # Free
                new_base_amount = float(asset['free'])

            # Get information about the pair
            symbol_info = client.get_symbol_info(symbol)

            # Check the minimum precision
            precision = 0
            for rule in symbol_info['filters']:
                if rule['filterType'] == "LOT_SIZE":
                    min_quantity = rule["minQty"]

                    # Check the maximum precision
                    max_quantity = rule["maxQty"]

                    # Calculate the precision from minimum quantity
                    i = min_quantity

                    # Precision will be positive, if it allows floating numbers
                    if i < 0:
                        while i < 0:
                            i *= 10
                            precision += 1

                    # Precision will be negative, if it requires rounding up integers
                    elif i > 0:
                        while i > 0:
                            i //= 10
                            precision += -1

                # Check the minimum base currency amount requirements
                if rule['filterType'] == "MIN_NOTIONAL":
                    min_base_order = rule["minNotional"]

            if asset_loan_amount > 0:
                if asset_amount >= asset_loan_amount:
                    transaction = repay_loan(asset_name, asset_loan_amount, symbol)
                else:
                    # Get asset price
                    price = float(client.get_margin_price_index(symbol=symbol)['price'])
                    if asset_loan_amount * price > min_base_order:
                        order_response = margin_order("BUY", asset_loan_amount, base_amount, symbol, precision,
                                                      min_quantity, market, stop=0)
                        transaction = repay_loan(asset_name, asset_loan_amount, symbol)
                        asset_amount -= asset_loan_amount

            if base_loan_amount > 0:
                if base_amount >= base_loan_amount:
                    transaction = repay_loan(base, base_loan_amount, symbol)
                else:
                    # Get asset price
                    price = float(client.get_margin_price_index(symbol=symbol)['price'])
                    if min_base_order < base_loan_amount < asset_amount * price:
                        order_response = margin_order("SELL", asset_loan_amount, base_amount, symbol, precision,
                                                      min_quantity, market, stop=0)
                        transaction = repay_loan(base, base_loan_amount, symbol)
                        base_amount -= base_loan_amount

            if asset_name == new_asset_name:
                side = "BUY"
            elif base == new_base:
                side = "SELL"
            else:
                try:
                    symbol = asset_name + new_base
                    symbol_info = client.get_symbol_info(symbol)
                    side = "SELL"
                except BinanceAPIException:
                    try:
                        symbol = new_asset_name + base
                        symbol_info = client.get_symbol_info(symbol)
                        side = "BUY"
                    except BinanceAPIException:
                        try:
                            symbol = base + new_base
                            symbol_info = client.get_symbol_info(symbol)
                            side = "SELL"
                        except BinanceAPIException:
                            try:
                                symbol = asset_name + new_asset_name
                                symbol_info = client.get_symbol_info(symbol)
                                side = "SELL"
                            except BinanceAPIException:
                                try:
                                    symbol = new_base + base
                                    symbol_info = client.get_symbol_info(symbol)
                                    side = "SELL"
                                    asset_amount = new_base_amount
                                except BinanceAPIException:
                                    try:
                                        symbol = new_asset_name + asset_name
                                        symbol_info = client.get_symbol_info(symbol)
                                        side = "SELL"
                                        asset_amount = new_asset_amount
                                    except BinanceAPIException:
                                        return False

            # Get information about the pair
            symbol_info = client.get_symbol_info(symbol)

            # Check the minimum precision
            precision = 0
            for rule in symbol_info['filters']:
                if rule['filterType'] == "LOT_SIZE":
                    min_quantity = rule["minQty"]

                    # Check the maximum precision
                    max_quantity = rule["maxQty"]

                    # Calculate the precision from minimum quantity
                    i = min_quantity

                    # Precision will be positive, if it allows floating numbers
                    if i < 0:
                        while i < 0:
                            i *= 10
                            precision += 1

                    # Precision will be negative, if it requires rounding up integers
                    elif i > 0:
                        while i > 0:
                            i //= 10
                            precision += -1

            order_response = margin_order(side, asset_amount, base_amount, symbol, precision,
                                          min_quantity, market, stop=0)

            return order_response


# Writes a python list into a txt file
def list_to_file(str_list):

    # Open file
    f = open("last.txt", "w")

    # Write each word in a new line
    for text in str_list:
        f.write(text + "\n")

    # Close the file
    f.close()


# Check if the last recorded trade used a different currency pair.
def compare_last_pair(side, symbol, base, market):
    try:
        # Open the txt file
        f = open("last.txt", "r")

        # Read the Pair
        last_symbol = f.read(0)

        # Read the base currency
        last_base = f.read(1)

        # Read the market type
        last_market = f.read(2)

        # Close the file
        f.close()

        # Check if last trade used a different currency pair, and if it's the same market as the current trade
        if last_symbol != symbol and last_market == market and last_market != "ISOLATED":

            # Run the pair changing function, to add the funds from the last trade to the current trade
            change_pairs(side, last_symbol, last_base, symbol, base, market)

    # If a file with previous records doesn't exist, skip this part
    except FileNotFoundError:
        pass


# *********************************************************************************************
# ROUTES
# *********************************************************************************************


# Index page
@app.route('/')
def hello_world():
    return 'Hello, World!'


# Address for receiving pings to avoid idling
@app.route('/ping', methods=['POST'])
def ping():
    return "Pinged!"


# Address for receiving webhooks
@app.route('/webhook', methods=['POST'])
def webhook():

    # *********************************************************************************************
    # MAIN ROUTE
    # *********************************************************************************************
    # READING RECEIVED DATA
    # ---------------------------------------------------------------------------------------------

    # Change JSON object from webhook to a python dictionary
    data = json.loads(request.data)

    # Check if safety key is matching
    if data['passphrase'] != config.WEBHOOK_PASSPHRASE:
        return {
            "code": "error",
            "message": "Access Denied!"
        }

    # Change order type into UPPERCASE
    side = data['strategy']['order_action'].upper()

    # Change market type into UPPERCASE
    market = data['strategy']['market'].upper()
    isolated = False

    # Set a boolean variable for Isolated Margin
    if market == "ISOLATED":
        isolated = True

    # Check if a leverage variable exists, else, set leverage to 0
    try:
        leverage = float(data['strategy']['leverage'])
    except KeyError:
        leverage = 0

    # Get the base currency, and check it's length
    base_name = data['base_currency']
    base_len = len(base_name)

    # Get the stop-loss % and limit/order % price difference
    try:
        stop = float(data['strategy']['stop_loss'])

    # If no value is given, disable stop-loss
    except KeyError:
        stop = 0
    try:
        stop_diff = float(data['strategy']['limit_price_difference'])

    # If no value is given, set the limit/order difference to 0
    except KeyError:
        stop_diff = 0

    # Get The pair and asset names
    symbol = data['ticker']

    # Get the asset name by removing the base currency name from the pair
    asset_name = symbol[:-base_len]

    # Check if equity is set, and divide it by 100 for easier calculations (0.01 - 1) == (1% - 100%)
    try:
        equity = float(data['strategy']['order_equity'])/100

    # If no equity given, set it to 1 (100%)
    except KeyError:
        equity = 1

    # Check if overwriting previous order is enabled
    try:
        overwrite = bool(data['strategy']['overwrite'])

    # If not, disable it
    except KeyError:
        overwrite = False

    # If it is enabled, close the last order and add the funds to the current order, if possible
    if overwrite:

        # It's for different pairs, if the last trade used the same pair,
        # it will always close the trade, when changing from short to long
        # Function checks if the last pair is different, but in the same market for easy converting
        compare_last_pair(side, symbol, base_name, market)

    # Get information about the pair
    symbol_info = client.get_symbol_info(symbol)

    # Check the minimum precision
    precision = 0
    for rule in symbol_info['filters']:
        if rule['filterType'] == "LOT_SIZE":
            min_quantity = rule["minQty"]

            # Check the maximum precision
            max_quantity = rule["maxQty"]

            # Calculate the precision from minimum quantity
            i = min_quantity

            # Precision will be positive, if it allows floating numbers
            if i < 0:
                while i < 0:
                    i *= 10
                    precision += 1

            # Precision will be negative, if it requires rounding up integers
            elif i > 0:
                while i > 0:
                    i //= 10
                    precision += -1

        # Check the minimum base currency amount requirements
        if rule['filterType'] == "MIN_NOTIONAL":
            min_base_order = rule["minNotional"]

    # ---------------------------------------------------------------------------------------------
    # SPOT TRADING
    # ---------------------------------------------------------------------------------------------

    # For SPOT market
    if market == "SPOT":

        # Get all user Spot wallet assets
        assets = client.get_account()['balances']

        # Check all open Spot orders
        orders = client.get_open_orders(symbol=symbol)

        # Cancel all orders
        # That is done to get rid of the last stop-loss
        # Don't place limit orders on the same currency pair with a bot active, orders will get canceled!
        for order in orders:

            # Cancel all orders 1 by 1
            try:
                result = client.cancel_order(symbol=symbol, orderId=order['orderId'])

            # Error
            except BinanceAPIException as e:
                send_report(str(e) + "During Spot Stop Loss Cancel")

        # Check how much of both currencies are available in the Spot wallet
        for asset in assets:
            if asset['asset'] == asset_name:
                quantity = float(asset['free'])
            if asset['asset'] == base_name:
                base = float(asset['free'])

        # Run the Spot market order function
        order = spot_order(side, quantity, base, symbol, precision, equity, min_quantity, market, stop, stop_diff)

        # Successful trade
        if order:

            # Write the pair and market to the last trade txt file
            list_to_file([symbol, base_name, market])

            return {
                "code": "success",
                "message": "order completed"
            }

        # Failed trade
        else:
            print("order failed!")
            send_report("Error")

            return {
                "code": "error",
                "message": "order failed"
            }

    # ---------------------------------------------------------------------------------------------
    # MARGIN TRADING
    # ---------------------------------------------------------------------------------------------

    # If Margin is chosen, but the pair doesn't allow margin trading, quit
    elif not symbol_info["isMarginTradingAllowed"]:
        # Can't trade margin
        print("margin unavailable!")
        send_report("Error")

        return {
            "code": "error",
            "message": "margin unavailable for this pair"
        }

    # Check all user Margin assets

    # Isolated
    if isolated:
        assets = client.get_isolated_margin_account()['assets']

    # Cross
    else:
        assets = client.get_margin_account()['userAssets']

    # Check for open orders
    orders = client.get_open_margin_orders(symbol=symbol, isIsolated=isolated)

    # Cancel all orders
    for order in orders:
        try:
            result = client.cancel_margin_order(symbol=symbol, orderId=order['orderId'], isIsolated=isolated)

        # Error
        except BinanceAPIException as e:
            send_report(str(e) + "During Margin Stop Limit Cancel")

    # ---------------------------------------------------------------------------------------------
    # MARGIN LONG
    # ---------------------------------------------------------------------------------------------
    # CLOSING SHORT, REPAYING THE LEVERAGE, GOING LONG
    # .............................................................................................

    # If Going Long
    if side == "BUY":

        # Check information about assets
        for asset in assets:

            # Isolated
            if isolated:
                # Pair
                if asset['symbol'] == symbol:
                    # Borrowed
                    loan_amount = float(asset['baseAsset']['borrowed'])
                    # Base currency amount
                    base = float(asset['quoteAsset']['free'])
                    # Margin ratio
                    margin_ratio = float(asset['marginRatio'])
                    break

            # Cross
            else:
                if asset['asset'] == asset_name:
                    # Borrowed
                    loan_amount = float(asset['borrowed'])
                if asset['asset'] == base_name:
                    # Base currency amount
                    base = float(asset['free'])
                # Cross always has max leverage x3
                margin_ratio = 3

        # Get asset price
        price = float(client.get_margin_price_index(symbol=symbol)['price'])

        # Calculate the amount you can buy
        quantity = base * equity / price

        # Execute market buy order, to exit previous short trade
        # And Start a long without leverage
        order_response = margin_order(side, quantity, symbol, precision, min_quantity, market,
                                      stop=stop, stop_diff=stop_diff, loan=loan_amount, isIsolated=isolated)

        # If order successful
        if order_response:

            # Repay the debt, if any
            if loan_amount > 0:
                if not repay_loan(asset_name, loan_amount, symbol, isolated):

                    # Failed to repay
                    print("repay failed!")
                    send_report("Error")

                    return {
                        "code": "error",
                        "message": "repay failed"
                    }

            # .............................................................................................
            # LONG WITH LEVERAGE
            # .............................................................................................

            # If Leverage is enabled
            if leverage > 0:

                # Check if leverage is smaller than available ratio
                # If not, set leverage to max
                if leverage > margin_ratio:
                    leverage = margin_ratio

                # Calculate the remaining base currency amount after repaying the debt
                base = quantity - loan_amount

                # Calculate the leverage amount
                loan = base * equity * leverage

                # Get the leverage
                take_loan(base_name, loan, symbol, isolated)

                # Enter a Long trade with the leveraged currency
                order_response = margin_order(side, loan, symbol, precision, min_quantity, market,
                                              stop=stop, stop_diff=stop_diff, loan=0, isIsolated=isolated)

                # Error
                if not order_response:
                    # Failed order
                    print("order failed!")
                    send_report("Error")

                    return {
                        "code": "error",
                        "message": "order failed"
                    }

            # Successful trade

            # Add the pair and market type to the last trade txt file
            list_to_file([symbol, base_name, market])

            return {
                "code": "success",
                "message": "order completed"
            }

        else:
            # Failed order
            print("order failed!")
            send_report("Error")

            return {
                "code": "error",
                "message": "order failed"
            }

    # ---------------------------------------------------------------------------------------------
    # MARGIN SHORT
    # ---------------------------------------------------------------------------------------------
    # CLOSING LONG
    # .............................................................................................

    # If going Long
    elif side == "SELL":

        # Check information about assets
        for asset in assets:

            # Isolated
            if isolated:
                # Pair
                if asset['symbol'] == symbol:
                    # Available amount
                    amount = float(asset['baseAsset']['free'])
                    # Margin ratio
                    margin_ratio = float(asset['marginRatio'])-1
                    break

            # Cross
            else:
                if asset['asset'] == asset_name:
                    # Available amount
                    amount = float(asset['free'])
                    break
                margin_ratio = 3

        # Execute a market sell order, to close the previous long position
        order_response = margin_order(side, amount, symbol, precision, min_quantity, market)

        # Failed order
        if not order_response:
            print("order failed!")

            return {
                "code": "error",
                "message": "order failed"
                }

        # .............................................................................................
        # REPAYING THE LEVERAGE
        # .............................................................................................

        # Check how much base currency was gained in the trade
        for asset in assets:

            # Isolated
            if isolated:
                if asset['symbol'] == symbol:
                    # Available amount
                    base = float(asset['quoteAsset']['free'])
                    # Borrowed amount
                    loan_amount = float(asset['quoteAsset']['borrowed'])
                    break

            # Cross
            else:
                if asset['asset'] == base_name:
                    # Available amount
                    base = float(asset['free'])
                    # Borrowed amount
                    loan_amount = float(asset['borrowed'])
                    break

        base =- loan_amount
        repay = repay_loan(base_name, loan_amount, symbol, isolated)

        # .............................................................................................
        # LOANING LEVERAGE
        # .............................................................................................

        # If leverage is enabled
        if leverage > 0:

            # For shorting, the leverage available is -1x
            # Check if it's less than the max amount
            if leverage > margin_ratio-1:
                # Else, set it to max
                leverage = margin_ratio-1

        # Get asset price
        price = float(client.get_margin_price_index(symbol=symbol)['price'])

        # Calculate how much of the asset will you short
        # On the left side the standard short, on the right side, with extra leverage, if any
        amount = (base * equity + base * equity * leverage) / price

        # Take a loan for the same amount as sold
        transfer = take_loan(asset_name, amount, symbol, isolated)

        # Loan failed
        if not transfer:
            print("loan failed!")
            send_report(str(e) + "During Margin Stop Limit")

            return {
                "code": "error",
                "message": "loan failed"
            }

        # .............................................................................................
        # SHORTING THE MARKET
        # .............................................................................................

        # Sell the loan to short the market
        # Later it will be bought and repaid for a lower price
        order_response = margin_order(side, amount, symbol, precision,
                                      min_quantity, market, stop=stop, stop_diff=stop_diff)

        # Successful trade
        if order_response:

            list_to_file([symbol, base_name, market])

            print("success")

            return {
                "code": "success",
                "message": "order completed"
            }

        else:
            # Error :(
            print("order failed!")
            send_report(str(e) + "During Margin Stop Limit")

            return {
                "code": "error",
                "message": "order failed"
            }
    else:
        # No BUY or SELL found in signal
        print("incorrect order action")

        send_report("Error")

        return {
            "code": "error",
            "message": "incorrect order action"
        }

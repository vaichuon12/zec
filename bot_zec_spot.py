# bot_bitget_sol.py
# Scalping fast: SOL/USDT on Bitget (ccxt) - "B1" config (many trades, TP gross 0.25%)
# REQUIREMENTS: pip install ccxt python-dotenv
# WARNING: KEEP dry_run=True while testing. Fill API keys before switching dry_run=False.

import ccxt
import time
import math
import os
from datetime import datetime, timezone
from dotenv import load_dotenv

load_dotenv()  # optional: if you store keys in .env

# ---------------- CONFIG ----------------
SYMBOL = "SOL/USDT"
EXCHANGE_ID = "bitget"

# Trading behaviour
DRY_RUN = True                 # True = simulate only, False = live
CHECK_INTERVAL = 0.4           # seconds between checks (fast for many trades)
DUMP_THRESHOLD = 0.0012       # 0.12% quick drop -> buy (aggressive, many-entry)
PROFIT_TARGET_GROSS = 0.0025  # 0.25% gross target to consider selling
STOP_LOSS = 0.0075            # 0.75% hard stop (protect capital) - adjust if wanted

# Fees (assumed taker; adjust if you know your fee tier)
FEE_PER_SIDE = 0.001          # 0.1% per side by default => total 0.002

# Minimum notional to avoid tiny orders (exchange min cost)
MIN_NOTIONAL_USDT = 5.0

# Safety: fraction of USDT to use per buy (keep a tiny buffer for fees/precision)
USE_USDT_FRAC = 0.98

# ---------------- API KEYS (put into environment or paste directly) ----------------
API_KEY = os.getenv("bg_aef3f1fd1131d53a300900a583720bfb") or "bg_aef3f1fd1131d53a300900a583720bfb"       # or paste your key here (not recommended)
API_SECRET = os.getenv("c4d69f7e3122eb858b45c9f2a7a30540e7e84597cae3103d3b189d9556b70da7") or "c4d69f7e3122eb858b45c9f2a7a30540e7e84597cae3103d3b189d9556b70da7" # or paste your secret here
API_PASSPHRASE = os.getenv("12345678") or "12345678"  # usually not needed for bitget

# ---------------- SETUP EXCHANGE ----------------
exchange = getattr(ccxt, EXCHANGE_ID)({
    "apiKey": API_KEY,
    "secret": API_SECRET,
    "password": API_PASSPHRASE,
    "enableRateLimit": True,
    "options": {"defaultType": "spot"}
})
exchange.load_markets()

market = exchange.markets.get(SYMBOL)
if not market:
    raise SystemExit(f"Market {SYMBOL} not found on {EXCHANGE_ID}")

base_asset = SYMBOL.split("/")[0]
quote_asset = SYMBOL.split("/")[1]

# ---------------- Helpers ----------------
def nowiso():
    return datetime.now(timezone.utc).isoformat()

def log(*args):
    print(f"[{nowiso()}]", *args)

def safe_call(fn, retries=3, delay=1.0):
    for i in range(retries):
        try:
            return fn()
        except Exception as e:
            log("⚠ API retry", i+1, "error:", e)
            time.sleep(delay)
    return None

def amount_to_precision(sym, amt):
    try:
        a = exchange.amount_to_precision(sym, amt)
        return float(a)
    except Exception:
        prec = market.get("precision", {}).get("amount")
        if prec is not None:
            factor = 10 ** prec
            return math.floor(amt * factor) / factor
        return float(round(amt, 8))

def price_now():
    t = safe_call(lambda: exchange.fetch_ticker(SYMBOL))
    if not t:
        return None
    return float(t.get("last") or t.get("close"))

def fetch_balances():
    b = safe_call(lambda: exchange.fetch_balance())
    if not b:
        return None, None
    free = b.get("free", {})
    usdt_free = float(free.get(quote_asset, 0) or 0)
    base_free = float(free.get(base_asset, 0) or 0)
    return usdt_free, base_free

def extract_fill_price(order):
    if not order: return None
    avg = order.get("average")
    if avg:
        try: return float(avg)
        except: pass
    info = order.get("info") or {}
    fills = order.get("fills") or info.get("fills")
    if fills and isinstance(fills, list) and len(fills) > 0:
        p = fills[0].get("price") or fills[0].get("priceStr")
        try: return float(p)
        except: pass
    p = order.get("price")
    try: return float(p)
    except: return None

# ---------------- STATE ----------------
last_tick_price = price_now()
if last_tick_price is None:
    raise SystemExit("Cannot fetch initial price.")

in_position = False
entry_price = None
position_qty = 0.0

log("START scalper B1", SYMBOL, "dry_run=", DRY_RUN)
log("CONFIG: DUMP_TH=", DUMP_THRESHOLD, "TP_gross=", PROFIT_TARGET_GROSS if 'PROFIT_TARGET_GROSS' in globals() else PROFIT_TARGET_GROSS)

# (note: keep small sleep before main loop to let API warm up)
time.sleep(0.2)

# ---------------- MAIN LOOP ----------------
while True:
    try:
        price = price_now()
        if price is None:
            time.sleep(CHECK_INTERVAL)
            continue

        # compute immediate % change vs last tick (quick micro-dump detector)
        change = (price - last_tick_price) / last_tick_price
        last_tick_price = price  # update for next tick

        usdt_free, base_free = fetch_balances()
        if usdt_free is None:
            time.sleep(CHECK_INTERVAL)
            continue

        log(f"Price={price:.6f} change={change*100:.3f}% USDT_free={usdt_free:.4f} {base_asset}_free={base_free:.6f}")

        # ---------- BUY (aggressive many-entry logic) ----------
        if (not in_position) and usdt_free >= MIN_NOTIONAL_USDT:
            # if quick drop beyond threshold => buy all-in (approx)
            if change <= -DUMP_THRESHOLD:
                # use almost all USDT but keep tiny buffer
                cap = usdt_free * USE_USDT_FRAC
                raw_amount = cap / price
                amount = amount_to_precision(SYMBOL, raw_amount)
                notional = amount * price
                if notional < MIN_NOTIONAL_USDT:
                    log("Computed notional too small:", notional, "skip buy")
                else:
                    log(f"BUY condition met: change={change*100:.3f}%, buying amount={amount} (notional {notional:.4f})")
                    if DRY_RUN:
                        log("(dry_run) Simulated BUY:", amount, "at", price)
                        fill_price = price
                    else:
                        order = safe_call(lambda: exchange.create_market_buy_order(SYMBOL, amount))
                        log("buy order:", order)
                        fill_price = extract_fill_price(order) or price
                    # set position
                    in_position = True
                    entry_price = float(fill_price)
                    position_qty = amount
                    log("ENTER position entry_price=", entry_price, "qty=", position_qty)
                    # short pause after buy
                    time.sleep(0.6)
                    continue

        # ---------- SELL (take profit or stoploss) ----------
        if in_position and position_qty > 0:
            gross_change = (price - entry_price) / entry_price  # e.g. 0.003 => 0.3%
            total_fee = FEE_PER_SIDE * 2.0
            net_change = gross_change - total_fee
            log(f"In pos entry={entry_price:.6f} gross={gross_change*100:.3f}% net(after fees)={net_change*100:.3f}%")

            # SELL when gross reaches profit target (we accept gross target so net > 0)
            if gross_change >= PROFIT_TARGET_GROSS:
                log("TP reached (gross). SELLING ALL")
                if DRY_RUN:
                    log("(dry_run) Simulated SELL:", position_qty, "at", price)
                else:
                    order = safe_call(lambda: exchange.create_market_sell_order(SYMBOL, position_qty))
                    log("sell order:", order)
                # reset
                in_position = False
                entry_price = None
                position_qty = 0.0
                # small cooldown
                time.sleep(0.6)
                continue

            # SELL if net_change already positive above small margin (optionally)
            # (This branch optional; left commented for clarity)
            # if net_change >= 0.0005:
            #     log("Net positive threshold reached -> SELL")
            #     ...

            # STOP LOSS (hard)
            if gross_change <= -STOP_LOSS:
                log("STOP-LOSS hit -> SELL ALL")
                if DRY_RUN:
                    log("(dry_run) Simulated STOP SELL:", position_qty, "at", price)
                else:
                    order = safe_call(lambda: exchange.create_market_sell_order(SYMBOL, position_qty))
                    log("stop sell order:", order)
                in_position = False
                entry_price = None
                position_qty = 0.0
                time.sleep(0.6)
                continue

        # loop sleep
        time.sleep(CHECK_INTERVAL)

    except KeyboardInterrupt:
        log("KeyboardInterrupt — exiting")
        break
    except Exception as e:
        log("ERROR main loop:", e)
        time.sleep(1.0)

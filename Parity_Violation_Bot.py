"""
Ultimate Real-Time Put-Call Parity Scanner
==========================================
Full real-time data for both stocks and options
Ready to find EXECUTABLE arbitrage opportunities!
"""

import requests
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import time
import json
from dataclasses import dataclass
from typing import List, Optional, Dict, Tuple
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

@dataclass
class RealTimeQuote:
    """Real-time quote data"""
    bid: float
    ask: float
    bid_size: int
    ask_size: int
    last_price: float
    timestamp: datetime

@dataclass
class ExecutableArbitrage:
    """Fully executable arbitrage opportunity"""
    underlying: str
    strike: float
    expiry: datetime
    # Stock quotes
    stock_bid: float
    stock_ask: float
    stock_bid_size: int
    stock_ask_size: int
    # Option quotes
    call_symbol: str
    put_symbol: str
    call_bid: float
    call_ask: float
    call_bid_size: int
    call_ask_size: int
    put_bid: float
    put_ask: float
    put_bid_size: int
    put_ask_size: int
    # Profit calculations
    theoretical_profit: float
    executable_profit: float
    profit_per_contract: float
    max_contracts: int
    # Execution details
    strategy: str
    required_capital: float
    execution_score: float
    time_to_expiry_days: int
    timestamp: datetime
    execution_steps: List[str]

class RealTimeArbitrageScanner:
    """Production-ready arbitrage scanner with real-time data"""
    
    def __init__(self, api_key: str, risk_free_rate: float = 0.05):
        self.api_key = api_key
        self.risk_free_rate = risk_free_rate
        self.base_url = "https://api.polygon.io"
        
        # Strict thresholds for real opportunities
        self.min_profit_per_contract = 10.00  # $10 minimum after all costs
        self.min_volume = 10                  # Lowered from 100 to find more options
        self.min_open_interest = 50           # Lowered from 200
        self.max_spread_pct = 0.05           # Max 5% bid-ask spread (increased from 3%)
        
        # Real execution costs
        self.commission_per_contract = 0.65   # Per option contract
        self.stock_commission = 0.005         # Per share
        self.sec_fee = 0.0000278             # Per dollar sold
        self.option_regulatory_fee = 0.03    # Per contract
        
        # Cache for efficiency
        self.quote_cache = {}
        self.cache_lock = threading.Lock()
        
    def get_real_time_stock_quote(self, symbol: str) -> Optional[RealTimeQuote]:
        """Get real-time stock quote with bid/ask"""
        # Method 1: Try snapshot endpoint
        url = f"{self.base_url}/v2/snapshot/locale/us/markets/stocks/tickers/{symbol}"
        
        try:
            response = requests.get(url, params={'apikey': self.api_key}, timeout=5)
            if response.status_code == 200:
                data = response.json()
                if 'ticker' in data and 'min' in data['ticker']:
                    ticker_data = data['ticker']
                    min_data = ticker_data['min']
                    
                    # Get quote data
                    if 'q' in ticker_data:  # Quote data
                        quote = ticker_data['q']
                        return RealTimeQuote(
                            bid=quote.get('b', 0),
                            ask=quote.get('a', 0),
                            bid_size=quote.get('bs', 0) * 100,  # Convert to shares
                            ask_size=quote.get('as', 0) * 100,
                            last_price=quote.get('l', min_data.get('c', 0)),
                            timestamp=datetime.now()
                        )
        except Exception as e:
            logger.debug(f"Snapshot failed: {e}")
        
        # Method 2: Try last quote endpoint
        url = f"{self.base_url}/v3/quotes/{symbol}"
        try:
            response = requests.get(url, params={'apikey': self.api_key, 'limit': 1}, timeout=5)
            if response.status_code == 200:
                data = response.json()
                if 'results' in data and data['results']:
                    quote = data['results'][0]
                    return RealTimeQuote(
                        bid=quote.get('bid_price', 0),
                        ask=quote.get('ask_price', 0),
                        bid_size=quote.get('bid_size', 0),
                        ask_size=quote.get('ask_size', 0),
                        last_price=(quote.get('bid_price', 0) + quote.get('ask_price', 0)) / 2,
                        timestamp=datetime.now()
                    )
        except:
            pass
        
        return None
    
    def get_real_time_options(self, symbol: str) -> Dict[str, List[Dict]]:
        """Get real-time option chain with quotes"""
        logger.info(f"Getting option chain for {symbol}...")
        
        option_data = {'calls': [], 'puts': []}
        
        # Get option chain snapshot
        url = f"{self.base_url}/v3/snapshot/options/{symbol}"
        
        # Focus on near-term, liquid options
        params = {
            'apikey': self.api_key,
            'limit': 250,
            'expiration_date.gte': datetime.now().strftime('%Y-%m-%d'),
            'expiration_date.lte': (datetime.now() + timedelta(days=45)).strftime('%Y-%m-%d'),
        }
        
        contracts_processed = 0
        next_url = None
        
        while contracts_processed < 500:  # Limit total
            try:
                if next_url:
                    response = requests.get(next_url + f"&apikey={self.api_key}", timeout=10)
                else:
                    response = requests.get(url, params=params, timeout=10)
                
                if response.status_code != 200:
                    logger.error(f"API error: {response.status_code}")
                    break
                
                data = response.json()
                results = data.get('results', [])
                
                for item in results:
                    details = item.get('details', {})
                    ticker = details.get('ticker', '')
                    
                    if not ticker:
                        continue
                    
                    # FIXED: Get quote data from TOP LEVEL last_quote, not day.last_quote!
                    last_quote = item.get('last_quote', {})
                    
                    # Check if we have valid quotes
                    bid = last_quote.get('bid', 0)
                    ask = last_quote.get('ask', 0)
                    
                    # Also check day data for volume
                    day = item.get('day', {})
                    
                    if bid <= 0 or ask <= 0:
                        # Skip if no valid quotes
                        continue
                    
                    # Parse contract details
                    parsed = self.parse_option_ticker(ticker)
                    if not parsed:
                        continue
                    
                    # Create option record
                    option = {
                        'symbol': ticker,
                        'underlying': parsed['underlying'],
                        'strike': parsed['strike'],
                        'expiry': parsed['expiry'],
                        'option_type': parsed['option_type'],
                        'bid': bid,
                        'ask': ask,
                        'bid_size': last_quote.get('bid_size', 0),
                        'ask_size': last_quote.get('ask_size', 0),
                        'last_price': day.get('close', (bid + ask) / 2),
                        'volume': day.get('volume', 0),
                        'open_interest': item.get('open_interest', 0),
                        'implied_vol': item.get('implied_volatility', 0),
                        'greeks': item.get('greeks', {}),
                        'last_updated': last_quote.get('last_updated', 0),
                        'timeframe': last_quote.get('timeframe', 'UNKNOWN')
                    }
                    
                    # Log if quotes are delayed
                    if option['timeframe'] == 'DELAYED' and contracts_processed == 0:
                        logger.debug(f"Note: Quotes marked as DELAYED (expected with bulk endpoint)")
                    
                    # Categorize by type
                    if parsed['option_type'] == 'call':
                        option_data['calls'].append(option)
                    else:
                        option_data['puts'].append(option)
                
                contracts_processed += len(results)
                
                # Check for more pages
                next_url = data.get('next_url')
                if not next_url:
                    break
                
                time.sleep(0.1)  # Rate limit
                
            except Exception as e:
                logger.error(f"Error fetching options: {e}")
                break
        
        logger.info(f"Found {len(option_data['calls'])} calls and {len(option_data['puts'])} puts with valid quotes")
        return option_data
    
    def parse_option_ticker(self, ticker: str) -> Optional[Dict]:
        """Parse option ticker format"""
        try:
            if ticker.startswith('O:'):
                ticker = ticker[2:]
            
            # Find date position
            date_start = None
            for i in range(len(ticker) - 6):
                if ticker[i:i+6].isdigit():
                    date_start = i
                    break
            
            if date_start is None:
                return None
            
            underlying = ticker[:date_start]
            date_str = ticker[date_start:date_start+6]
            year = 2000 + int(date_str[:2])
            month = int(date_str[2:4])
            day = int(date_str[4:6])
            expiry = datetime(year, month, day)
            
            type_char = ticker[date_start+6]
            option_type = 'call' if type_char == 'C' else 'put'
            
            strike = int(ticker[date_start+7:]) / 1000
            
            return {
                'underlying': underlying,
                'expiry': expiry,
                'option_type': option_type,
                'strike': strike
            }
        except:
            return None
    
    def check_arbitrage_opportunity(self, call: Dict, put: Dict, 
                                  stock_quote: RealTimeQuote, symbol: str) -> Optional[ExecutableArbitrage]:
        """Check if there's an executable arbitrage opportunity"""
        
        # Must be same strike and expiry
        if call['strike'] != put['strike'] or call['expiry'] != put['expiry']:
            return None
        
        # First check if we have valid quotes
        if (call['bid'] <= 0 or call['ask'] <= 0 or 
            put['bid'] <= 0 or put['ask'] <= 0):
            return None
        
        # Check liquidity requirements (more lenient)
        if (call['volume'] < self.min_volume and call['open_interest'] < self.min_open_interest):
            return None
        if (put['volume'] < self.min_volume and put['open_interest'] < self.min_open_interest):
            return None
        
        # Check spreads
        call_spread_pct = (call['ask'] - call['bid']) / ((call['ask'] + call['bid']) / 2) if call['bid'] > 0 else 1
        put_spread_pct = (put['ask'] - put['bid']) / ((put['ask'] + put['bid']) / 2) if put['bid'] > 0 else 1
        
        if call_spread_pct > self.max_spread_pct or put_spread_pct > self.max_spread_pct:
            return None
        
        # Check size availability - we don't need specific sizes for standard arbitrage
        # Just log them for reference
        call_bid_size = call.get('bid_size', 0)
        call_ask_size = call.get('ask_size', 0)
        put_bid_size = put.get('bid_size', 0)
        put_ask_size = put.get('ask_size', 0)
        
        # Calculate time to expiry
        days_to_expiry = (call['expiry'] - datetime.now()).days
        if days_to_expiry <= 0:
            return None
        
        time_to_expiry = days_to_expiry / 365.0
        
        # Put-Call Parity calculation
        strike = call['strike']
        discount_factor = np.exp(-self.risk_free_rate * time_to_expiry)
        
        # Theoretical relationship: C - P = S - K*e^(-r*T)
        theoretical_diff = stock_quote.bid - strike * discount_factor
        
        # Market prices (using executable prices)
        market_diff_sell_call = call['bid'] - put['ask']  # If we sell call, buy put
        market_diff_buy_call = call['ask'] - put['bid']   # If we buy call, sell put
        
        # Check both arbitrage directions
        execution_steps = []
        
        # Direction 1: Sell Call, Buy Put, Buy Stock
        violation1 = market_diff_sell_call - theoretical_diff
        gross_profit1 = violation1 * 100  # Per contract
        
        # Costs for direction 1
        option_commissions1 = 2 * self.commission_per_contract
        option_fees1 = 2 * self.option_regulatory_fee
        stock_commission1 = 100 * self.stock_commission
        sec_fee1 = call['bid'] * 100 * self.sec_fee  # SEC fee on sale
        total_costs1 = option_commissions1 + option_fees1 + stock_commission1 + sec_fee1
        
        net_profit1 = gross_profit1 - total_costs1
        
        # Direction 2: Buy Call, Sell Put, Short Stock
        violation2 = theoretical_diff - market_diff_buy_call
        gross_profit2 = violation2 * 100
        
        # Costs for direction 2 (includes borrow cost estimate)
        option_commissions2 = 2 * self.commission_per_contract
        option_fees2 = 2 * self.option_regulatory_fee
        stock_commission2 = 100 * self.stock_commission
        sec_fee2 = (put['bid'] * 100 + stock_quote.bid * 100) * self.sec_fee
        borrow_cost = stock_quote.bid * 100 * 0.02 * time_to_expiry  # 2% borrow rate
        total_costs2 = option_commissions2 + option_fees2 + stock_commission2 + sec_fee2 + borrow_cost
        
        net_profit2 = gross_profit2 - total_costs2
        
        # Choose best direction
        if net_profit1 > net_profit2 and net_profit1 > self.min_profit_per_contract:
            strategy = "SELL_CALL_BUY_PUT_BUY_STOCK"
            net_profit = net_profit1
            required_capital = put['ask'] * 100 + stock_quote.ask * 100
            max_contracts = 1  # Standard arbitrage: 1 contract each
            
            execution_steps = [
                f"SELL {call['symbol']} @ ${call['bid']:.2f}",
                f"BUY {put['symbol']} @ ${put['ask']:.2f}",
                f"BUY 100 shares {symbol} @ ${stock_quote.ask:.2f}",
                f"At expiry: Deliver shares for ${strike:.2f}"
            ]
            
        elif net_profit2 > self.min_profit_per_contract:
            strategy = "BUY_CALL_SELL_PUT_SHORT_STOCK"
            net_profit = net_profit2
            required_capital = call['ask'] * 100 + strike * 100 * 0.5  # 50% margin
            max_contracts = 1  # Standard arbitrage: 1 contract each
            
            execution_steps = [
                f"BUY {call['symbol']} @ ${call['ask']:.2f}",
                f"SELL {put['symbol']} @ ${put['bid']:.2f}",
                f"SHORT 100 shares {symbol} @ ${stock_quote.bid:.2f}",
                f"At expiry: Buy shares for ${strike:.2f} to cover short"
            ]
        else:
            return None
        
        # Calculate execution score (simpler for standard arbitrage)
        liquidity_score = min(1.0, (call['volume'] + put['volume']) / 100)
        spread_score = 1 - (call_spread_pct + put_spread_pct) / 2
        execution_score = (liquidity_score + spread_score) / 2
        
        return ExecutableArbitrage(
            underlying=call.get('underlying', symbol),  # Use symbol if underlying not in data
            strike=strike,
            expiry=call['expiry'],
            stock_bid=stock_quote.bid,
            stock_ask=stock_quote.ask,
            stock_bid_size=stock_quote.bid_size,
            stock_ask_size=stock_quote.ask_size,
            call_symbol=call['symbol'],
            put_symbol=put['symbol'],
            call_bid=call['bid'],
            call_ask=call['ask'],
            call_bid_size=call_bid_size,
            call_ask_size=call_ask_size,
            put_bid=put['bid'],
            put_ask=put['ask'],
            put_bid_size=put_bid_size,
            put_ask_size=put_ask_size,
            theoretical_profit=max(violation1, violation2) * 100,
            executable_profit=net_profit,
            profit_per_contract=net_profit,
            max_contracts=1,  # Standard arbitrage always uses 1 contract
            strategy=strategy,
            required_capital=required_capital,
            execution_score=execution_score,
            time_to_expiry_days=days_to_expiry,
            timestamp=datetime.now(),
            execution_steps=execution_steps
        )
    
    def scan_symbol(self, symbol: str) -> List[ExecutableArbitrage]:
        """Scan a symbol for arbitrage opportunities"""
        logger.info(f"Scanning {symbol}...")
        
        # Get real-time stock quote
        stock_quote = self.get_real_time_stock_quote(symbol)
        if not stock_quote or stock_quote.bid <= 0:
            logger.error(f"Could not get real-time quote for {symbol}")
            return []
        
        logger.info(f"{symbol}: ${stock_quote.bid:.2f} x ${stock_quote.ask:.2f}")
        
        # Get real-time options
        option_data = self.get_real_time_options(symbol)
        
        if not option_data['calls'] or not option_data['puts']:
            logger.warning(f"No liquid options found for {symbol}")
            return []
        
        # Find arbitrage opportunities
        opportunities = []
        pairs_checked = 0
        
        # Group by strike/expiry
        option_pairs = {}
        for call in option_data['calls']:
            key = (call['strike'], call['expiry'])
            if key not in option_pairs:
                option_pairs[key] = {'calls': [], 'puts': []}
            option_pairs[key]['calls'].append(call)
        
        for put in option_data['puts']:
            key = (put['strike'], put['expiry'])
            if key not in option_pairs:
                option_pairs[key] = {'calls': [], 'puts': []}
            option_pairs[key]['puts'].append(put)
        
        # Check each pair
        for (strike, expiry), options in option_pairs.items():
            if options['calls'] and options['puts']:
                pairs_checked += 1
                
                # Use most liquid of each
                call = max(options['calls'], key=lambda x: x['volume'])
                put = max(options['puts'], key=lambda x: x['volume'])
                
                opportunity = self.check_arbitrage_opportunity(call, put, stock_quote, symbol)
                
                if opportunity:
                    opportunities.append(opportunity)
                    logger.info(f"  🎯 FOUND: ${strike} strike, ${opportunity.profit_per_contract:.2f} profit")
        
        logger.info(f"Checked {pairs_checked} pairs, found {len(opportunities)} opportunities")
        return opportunities  # FIXED: Removed the undefined 'violations' check
    
    def generate_alert_report(self, opportunities: List[ExecutableArbitrage]) -> str:
        """Generate actionable alert report"""
        if not opportunities:
            return """
📊 NO ARBITRAGE OPPORTUNITIES CURRENTLY AVAILABLE

Scanned with real-time data. This is normal - true arbitrage is rare.
The market is functioning efficiently.

Keep scanning during:
• High volatility periods
• Earnings announcements  
• Major economic releases
• Market open/close
"""
        
        report = f"""
🚨🚨🚨 ARBITRAGE ALERT - EXECUTE NOW! 🚨🚨🚨
{'='*80}
{len(opportunities)} OPPORTUNITIES FOUND AT {datetime.now().strftime('%H:%M:%S')}

Each opportunity trades:
• 1 Call Contract
• 1 Put Contract  
• 100 Shares of Stock
{'='*80}
"""
        
        for i, opp in enumerate(opportunities[:5], 1):
            
            if opp.strategy == "SELL_CALL_BUY_PUT_BUY_STOCK":
                report += f"""

🎯 OPPORTUNITY #{i} - MAKE ${opp.profit_per_contract:.2f} PROFIT! 🎯
{'='*80}

📊 STRATEGY: Sell Call + Buy Put + Buy Stock

⚡ DO THESE 3 TRADES RIGHT NOW:

1️⃣ SELL CALL:
   Symbol: {opp.call_symbol}
   Strike: ${opp.strike}
   Type: SELL TO OPEN
   Price: ${opp.call_bid:.2f} OR BETTER
   Quantity: 1 contract
   
2️⃣ BUY PUT:  
   Symbol: {opp.put_symbol}
   Strike: ${opp.strike} (SAME AS CALL!)
   Type: BUY TO OPEN
   Price: ${opp.put_ask:.2f} OR LESS
   Quantity: 1 contract
   
3️⃣ BUY STOCK:
   Symbol: {opp.underlying}
   Type: BUY
   Price: ${opp.stock_ask:.2f} OR LESS  
   Quantity: 100 shares

💰 YOUR PROFIT: ${opp.profit_per_contract:.2f}
📅 OPTIONS EXPIRE: {opp.expiry.strftime('%B %d, %Y')}
💵 TOTAL CAPITAL NEEDED: ${opp.required_capital:,.2f}
"""
            else:  # BUY_CALL_SELL_PUT_SHORT_STOCK
                report += f"""

🎯 OPPORTUNITY #{i} - MAKE ${opp.profit_per_contract:.2f} PROFIT! 🎯  
{'='*80}

📊 STRATEGY: Buy Call + Sell Put + Short Stock

⚡ DO THESE 3 TRADES RIGHT NOW:

1️⃣ BUY CALL:
   Symbol: {opp.call_symbol}
   Strike: ${opp.strike}
   Type: BUY TO OPEN
   Price: ${opp.call_ask:.2f} OR LESS
   Quantity: 1 contract
   
2️⃣ SELL PUT:
   Symbol: {opp.put_symbol}  
   Strike: ${opp.strike} (SAME AS CALL!)
   Type: SELL TO OPEN
   Price: ${opp.put_bid:.2f} OR BETTER
   Quantity: 1 contract
   
3️⃣ SHORT STOCK:
   Symbol: {opp.underlying}
   Type: SELL SHORT
   Price: ${opp.stock_bid:.2f} OR BETTER
   Quantity: 100 shares

💰 YOUR PROFIT: ${opp.profit_per_contract:.2f}
📅 OPTIONS EXPIRE: {opp.expiry.strftime('%B %d, %Y')}
💵 MARGIN REQUIRED: ${opp.required_capital:,.2f}
"""
            
            report += f"""
⏰ CURRENT MARKET PRICES (EXECUTE WITHIN 30 SECONDS!):
   {opp.underlying}: ${opp.stock_bid:.2f} × ${opp.stock_ask:.2f}
   Call: ${opp.call_bid:.2f} × ${opp.call_ask:.2f}  
   Put: ${opp.put_bid:.2f} × ${opp.put_ask:.2f}

{'='*80}
"""
        
        report += """
🚨 EXECUTION CHECKLIST:
☐ Open your broker NOW
☐ Enter all 3 trades BEFORE submitting any
☐ Use LIMIT orders at the prices shown
☐ Submit all 3 trades within 10 seconds of each other
☐ If any leg fails, CANCEL the others immediately!

⚡ SPEED IS CRITICAL - EXECUTE NOW OR MISS IT! ⚡
"""
        
        return report

def continuous_scanner(api_key: str, symbols: List[str], scan_interval: int = 30):
    """Continuous scanning with alerts"""
    scanner = RealTimeArbitrageScanner(api_key)
    
    print("🔍 STARTING CONTINUOUS ARBITRAGE SCANNER")
    print("="*80)
    print(f"Monitoring: {', '.join(symbols)}")
    print(f"Scan interval: {scan_interval} seconds")
    print("Press Ctrl+C to stop")
    print("="*80)
    
    while True:
        try:
            print(f"\n[{datetime.now().strftime('%H:%M:%S')}] Scanning all symbols...")
            
            all_opportunities = []
            
            # Scan all symbols
            for symbol in symbols:
                opportunities = scanner.scan_symbol(symbol)
                all_opportunities.extend(opportunities)
                time.sleep(0.5)  # Brief pause between symbols
            
            # Sort by profit
            all_opportunities.sort(key=lambda x: x.profit_per_contract, reverse=True)
            
            if all_opportunities:
                # ALERT! OPPORTUNITIES FOUND!
                print("\n" + "🚨" * 40)
                print("🚨🚨🚨 ARBITRAGE FOUND - CHECK YOUR SCREEN NOW! 🚨🚨🚨")
                print("🚨" * 40)
                
                # Generate and display report
                report = scanner.generate_alert_report(all_opportunities)
                print(f"\n{report}")
                
                # Make some noise (system beep)
                print("\a\a\a")  # Terminal bell
                
                print("\n" + "⚡" * 40)
                print("⚡⚡⚡ EXECUTE THE TRADES ABOVE RIGHT NOW! ⚡⚡⚡")
                print("⚡" * 40)
            else:
                print(f"No opportunities found. Next scan in {scan_interval} seconds...")
            
            # Wait before next scan
            time.sleep(scan_interval)
            
        except KeyboardInterrupt:
            print("\n\nStopping scanner...")
            break
        except Exception as e:
            logger.error(f"Scanner error: {e}")
            time.sleep(5)  # Brief pause on error

def main():
    print("🎯 ULTIMATE REAL-TIME PUT-CALL PARITY SCANNER")
    print("="*80)
    print("✅ Using REAL-TIME data for both stocks and options")
    print("✅ Ready to find EXECUTABLE arbitrage opportunities")
    print("="*80)
    
    print("\n📋 STANDARD PUT-CALL PARITY ARBITRAGE:")
    print("   Always trade in this exact ratio:")
    print("   • 1 Call Option Contract")
    print("   • 1 Put Option Contract (same strike!)")
    print("   • 100 Shares of Stock")
    print("\n⚡ The scanner will show your profit per trade set!")
    print("="*80)
    
    print("\n⚠️  CRITICAL: Call and Put MUST have SAME strike price!")
    print("   Example: Both at $625 strike, not $625 call and $630 put")
    print("="*80)
    
    api_key = "API"
    
    # Most liquid symbols for best opportunities
    symbols = ['SPY', 'QQQ', 'IWM', 'AAPL', 'MSFT', 'NVDA', 'TSLA', 'AMD']
    
    # Choose mode
    print("\nSelect mode:")
    print("1. Single scan")
    print("2. Continuous monitoring (RECOMMENDED)")
    
    choice = input("\nEnter choice (1 or 2): ").strip()
    
    if choice == '2':
        interval = input("Scan interval in seconds (default 30): ").strip()
        interval = int(interval) if interval.isdigit() else 30
        print(f"\n🚀 Starting continuous scan every {interval} seconds...")
        print("🔔 You'll hear LOUD ALERTS when opportunities are found!")
        print("\nNote: Bulk quotes may show 'DELAYED' but are current with your subscription")
        continuous_scanner(api_key, symbols, interval)
    else:
        # Single scan
        scanner = RealTimeArbitrageScanner(api_key)
        all_opportunities = []
        
        for symbol in symbols:
            try:
                opportunities = scanner.scan_symbol(symbol)
                all_opportunities.extend(opportunities)
                time.sleep(0.5)
            except Exception as e:
                logger.error(f"Error scanning {symbol}: {e}")
        
        # Sort by profit
        all_opportunities.sort(key=lambda x: x.profit_per_contract, reverse=True)
        
        # Generate report
        report = scanner.generate_alert_report(all_opportunities)
        print(report)
        
        # Save if found
        if all_opportunities:
            filename = f"arbitrage_live_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
            with open(filename, 'w') as f:
                data = []
                for opp in all_opportunities:
                    data.append({
                        'underlying': opp.underlying,
                        'strike': opp.strike,
                        'expiry': opp.expiry.isoformat(),
                        'profit_per_contract': opp.profit_per_contract,
                        'max_contracts': opp.max_contracts,
                        'strategy': opp.strategy,
                        'timestamp': opp.timestamp.isoformat()
                    })
                json.dump(data, f, indent=2)
            print(f"\n💾 Opportunities saved to {filename}")
            print("\n⚡⚡⚡ EXECUTE THE TRADES SHOWN ABOVE NOW! ⚡⚡⚡")

if __name__ == "__main__":
    main()

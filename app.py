import os
import time
import json
import logging
from datetime import datetime
from typing import Dict, Any, Optional, List
import requests
from flask import Flask, request, jsonify
from eth_account import Account
from hyperliquid.utils import constants
from hyperliquid.exchange import Exchange
from hyperliquid.info import Info

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

class HyperliquidBot:
    def __init__(self):
        self.private_key = os.getenv('HYPERLIQUID_PRIVATE_KEY')
        self.webhook_secret = os.getenv('WEBHOOK_SECRET', 'default_secret')
        self.use_testnet = os.getenv('USE_TESTNET', 'false').lower() == 'true'
        self.symbol = 'ETH'
        
        if not self.private_key:
            raise ValueError("HYPERLIQUID_PRIVATE_KEY environment variable is required")
        
        # Initialize account
        try:
            self.account = Account.from_key(self.private_key)
            self.wallet_address = self.account.address
            logger.info(f"Bot initialized for wallet: {self.wallet_address}")
        except Exception as e:
            logger.error(f"Failed to initialize account: {e}")
            raise
        
        # Initialize Hyperliquid clients with better error handling
        try:
            base_url = constants.TESTNET_API_URL if self.use_testnet else constants.MAINNET_API_URL
            self.info = Info(base_url=base_url, skip_ws=True)
            self.exchange = Exchange(
                account=self.account,
                base_url=base_url,
                skip_ws=True
            )
            logger.info(f"Connected to Hyperliquid ({'testnet' if self.use_testnet else 'mainnet'})")
        except Exception as e:
            logger.error(f"Failed to initialize Hyperliquid clients: {e}")
            raise

    def get_eth_price(self) -> float:
        """Get current ETH price with multiple fallback methods"""
        try:
            # Method 1: Hyperliquid meta info
            try:
                meta = self.info.meta()
                if meta and 'universe' in meta:
                    for asset in meta['universe']:
                        if asset.get('name') == 'ETH':
                            price = float(asset.get('markPx', 0))
                            if price > 0:
                                logger.info(f"ETH price from Hyperliquid meta: ${price}")
                                return price
            except Exception as e:
                logger.warning(f"Method 1 failed: {e}")

            # Method 2: Hyperliquid all mids
            try:
                all_mids = self.info.all_mids()
                if all_mids and 'ETH' in all_mids:
                    price = float(all_mids['ETH'])
                    if price > 0:
                        logger.info(f"ETH price from Hyperliquid mids: ${price}")
                        return price
            except Exception as e:
                logger.warning(f"Method 2 failed: {e}")

            # Method 3: External price feed fallback
            try:
                response = requests.get(
                    'https://api.coingecko.com/api/v3/simple/price?ids=ethereum&vs_currencies=usd',
                    timeout=5
                )
                if response.status_code == 200:
                    data = response.json()
                    price = float(data['ethereum']['usd'])
                    logger.info(f"ETH price from CoinGecko fallback: ${price}")
                    return price
            except Exception as e:
                logger.warning(f"Method 3 failed: {e}")

            # If all methods fail, return 0
            logger.error("All price feed methods failed")
            return 0

        except Exception as e:
            logger.error(f"Critical error in get_eth_price: {e}")
            return 0

    def get_account_info(self) -> Dict[str, Any]:
        """Get account information with error handling"""
        try:
            user_state = self.info.user_state(self.wallet_address)
            if not user_state:
                return {
                    'balance': '0',
                    'positions': [],
                    'account_connected': False
                }

            balance = '0'
            positions = []
            
            # Get balance
            if 'marginSummary' in user_state:
                balance = user_state['marginSummary'].get('accountValue', '0')
            
            # Get positions
            if 'assetPositions' in user_state:
                for pos in user_state['assetPositions']:
                    if pos.get('position', {}).get('coin') == 'ETH':
                        size = float(pos['position'].get('szi', '0'))
                        if abs(size) > 0.0001:  # Only include significant positions
                            positions.append({
                                'symbol': 'ETH',
                                'size': size,
                                'side': 'long' if size > 0 else 'short'
                            })

            return {
                'balance': balance,
                'positions': positions,
                'account_connected': True
            }

        except Exception as e:
            logger.error(f"Error getting account info: {e}")
            return {
                'balance': '0',
                'positions': [],
                'account_connected': False,
                'error': str(e)
            }

    def calculate_position_size(self, eth_price: float, balance: float) -> float:
        """Calculate position size based on available balance"""
        try:
            if eth_price <= 0 or balance <= 0:
                return 0
            
            # Use 95% of available balance for position
            position_value = balance * 0.95
            position_size = position_value / eth_price
            
            # Round to 4 decimal places (Hyperliquid precision)
            position_size = round(position_size, 4)
            
            logger.info(f"Calculated position size: {position_size} ETH (Balance: ${balance}, ETH Price: ${eth_price})")
            return position_size

        except Exception as e:
            logger.error(f"Error calculating position size: {e}")
            return 0

    def place_order(self, action: str) -> Dict[str, Any]:
        """Place order with improved error handling and JSON formatting"""
        try:
            # Get current price
            eth_price = self.get_eth_price()
            if eth_price <= 0:
                return {
                    'status': 'error',
                    'message': f'Failed to get ETH price: {eth_price}'
                }

            # Get account info
            account_info = self.get_account_info()
            if not account_info['account_connected']:
                return {
                    'status': 'error',
                    'message': 'Account not connected'
                }

            balance = float(account_info['balance'])
            
            if action == 'close':
                # Close all positions
                positions = account_info['positions']
                if not positions:
                    return {
                        'status': 'success',
                        'message': 'No positions to close',
                        'closed_positions': []
                    }

                closed_positions = []
                for position in positions:
                    try:
                        # Close position by trading opposite direction
                        size = abs(position['size'])
                        side = 'sell' if position['side'] == 'long' else 'buy'
                        
                        order_result = self.exchange.market_order(
                            coin='ETH',
                            is_buy=(side == 'buy'),
                            sz=size,
                            px=None  # Market order
                        )
                        
                        closed_positions.append({
                            'symbol': 'ETH',
                            'size': size,
                            'side': side,
                            'result': order_result
                        })
                        
                    except Exception as e:
                        logger.error(f"Error closing position: {e}")
                        continue

                return {
                    'status': 'success',
                    'message': f'Closed {len(closed_positions)} positions',
                    'closed_positions': closed_positions
                }

            # For buy/sell orders
            if balance < 1:  # Minimum $1 balance required
                return {
                    'status': 'error',
                    'message': f'Insufficient balance: ${balance}'
                }

            position_size = self.calculate_position_size(eth_price, balance)
            if position_size <= 0:
                return {
                    'status': 'error',
                    'message': f'Invalid position size: {position_size}'
                }

            # Determine order side
            is_buy = (action == 'buy')
            
            logger.info(f"Placing {action} order: {position_size} ETH at market price")

            # Place market order with proper error handling
            try:
                order_result = self.exchange.market_order(
                    coin='ETH',
                    is_buy=is_buy,
                    sz=position_size,
                    px=None  # Market order, no price limit
                )
                
                logger.info(f"Order result: {order_result}")
                
                return {
                    'status': 'success',
                    'message': f'{action.capitalize()} order placed successfully',
                    'order_result': order_result,
                    'position_size': position_size,
                    'eth_price': eth_price
                }

            except Exception as e:
                logger.error(f"Failed to place order: {e}")
                return {
                    'status': 'error',
                    'message': f'Order failed: {str(e)}',
                    'eth_price': eth_price,
                    'position_size': position_size
                }

        except Exception as e:
            logger.error(f"Critical error in place_order: {e}")
            return {
                'status': 'error',
                'message': f'Critical error: {str(e)}'
            }

    def process_webhook(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Process webhook with comprehensive error handling"""
        try:
            # Validate required fields
            if 'action' not in data:
                return {
                    'status': 'error',
                    'message': 'Missing action field'
                }

            if 'passphrase' not in data:
                return {
                    'status': 'error',
                    'message': 'Missing passphrase field'
                }

            # Verify passphrase
            if data['passphrase'] != self.webhook_secret:
                return {
                    'status': 'error',
                    'message': 'Invalid passphrase'
                }

            action = data['action'].lower()
            if action not in ['buy', 'sell', 'close']:
                return {
                    'status': 'error',
                    'message': f'Invalid action: {action}'
                }

            logger.info(f"Processing signal: {action}")

            # Execute the order
            result = self.place_order(action)
            
            logger.info(f"Order result: {result}")
            return result

        except Exception as e:
            logger.error(f"Error processing webhook: {e}")
            return {
                'status': 'error',
                'message': f'Processing error: {str(e)}'
            }

# Initialize bot
try:
    bot = HyperliquidBot()
except Exception as e:
    logger.error(f"Failed to initialize bot: {e}")
    bot = None

@app.route('/', methods=['GET'])
def status():
    """Bot status endpoint"""
    try:
        if not bot:
            return jsonify({
                'status': 'error',
                'message': 'Bot not initialized'
            }), 500

        account_info = bot.get_account_info()
        eth_price = bot.get_eth_price()

        return jsonify({
            'bot': 'Hyperliquid ETH Trading Bot',
            'status': 'running',
            'symbol': bot.symbol,
            'testnet': bot.use_testnet,
            'wallet': bot.wallet_address,
            'account_connected': account_info['account_connected'],
            'balance': account_info['balance'],
            'positions': len(account_info['positions']),
            'eth_price': eth_price,
            'timestamp': datetime.utcnow().isoformat(),
            'version': '2.1'
        })

    except Exception as e:
        logger.error(f"Error in status endpoint: {e}")
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500

@app.route('/health', methods=['GET'])
def health():
    """Health check endpoint"""
    return jsonify({
        'status': 'healthy',
        'timestamp': datetime.utcnow().isoformat()
    })

@app.route('/webhook', methods=['POST'])
def webhook():
    """Webhook endpoint for TradingView alerts"""
    try:
        if not bot:
            logger.error("Bot not initialized")
            return jsonify({
                'status': 'error',
                'message': 'Bot not initialized'
            }), 500

        # Parse JSON data
        try:
            data = request.get_json()
            if not data:
                return jsonify({
                    'status': 'error',
                    'message': 'No JSON data received'
                }), 400
        except Exception as e:
            logger.error(f"Failed to parse JSON: {e}")
            return jsonify({
                'status': 'error',
                'message': 'Invalid JSON format'
            }), 400

        logger.info(f"Received webhook: {data}")

        # Process the webhook
        result = bot.process_webhook(data)
        
        # Return appropriate status code
        status_code = 200 if result.get('status') == 'success' else 400
        
        return jsonify(result), status_code

    except Exception as e:
        logger.error(f"Critical error in webhook endpoint: {e}")
        return jsonify({
            'status': 'error',
            'message': f'Server error: {str(e)}'
        }), 500

@app.errorhandler(Exception)
def handle_exception(e):
    """Global exception handler"""
    logger.error(f"Unhandled exception: {e}")
    return jsonify({
        'status': 'error',
        'message': 'Internal server error'
    }), 500

if __name__ == '__main__':
    port = int(os.getenv('PORT', 8000))
    app.run(host='0.0.0.0', port=port, debug=False)

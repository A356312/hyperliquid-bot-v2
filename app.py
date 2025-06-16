import os
import json
import requests
import time
from flask import Flask, request, jsonify
from datetime import datetime
import logging
import hashlib
from eth_account import Account
from eth_account.messages import encode_defunct

# Logging Setup
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

class HyperliquidBot:
    def __init__(self):
        # Environment Variables
        self.private_key = os.environ.get('HYPERLIQUID_PRIVATE_KEY')
        self.webhook_secret = os.environ.get('WEBHOOK_SECRET', 'default_secret')
        self.testnet = os.environ.get('USE_TESTNET', 'false').lower() == 'true'
        
        # API URLs
        self.api_url = "https://api.hyperliquid-testnet.xyz" if self.testnet else "https://api.hyperliquid.xyz"
        
        # Symbol
        self.symbol = "ETH"
        
        if not self.private_key:
            logger.warning("HYPERLIQUID_PRIVATE_KEY not set! Bot will run in simulation mode.")
        else:
            try:
                # Create account from private key
                self.account = Account.from_key(self.private_key)
                self.wallet_address = self.account.address
                logger.info(f"Bot initialized for wallet: {self.wallet_address}")
            except Exception as e:
                logger.error(f"Invalid private key: {e}")
                self.account = None
                self.wallet_address = None
    
    def sign_l1_action(self, action, nonce):
        """Sign action for Hyperliquid"""
        if not self.private_key or not self.account:
            return "SIMULATION_SIGNATURE"
        
        try:
            # Create the message to sign
            msg_dict = {
                "action": action,
                "nonce": nonce
            }
            
            # Convert to JSON string
            msg_str = json.dumps(msg_dict, separators=(',', ':'))
            
            # Create message hash
            msg_hash = hashlib.sha256(msg_str.encode()).hexdigest()
            
            # Sign with private key
            message = encode_defunct(text=msg_hash)
            signed_message = self.account.sign_message(message)
            
            return signed_message.signature.hex()
            
        except Exception as e:
            logger.error(f"Error signing message: {e}")
            return None
    
    def validate_webhook(self, data):
        """Validate incoming webhook"""
        if not isinstance(data, dict):
            return False, "Invalid JSON data"
            
        if 'passphrase' not in data:
            return False, "Missing passphrase"
        
        if data['passphrase'] != self.webhook_secret:
            return False, "Invalid passphrase"
        
        if 'action' not in data:
            return False, "Missing action"
        
        valid_actions = ['buy', 'sell', 'close']
        if data['action'].lower() not in valid_actions:
            return False, f"Invalid action. Must be one of: {valid_actions}"
        
        return True, "Valid"
    
    def get_account_state(self):
        """Get current account state and positions"""
        try:
            endpoint = f"{self.api_url}/info"
            
            if not self.wallet_address:
                logger.info("No wallet address - simulation mode")
                return None
            
            payload = {
                "type": "clearinghouseState",
                "user": self.wallet_address
            }
            
            response = requests.post(endpoint, json=payload, timeout=10)
            
            if response.status_code == 200:
                return response.json()
            else:
                logger.error(f"Failed to get account state: {response.status_code} - {response.text}")
                return None
                
        except Exception as e:
            logger.error(f"Error getting account state: {e}")
            return None
    
    def close_all_positions(self):
        """Close all open positions"""
        try:
            if not self.private_key or not self.account:
                logger.info("SIMULATION: Closing all positions")
                return {"status": "simulated", "message": "All positions closed (simulation)"}
            
            # Get current positions
            account_state = self.get_account_state()
            if not account_state or 'assetPositions' not in account_state:
                return {"status": "success", "message": "No positions to close"}
            
            closed_positions = []
            
            for position in account_state['assetPositions']:
                if position['position']['coin'] == self.symbol:
                    size = float(position['position']['szi'])
                    
                    if abs(size) > 0.0001:  # Position exists (small threshold for floating point)
                        # Determine close side (opposite of current position)
                        is_buy = size < 0  # If short, we buy to close
                        close_size = abs(size)
                        
                        # Create close order
                        nonce = int(time.time() * 1000)
                        
                        order_action = {
                            "type": "order",
                            "orders": [{
                                "a": 0,  # ETH asset index
                                "b": is_buy,
                                "p": "0",  # Market order
                                "s": str(close_size),
                                "r": True,  # Reduce only
                                "t": {"market": {}}
                            }]
                        }
                        
                        signature = self.sign_l1_action(order_action, nonce)
                        if not signature:
                            return {"status": "error", "message": "Failed to sign close order"}
                        
                        payload = {
                            "action": order_action,
                            "nonce": nonce,
                            "signature": signature
                        }
                        
                        response = requests.post(f"{self.api_url}/exchange", json=payload, timeout=10)
                        
                        if response.status_code == 200:
                            closed_positions.append({
                                "symbol": self.symbol,
                                "size": close_size,
                                "side": "buy" if is_buy else "sell"
                            })
                            logger.info(f"Closed position: {close_size} {self.symbol}")
                        else:
                            logger.error(f"Failed to close position: {response.text}")
            
            return {
                "status": "success",
                "message": f"Closed {len(closed_positions)} positions",
                "closed_positions": closed_positions
            }
            
        except Exception as e:
            logger.error(f"Error closing positions: {e}")
            return {"status": "error", "message": str(e)}
    
    def calculate_position_size(self, account_state):
        """Calculate position size using 100% of available balance"""
        try:
            if not account_state:
                return 0
            
            # Get withdrawable balance (this is what we can use for trading)
            withdrawable = float(account_state.get('withdrawable', '0'))
            
            if withdrawable <= 1:  # Need at least $1
                logger.warning(f"Insufficient balance: ${withdrawable}")
                return 0
            
            # Get current ETH price to calculate position size
            eth_price = self.get_eth_price()
            if not eth_price or eth_price <= 0:
                logger.error("Could not get valid ETH price")
                return 0
            
            # Calculate position size (use 99% to account for fees)
            usable_balance = withdrawable * 0.99
            position_size = usable_balance / eth_price
            
            # Round to reasonable precision (4 decimal places)
            position_size = round(position_size, 4)
            
            logger.info(f"Calculated position size: {position_size} ETH (Balance: ${withdrawable}, ETH Price: ${eth_price})")
            return position_size
            
        except Exception as e:
            logger.error(f"Error calculating position size: {e}")
            return 0
    
    def get_eth_price(self):
        """Get current ETH price"""
        try:
            endpoint = f"{self.api_url}/info"
            payload = {"type": "allMids"}
            
            response = requests.post(endpoint, json=payload, timeout=10)
            
            if response.status_code == 200:
                data = response.json()
                # ETH is typically at index 0
                if len(data) > 0:
                    price = float(data[0])
                    if price > 0:
                        return price
                    
            logger.warning("Could not get ETH price from API, using fallback")
            return 3000.0  # Fallback price
                
        except Exception as e:
            logger.error(f"Error getting ETH price: {e}")
            return 3000.0  # Fallback price
    
    def place_order(self, action):
        """Place market order on Hyperliquid"""
        try:
            if not self.private_key or not self.account:
                logger.info(f"SIMULATION: {action.upper()} ETH with 100% balance")
                return {"status": "simulated", "message": f"{action.upper()} order simulated"}
            
            # Get account state
            account_state = self.get_account_state()
            if not account_state:
                return {"status": "error", "message": "Could not get account state"}
            
            # Calculate position size
            position_size = self.calculate_position_size(account_state)
            
            if position_size <= 0:
                return {"status": "error", "message": "Invalid position size - insufficient balance"}
            
            # Determine order side
            is_buy = action.lower() == 'buy'
            
            # Create order
            nonce = int(time.time() * 1000)
            
            order_action = {
                "type": "order",
                "orders": [{
                    "a": 0,  # ETH asset index on Hyperliquid
                    "b": is_buy,
                    "p": "0",  # Market order (price = 0)
                    "s": str(position_size),
                    "r": False,  # Not reduce only
                    "t": {"market": {}}
                }]
            }
            
            signature = self.sign_l1_action(order_action, nonce)
            if not signature:
                return {"status": "error", "message": "Failed to sign order"}
            
            payload = {
                "action": order_action,
                "nonce": nonce,
                "signature": signature
            }
            
            response = requests.post(f"{self.api_url}/exchange", json=payload, timeout=10)
            
            if response.status_code == 200:
                result = response.json()
                logger.info(f"Order placed: {action.upper()} {position_size} ETH")
                return {
                    "status": "success",
                    "action": action,
                    "symbol": self.symbol,
                    "size": position_size,
                    "order_type": "market",
                    "result": result
                }
            else:
                logger.error(f"Failed to place order: {response.status_code} - {response.text}")
                return {"status": "error", "message": f"Order failed: {response.text}"}
                
        except Exception as e:
            logger.error(f"Error placing order: {e}")
            return {"status": "error", "message": str(e)}
    
    def process_signal(self, data):
        """Process TradingView signal"""
        try:
            # Validate webhook
            is_valid, message = self.validate_webhook(data)
            if not is_valid:
                return {"status": "error", "message": message}
            
            action = data['action'].lower()
            
            logger.info(f"Processing signal: {action}")
            
            if action == 'close':
                # Close all positions
                return self.close_all_positions()
            
            elif action in ['buy', 'sell']:
                # First close any existing positions
                close_result = self.close_all_positions()
                logger.info(f"Close result: {close_result}")
                
                # Small delay to ensure position is closed
                time.sleep(2)
                
                # Then open new position
                return self.place_order(action)
            
            else:
                return {"status": "error", "message": f"Unknown action: {action}"}
                
        except Exception as e:
            logger.error(f"Error processing signal: {e}")
            return {"status": "error", "message": str(e)}

# Initialize bot
bot = HyperliquidBot()

@app.route('/')
def home():
    """Health check endpoint"""
    return jsonify({
        "status": "running",
        "bot": "Hyperliquid ETH Trading Bot",
        "symbol": bot.symbol,
        "testnet": bot.testnet,
        "wallet": bot.wallet_address if bot.wallet_address else "simulation",
        "timestamp": datetime.now().isoformat(),
        "version": "2.0"
    })

@app.route('/webhook', methods=['POST'])
def webhook():
    """TradingView webhook endpoint"""
    try:
        data = request.get_json()
        
        if not data:
            logger.warning("No JSON data received")
            return jsonify({"error": "No JSON data received"}), 400
        
        logger.info(f"Received webhook: {data}")
        
        # Process the trading signal
        result = bot.process_signal(data)
        
        if result["status"] in ["success", "simulated"]:
            return jsonify(result), 200
        else:
            return jsonify(result), 400
            
    except Exception as e:
        logger.error(f"Webhook error: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/status')
def status():
    """Bot status endpoint"""
    try:
        account_state = bot.get_account_state()
        
        status_info = {
            "status": "operational",
            "symbol": bot.symbol,
            "testnet": bot.testnet,
            "wallet": bot.wallet_address if bot.wallet_address else "simulation",
            "account_connected": account_state is not None,
            "timestamp": datetime.now().isoformat()
        }
        
        if account_state:
            status_info["balance"] = account_state.get('withdrawable', '0')
            status_info["positions"] = len(account_state.get('assetPositions', []))
        
        return jsonify(status_info)
        
    except Exception as e:
        logger.error(f"Status error: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/health')
def health():
    """Simple health check"""
    return jsonify({"status": "healthy"}), 200

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8000))
    app.run(host='0.0.0.0', port=port, debug=False)

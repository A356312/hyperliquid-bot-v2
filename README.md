# Hyperliquid Trading Bot

Automated ETH trading bot for Hyperliquid exchange with TradingView webhook integration.

## Features
- Market orders (100% balance)
- Position management (long/short/close)
- Secure webhook validation
- Comprehensive logging
- Health monitoring

## Signals
- `buy` - Close all positions, open long
- `sell` - Close all positions, open short  
- `close` - Close all positions

## Environment Variables
- `HYPERLIQUID_PRIVATE_KEY` - Your wallet private key (sealed)
- `WEBHOOK_SECRET` - Webhook validation secret
- `USE_TESTNET` - true/false (default: false)
- `PORT` - Server port (default: 8000)

## Endpoints
- `/` - Health check
- `/webhook` - TradingView webhook
- `/status` - Bot status
- `/health` - Simple health check

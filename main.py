import os
import json
import logging
from typing import Dict, Any, Optional
import asyncio

from fastmcp import FastMCP
from pydantic import BaseModel, Field
from dotenv import load_dotenv

from services.hyperliquid_services import HyperliquidServices

# Load environment variables
load_dotenv()

# Configure logging
log_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'hyperliquid_mcp.log')
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(log_file)
    ]
)
logger = logging.getLogger(__name__)

# Initialize FastMCP
mcp = FastMCP("HyperLiquid Trading MCP")

# Global service instance
hyperliquid_service: Optional[HyperliquidServices] = None


class ConfigModel(BaseModel):
    """Configuration model for HyperLiquid settings"""
    private_key: str = Field(..., description="Private key for signing transactions")
    testnet: bool = Field(default=False, description="Use testnet instead of mainnet")
    account_address: Optional[str] = Field(default=None, description="Account address (derived from private key if not provided)")


def get_config() -> ConfigModel:
    """Get configuration from environment variables or config file"""
    # Try environment variables first
    private_key = os.getenv("HYPERLIQUID_PRIVATE_KEY")
    testnet = os.getenv("HYPERLIQUID_TESTNET", "false").lower() == "true"
    account_address = os.getenv("HYPERLIQUID_ACCOUNT_ADDRESS")
    
    if private_key:
        return ConfigModel(
            private_key=private_key,
            testnet=testnet,
            account_address=account_address
        )
    
    # Try config file
    config_path = "config.json"
    if os.path.exists(config_path):
        with open(config_path, 'r') as f:
            config_data = json.load(f)
            return ConfigModel(**config_data)
    
    raise ValueError(
        "No configuration found. Please set HYPERLIQUID_PRIVATE_KEY environment variable "
        "or create a config.json file with your private key."
    )


def initialize_service():
    """Initialize the HyperLiquid service"""
    global hyperliquid_service
    if hyperliquid_service is None:
        config = get_config()
        network = "Testnet" if config.testnet else "Mainnet"
        logger.info(f"Initializing HyperLiquid service - Network: {network}")
        hyperliquid_service = HyperliquidServices(
            private_key=config.private_key,
            testnet=config.testnet,
            account_address=config.account_address
        )
        account_info = config.account_address or "Derived from private key"
        logger.info(f"Service initialized for account: {account_info}")


# Account Management Tools

@mcp.tool
async def get_account_balance() -> Dict[str, Any]:
    """Get account balance and margin information"""
    initialize_service()
    return await hyperliquid_service.get_account_balance()


@mcp.tool
async def get_open_positions() -> Dict[str, Any]:
    """Get all open positions with PnL information"""
    initialize_service()
    return await hyperliquid_service.get_open_positions()


@mcp.tool
async def get_open_orders() -> Dict[str, Any]:
    """Get all open orders"""
    initialize_service()
    return await hyperliquid_service.get_open_orders()


@mcp.tool
async def get_trade_history(days: int = 7) -> Dict[str, Any]:
    """
    Get trade history for the account
    
    Args:
        days: Number of days to look back (default: 7)
    """
    initialize_service()
    return await hyperliquid_service.get_trade_history(days)


# Trading Tools

@mcp.tool
async def place_limit_order(
    coin: str,
    side: str,
    size: float,
    price: float,
    reduce_only: bool = False,
    client_order_id: Optional[str] = None
) -> Dict[str, Any]:
    """
    Place a basic limit order (for opening new positions or manual closing)
    
    Args:
        coin: Trading pair (e.g., "BTC", "ETH")
        side: Order side ("buy" or "sell")
        size: Number of tokens/coins to trade (NOT dollar value - e.g., 0.1 for 0.1 SOL, not $20)
        price: Limit price per token (e.g., 150.0 for $150 per SOL)
        reduce_only: Whether order should only reduce existing position
        client_order_id: Optional client order ID (128-bit hex string, e.g. 0x1234567890abcdef1234567890abcdef)
    
    IMPORTANT: The 'size' parameter is the NUMBER OF TOKENS, not dollar value. 
    If user wants "$20 worth of SOL" at $150/SOL, calculate: $20 ÷ $150 = 0.133 SOL
    
    Note: For setting take profit/stop loss on existing positions, use set_take_profit_stop_loss instead.
    """
    initialize_service()
    is_buy = side.lower() == "buy"
    
    return await hyperliquid_service.place_order(
        coin=coin,
        is_buy=is_buy,
        sz=size,
        limit_px=price,
        reduce_only=reduce_only,
        cloid=client_order_id
    )


@mcp.tool
async def market_open_position(
    coin: str,
    side: str,
    size: float,
    client_order_id: Optional[str] = None
) -> Dict[str, Any]:
    """
    Open a new position at market price using HyperLiquid's market_open for optimal execution
    
    Args:
        coin: Trading pair (e.g., "BTC", "ETH")
        side: Position side ("buy" for long, "sell" for short)
        size: Number of tokens/coins to trade (NOT dollar value - e.g., 0.1 for 0.1 SOL, not $20)
        client_order_id: Optional client order ID for tracking
    
    IMPORTANT: The 'size' parameter is the NUMBER OF TOKENS, not dollar value.
    If user wants "$20 worth of SOL" at current price ~$150, calculate: $20 ÷ $150 = 0.133 SOL
    
    Note: This uses HyperLiquid's native market_open method for the best execution.
    """
    initialize_service()
    is_buy = side.lower() == "buy"
    
    return await hyperliquid_service.market_open_position(
        coin=coin,
        is_buy=is_buy,
        sz=size,
        cloid=client_order_id
    )


@mcp.tool
async def market_close_position(
    coin: str,
    client_order_id: Optional[str] = None
) -> Dict[str, Any]:
    """
    Close all positions for a coin at market price using HyperLiquid's market_close
    
    Args:
        coin: Trading pair (e.g., "BTC", "ETH")
        client_order_id: Optional client order ID for tracking
    
    Note: This closes ALL positions for the specified coin. HyperLiquid's market_close method
    automatically determines the correct side and size to close all positions.
    """
    initialize_service()
    
    return await hyperliquid_service.market_close_position(
        coin=coin,
        cloid=client_order_id
    )


@mcp.tool
async def place_bracket_order(
    coin: str,
    side: str,
    size: float,
    entry_price: float,
    take_profit_price: float,
    stop_loss_price: float,
    client_order_id: Optional[str] = None
) -> Dict[str, Any]:
    """
    Place a bracket order for a NEW position (entry + take profit + stop loss in one order)
    
    Args:
        coin: Trading pair (e.g., "BTC", "ETH")
        side: Order side ("buy" or "sell")
        size: Number of tokens/coins to trade (NOT dollar value - e.g., 0.1 for 0.1 SOL, not $20)
        entry_price: Entry limit price per token
        take_profit_price: Take profit price per token
        stop_loss_price: Stop loss price per token
        client_order_id: Optional client order ID (128-bit hex string, e.g. 0x1234567890abcdef1234567890abcdef)
    
    IMPORTANT: The 'size' parameter is the NUMBER OF TOKENS, not dollar value.
    If user wants "$20 worth of SOL" at $150/SOL, calculate: $20 ÷ $150 = 0.133 SOL
    
    Note: This creates a NEW position with TP/SL. For existing positions, use set_take_profit_stop_loss.
    Uses HyperLiquid's normalTpSl grouping for proper OCO behavior where TP and SL orders cancel each other.
    """
    initialize_service()
    is_buy = side.lower() == "buy"
    
    return await hyperliquid_service.place_bracket_order(
        coin=coin,
        is_buy=is_buy,
        sz=size,
        limit_px=entry_price,
        take_profit_px=take_profit_price,
        stop_loss_px=stop_loss_price,
        cloid=client_order_id
    )


@mcp.tool
async def cancel_order(coin: str, order_id: int) -> Dict[str, Any]:
    """
    Cancel a specific order by order ID
    
    Args:
        coin: Trading pair
        order_id: Order ID to cancel
    """
    initialize_service()
    return await hyperliquid_service.cancel_order(coin, order_id)


@mcp.tool
async def cancel_order_by_client_id(coin: str, client_order_id: str) -> Dict[str, Any]:
    """
    Cancel a specific order by client order ID
    
    Args:
        coin: Trading pair
        client_order_id: Client order ID to cancel (128-bit hex string, e.g. 0x1234567890abcdef1234567890abcdef)
    """
    initialize_service()
    return await hyperliquid_service.cancel_order_by_cloid(coin, client_order_id)


@mcp.tool
async def cancel_all_orders(coin: Optional[str] = None) -> Dict[str, Any]:
    """
    Cancel all orders, optionally for a specific coin
    
    Args:
        coin: Optional trading pair to cancel orders for (if None, cancels all orders)
    """
    initialize_service()
    return await hyperliquid_service.cancel_all_orders(coin)


@mcp.tool
async def modify_order(
    coin: str,
    order_id: int,
    new_size: float,
    new_price: float
) -> Dict[str, Any]:
    """
    Modify an existing order
    
    Args:
        coin: Trading pair
        order_id: Order ID to modify
        new_size: New order size
        new_price: New order price
    """
    initialize_service()
    return await hyperliquid_service.modify_order(coin, order_id, new_size, new_price)


# Market Data Tools

@mcp.tool
async def get_market_data(coin: str) -> Dict[str, Any]:
    """
    Get market data for a specific coin
    
    Args:
        coin: Trading pair (e.g., "BTC", "ETH")
    """
    initialize_service()
    return await hyperliquid_service.get_market_data(coin)


@mcp.tool
async def get_orderbook(coin: str, depth: int = 20) -> Dict[str, Any]:
    """
    Get orderbook data for a specific coin
    
    Args:
        coin: Trading pair (e.g., "BTC", "ETH")
        depth: Order book depth (default: 20)
    """
    initialize_service()
    return await hyperliquid_service.get_orderbook(coin, depth)


@mcp.tool
async def get_funding_history(coin: str, days: int = 7) -> Dict[str, Any]:
    """
    Get funding history for a coin
    
    Args:
        coin: Trading pair (e.g., "BTC", "ETH")
        days: Number of days to look back (default: 7)
    """
    initialize_service()
    return await hyperliquid_service.get_funding_history(coin, days)


# Account Management Tools

@mcp.tool
async def update_leverage(coin: str, leverage: int, cross_margin: bool = True) -> Dict[str, Any]:
    """
    Update leverage for a coin
    
    Args:
        coin: Trading pair (e.g., "BTC", "ETH")
        leverage: Leverage amount (e.g., 10 for 10x)
        cross_margin: Use cross margin (True) or isolated margin (False)
    """
    initialize_service()
    return await hyperliquid_service.update_leverage(coin, leverage, cross_margin)


@mcp.tool
async def transfer_between_spot_and_perp(
    amount: float,
    to_perp: bool = True
) -> Dict[str, Any]:
    """
    Transfer funds between spot and perpetual accounts
    
    Args:
        amount: Amount to transfer
        to_perp: Transfer to perpetual account (True) or to spot account (False)
    """
    initialize_service()
    return await hyperliquid_service.transfer_between_spot_and_perp(amount, to_perp)


@mcp.tool
async def set_take_profit_stop_loss(
    coin: str,
    take_profit_price: Optional[float] = None,
    stop_loss_price: Optional[float] = None,
    position_size: Optional[float] = None
) -> Dict[str, Any]:
    """
    Set take profit and/or stop loss orders for an EXISTING position (OCO orders)
    
    Args:
        coin: Trading pair (e.g., "BTC", "ETH") - must have an existing position
        take_profit_price: Take profit price (optional, can set just TP)
        stop_loss_price: Stop loss price (optional, can set just SL)
        position_size: Position size (will auto-detect from existing position if not provided)
    
    Note: This is for EXISTING positions only. Use place_bracket_order for new positions with TP/SL.
    The orders will use OCO (One-Cancels-Other) behavior where executing one cancels the other.
    """
    initialize_service()
    return await hyperliquid_service.set_position_tpsl(
        coin=coin,
        tp_px=take_profit_price,
        sl_px=stop_loss_price,
        position_size=position_size
    )


@mcp.tool
async def set_take_profit(
    coin: str,
    take_profit_price: float,
    position_size: Optional[float] = None
) -> Dict[str, Any]:
    """
    Set ONLY a take profit order for an EXISTING position
    
    Args:
        coin: Trading pair (e.g., "BTC", "ETH") - must have an existing position
        take_profit_price: Take profit price
        position_size: Position size (will auto-detect from existing position if not provided)
    
    Note: This is specifically for setting ONLY take profit on EXISTING positions.
    Use set_take_profit_stop_loss if you want both TP and SL, or place_bracket_order for new positions.
    """
    initialize_service()
    return await hyperliquid_service.set_position_tpsl(
        coin=coin,
        tp_px=take_profit_price,
        sl_px=None,
        position_size=position_size
    )


@mcp.tool
async def set_stop_loss(
    coin: str,
    stop_loss_price: float,
    position_size: Optional[float] = None
) -> Dict[str, Any]:
    """
    Set ONLY a stop loss order for an EXISTING position
    
    Args:
        coin: Trading pair (e.g., "BTC", "ETH") - must have an existing position  
        stop_loss_price: Stop loss price
        position_size: Position size (will auto-detect from existing position if not provided)
    
    Note: This is specifically for setting ONLY stop loss on EXISTING positions.
    Use set_take_profit_stop_loss if you want both TP and SL, or place_bracket_order for new positions.
    """
    initialize_service()
    return await hyperliquid_service.set_position_tpsl(
        coin=coin,
        tp_px=None,
        sl_px=stop_loss_price,
        position_size=position_size
    )


# Utility Tools

@mcp.tool
async def get_account_summary() -> Dict[str, Any]:
    """Get a comprehensive account summary including balance, positions, and orders"""
    initialize_service()
    
    # Get all account information
    balance = await hyperliquid_service.get_account_balance()
    positions = await hyperliquid_service.get_open_positions()
    orders = await hyperliquid_service.get_open_orders()
    
    return {
        "success": True,
        "summary": {
            "balance": balance.get("data") if balance.get("success") else None,
            "positions": positions.get("positions", []) if positions.get("success") else [],
            "orders": orders.get("orders", []) if orders.get("success") else [],
            "total_positions": positions.get("total_positions", 0) if positions.get("success") else 0,
            "total_orders": orders.get("total_orders", 0) if orders.get("success") else 0
        }
    }


@mcp.tool
async def close_position(coin: str, percentage: float = 100.0) -> Dict[str, Any]:
    """
    Close a position (full or partial)
    
    Args:
        coin: Trading pair (e.g., "BTC", "ETH")
        percentage: Percentage of position to close (default: 100.0 for full close)
        
    Note: For 100% closure, uses market_close_position for optimal execution.
          For partial closure, you'll need to use limit orders as HyperLiquid's 
          market_close closes all positions.
    """
    initialize_service()
    
    if percentage == 100.0:
        # For full closure, use the dedicated market_close_position method
        return await hyperliquid_service.market_close_position(coin=coin)
    else:
        # For partial closure, we can't use market_close as it closes everything
        # This would require a different approach - perhaps a limit order or market order with specific size
        return {
            "success": False,
            "error": f"Partial position closure ({percentage}%) not supported with market orders. "
                    f"HyperLiquid's market_close closes ALL positions. "
                    f"Use limit orders for partial closure or set percentage=100 for full closure."
        }


@mcp.tool
async def calculate_token_amount_from_dollars(
    coin: str,
    dollar_amount: float
) -> Dict[str, Any]:
    """
    Calculate how many tokens can be bought with a given dollar amount

    Args:
        coin: Trading pair (e.g., "BTC", "ETH", "SOL")
        dollar_amount: Dollar amount to spend (e.g., 20.0 for $20)

    Returns:
        Dictionary with token amount, current price, and sz_decimals for proper rounding

    Example: calculate_token_amount_from_dollars("SOL", 20.0)
             → {"token_amount": 0.133, "current_price": 150.0, "dollar_amount": 20.0, "sz_decimals": 1}
    """
    initialize_service()

    # Get current market price
    market_data = await hyperliquid_service.get_market_data(coin)

    if not market_data.get("success"):
        return {
            "success": False,
            "error": f"Failed to get market data for {coin}: {market_data.get('error', 'Unknown error')}"
        }

    current_price = market_data["market_data"]["mid_price"]
    if current_price == "N/A":
        return {
            "success": False,
            "error": f"Could not get current price for {coin}"
        }

    current_price = float(current_price)

    # Get szDecimals for this asset from metadata
    sz_decimals = 0  # Default to 0 (whole numbers) - safest for most altcoins
    try:
        meta = hyperliquid_service.info.meta()
        for asset_info in meta.get('universe', []):
            if asset_info.get('name') == coin:
                sz_decimals = asset_info.get('szDecimals', 0)
                break
    except Exception:
        pass  # Use default if metadata fetch fails

    token_amount = dollar_amount / current_price
    # Truncate to the asset's szDecimals (don't round up to avoid oversizing)
    token_amount_truncated = int(token_amount * (10 ** sz_decimals)) / (10 ** sz_decimals)

    return {
        "success": True,
        "coin": coin,
        "dollar_amount": dollar_amount,
        "current_price": current_price,
        "token_amount": token_amount_truncated,
        "sz_decimals": sz_decimals,
        "calculation": f"${dollar_amount} ÷ ${current_price} = {token_amount_truncated} {coin} (truncated to {sz_decimals} decimals)"
    }


async def run_as_server():
    await mcp.run_async(
        transport="http", 
        host="127.0.0.1",
        port=8080,
    )

def run_standard_server():
    mcp.run()

def start_server():
    """Entry point for 'poetry start' command - runs HTTP server"""
    try:
        config = get_config()
        logger.info("HyperLiquid MCP Server starting...")
        network = "Testnet" if config.testnet else "Mainnet"
        logger.info(f"Network: {network}")
        account_display = config.account_address or "Will be derived from private key"
        logger.info(f"Account: {account_display}")
        log_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'hyperliquid_mcp.log')
        logger.info(f"Logs will be written to: {log_path}")
        
        asyncio.run(run_as_server())
    except Exception as e:
        logger.error(f"Failed to start server: {e}")
        print(f"Failed to start server: {e}")
        print("\nTo configure the server:")
        print("1. Set environment variables:")
        print("   export HYPERLIQUID_PRIVATE_KEY='your_private_key'")
        print("   export HYPERLIQUID_TESTNET='false'  # or 'true' for testnet")
        print("   export HYPERLIQUID_ACCOUNT_ADDRESS='your_address'  # optional")
        print("\n2. Or create a config.json file:")
        print('   {"private_key": "your_private_key", "testnet": false, "account_address": "your_address"}')
        print("\n3. Or create a .env file:")
        print("   HYPERLIQUID_PRIVATE_KEY=your_private_key")
        print("   HYPERLIQUID_TESTNET=false")
        print("   HYPERLIQUID_ACCOUNT_ADDRESS=your_address")

def stdio_server():
    """Entry point for 'poetry stdio' command - runs stdio server"""
    try:
        config = get_config()
        logger.info("HyperLiquid MCP Server starting in stdio mode...")
        network = "Testnet" if config.testnet else "Mainnet"
        logger.info(f"Network: {network}")
        account_display = config.account_address or "Will be derived from private key"
        logger.info(f"Account: {account_display}")
        log_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'hyperliquid_mcp.log')
        logger.info(f"Logs will be written to: {log_path}")
        
        run_standard_server()
    except Exception as e:
        logger.error(f"Failed to start stdio server: {e}")
        print(f"Failed to start stdio server: {e}")
        print("\nTo configure the server:")
        print("1. Set environment variables:")
        print("   export HYPERLIQUID_PRIVATE_KEY='your_private_key'")
        print("   export HYPERLIQUID_TESTNET='false'  # or 'true' for testnet")
        print("   export HYPERLIQUID_ACCOUNT_ADDRESS='your_address'  # optional")
        print("\n2. Or create a config.json file:")
        print('   {"private_key": "your_private_key", "testnet": false, "account_address": "your_address"}')
        print("\n3. Or create a .env file:")
        print("   HYPERLIQUID_PRIVATE_KEY=your_private_key")
        print("   HYPERLIQUID_TESTNET=false")
        print("   HYPERLIQUID_ACCOUNT_ADDRESS=your_address")

if __name__ == "__main__":
    try:
        config = get_config()
        logger.info("HyperLiquid MCP Server starting...")
        network = "Testnet" if config.testnet else "Mainnet"
        logger.info(f"Network: {network}")
        account_display = config.account_address or "Will be derived from private key"
        logger.info(f"Account: {account_display}")
        log_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'hyperliquid_mcp.log')
        logger.info(f"Logs will be written to: {log_path}")
        
        # run_standard_server()
        asyncio.run(run_as_server())
    except Exception as e:
        logger.error(f"Failed to start server: {e}")
        print(f"Failed to start server: {e}")
        print("\nTo configure the server:")
        print("1. Set environment variables:")
        print("   export HYPERLIQUID_PRIVATE_KEY='your_private_key'")
        print("   export HYPERLIQUID_TESTNET='false'  # or 'true' for testnet")
        print("   export HYPERLIQUID_ACCOUNT_ADDRESS='your_address'  # optional")
        print("\n2. Or create a config.json file:")
        print('   {"private_key": "your_private_key", "testnet": false, "account_address": "your_address"}')
        print("\n3. Or create a .env file:")
        print("   HYPERLIQUID_PRIVATE_KEY=your_private_key")
        print("   HYPERLIQUID_TESTNET=false")
        print("   HYPERLIQUID_ACCOUNT_ADDRESS=your_address")
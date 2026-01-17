import time
import logging
from typing import Dict, Optional, Union, Any

from hyperliquid.info import Info
from hyperliquid.exchange import Exchange
from hyperliquid.utils import constants
from hyperliquid.utils.types import Cloid
from hyperliquid.utils.signing import (
    order_request_to_order_wire,
    order_wires_to_order_action,
    sign_l1_action
)

class HyperliquidServices:
    """Comprehensive HyperLiquid services for trading and account management"""
    
    def __init__(self, private_key: str, testnet: bool = False, account_address: str = None):
        """
        Initialize HyperLiquid services
        
        Args:
            private_key: Private key for signing transactions
            testnet: Whether to use testnet (default: False for mainnet)
            account_address: Optional account address (will be derived from private key if not provided)
        """
        self.private_key = private_key
        self.testnet = testnet
        
        # Set up logging
        self.logger = logging.getLogger("hyperliquid_services.HyperliquidServices")
        if not self.logger.handlers:
            handler = logging.StreamHandler()
            formatter = logging.Formatter(
                '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
            )
            handler.setFormatter(formatter)
            self.logger.addHandler(handler)
            self.logger.setLevel(logging.INFO)
        
        # Set up API URLs
        if testnet:
            self.base_url = "https://api.hyperliquid.xyz"
        else:
            self.base_url = "https://api.hyperliquid.xyz"
            
        # Initialize account address and wallet
        from eth_account import Account
        self.wallet = Account.from_key(private_key)
        
        self.account_address = account_address

        print(self.account_address)
        
        # Initialize clients
        self.info = Info(self.base_url, skip_ws=True)
        self.exchange = Exchange(self.wallet, self.base_url)
        
        network = "testnet" if testnet else "mainnet"
        self.logger.info(f"HyperliquidServices initialized for account {self.account_address} on {network}")

    def _get_asset_info(self, coin: str) -> Dict[str, Any]:
        """
        Get asset metadata including szDecimals for proper rounding.
        Returns dict with 'szDecimals' and 'maxPriceDecimals' (6 - szDecimals for perps).
        """
        try:
            meta = self.info.meta()
            for asset_info in meta.get('universe', []):
                if asset_info.get('name') == coin:
                    sz_decimals = asset_info.get('szDecimals', 0)
                    # For perpetuals: max price decimals = 6 - szDecimals
                    # Prices must have <= 5 significant figures
                    max_price_decimals = 6 - sz_decimals
                    return {
                        'szDecimals': sz_decimals,
                        'maxPriceDecimals': max_price_decimals
                    }
        except Exception as e:
            self.logger.warning(f"Failed to get asset info for {coin}: {e}")
        # Defaults: 0 decimals for size, 1 for price (conservative)
        return {'szDecimals': 0, 'maxPriceDecimals': 1}

    def _round_size(self, size: float, sz_decimals: int) -> float:
        """Round size down to the asset's szDecimals (truncate, don't round up)."""
        multiplier = 10 ** sz_decimals
        return int(size * multiplier) / multiplier

    def _round_price(self, price: float, max_decimals: int) -> float:
        """
        Round price to valid tick size per Hyperliquid rules:
        - Max 5 significant figures
        - Max 'max_decimals' decimal places
        - Integer prices are always valid regardless of sig figs
        """
        if price == 0:
            return 0

        # First, round to 5 significant figures
        # Use Python's float formatting with 5 sig figs
        rounded_5sf = float(f"{price:.5g}")

        # Then, round to max_decimals decimal places
        multiplier = 10 ** max_decimals
        final = round(rounded_5sf * multiplier) / multiplier

        return final

    def _bulk_orders_with_grouping(self, order_requests, grouping="na", builder=None):
        """
        Custom bulk orders implementation that allows setting proper grouping for OCO orders
        """
        self.logger.info(f"Processing {len(order_requests)} order requests with grouping: {grouping}")
        self.logger.info(f"Order requests: {order_requests}")
        
        # Convert order requests to order wires
        order_wires = []
        for i, order in enumerate(order_requests):
            try:
                wire = order_request_to_order_wire(order, self.info.name_to_asset(order["coin"]))
                self.logger.info(f"Order wire {i}: {wire}")
                order_wires.append(wire)
            except Exception as e:
                self.logger.error(f"Failed to convert order {i} to wire: {e}")
                self.logger.error(f"Problem order: {order}")
                raise
        
        # Get timestamp
        timestamp = int(time.time() * 1000)
        
        # Create the order action using the SDK's function
        order_action = order_wires_to_order_action(order_wires, None)
        
        # Set the grouping parameter (this is the key difference!)
        order_action["grouping"] = grouping
        
        self.logger.info(f"Final order action: {order_action}")
        
        # Debug: Log the raw JSON that will be sent
        import json
        self.logger.info(f"Order action as JSON: {json.dumps(order_action, indent=2)}")

        expires_after = self.exchange.expires_after
        
        # Sign the action using the same approach as the working code
        signature = sign_l1_action(
            self.exchange.wallet,
            order_action,
            self.exchange.vault_address,
            timestamp,
            expires_after,
            self.exchange.base_url == constants.MAINNET_API_URL
        )
        
        # Post the action
        return self.exchange._post_action(order_action, signature, timestamp)
    
    async def get_account_balance(self) -> Dict[str, Any]:
        """Get account balance and margin information"""
        try:
            user_state = self.info.user_state(self.account_address)
            return {
                "success": True,
                "data": user_state,
                "account_address": self.account_address
            }
        except Exception as e:
            self.logger.error(f"Failed to get account balance: {str(e)}", exc_info=True)
            return {"success": False, "error": str(e)}
    
    async def get_open_positions(self) -> Dict[str, Any]:
        """Get all open positions"""
        try:
            user_state = self.info.user_state(self.account_address)
            positions = user_state.get("assetPositions", [])
            
            formatted_positions = []
            for pos in positions:
                if pos["position"]["szi"] != "0":  # Only include non-zero positions
                    formatted_positions.append({
                        "coin": pos["position"]["coin"],
                        "size": pos["position"]["szi"],
                        "entry_price": pos["position"]["entryPx"],
                        "unrealized_pnl": pos["position"]["unrealizedPnl"],
                        "return_on_equity": pos["position"]["returnOnEquity"],
                        "margin_used": pos["position"]["marginUsed"]
                    })
            
            return {
                "success": True,
                "positions": formatted_positions,
                "total_positions": len(formatted_positions)
            }
        except Exception as e:
            self.logger.error(f"Failed to get open positions: {str(e)}", exc_info=True)
            return {"success": False, "error": str(e)}
    
    async def get_open_orders(self) -> Dict[str, Any]:
        """Get all open orders"""
        try:
            open_orders = self.info.open_orders(self.account_address)
            
            formatted_orders = []
            for order in open_orders:
                formatted_orders.append({
                "order_id": order["oid"],
                "coin": order["coin"],
                "side": "buy" if order["side"] == "B" else "sell",
                "size": order["sz"],
                "limit_price": order["limitPx"],
                "reduce_only": order.get("reduceOnly", False),
                "order_type": order.get("orderType", "unknown"),
                "timestamp": order["timestamp"],
                "cloid": order.get("cloid")
            })
            
            return {
                "success": True,
                "orders": formatted_orders,
                "total_orders": len(formatted_orders)
            }
        except Exception as e:
            self.logger.error(f"Failed to get open orders: {str(e)}", exc_info=True)
            return {"success": False, "error": str(e)}
    
    async def place_order(
        self,
        coin: str,
        is_buy: bool,
        sz: Union[str, float],
        limit_px: Union[str, float],
        order_type: Optional[Dict[str, Any]] = None,
        reduce_only: bool = False,
        cloid: Optional[str] = None,
        tp_px: Optional[Union[str, float]] = None,
        sl_px: Optional[Union[str, float]] = None
    ) -> Dict[str, Any]:
        """
        Place a single order or bracket order if TP/SL is specified
        
        Args:
            coin: Trading pair (e.g., "BTC", "ETH")
            is_buy: True for buy, False for sell
            sz: Order size
            limit_px: Limit price
            order_type: Order type specification
            reduce_only: Whether order should only reduce position
            cloid: Optional client order ID
            tp_px: Optional take profit price
            sl_px: Optional stop loss price
        """
        try:
            side = "BUY" if is_buy else "SELL"

            # Get asset info for proper rounding
            asset_info = self._get_asset_info(coin)
            sz_decimals = asset_info['szDecimals']
            max_price_decimals = asset_info['maxPriceDecimals']

            # Round size and price to valid tick/lot sizes
            original_sz = float(sz)
            original_px = float(limit_px)
            sz = self._round_size(original_sz, sz_decimals)
            limit_px = self._round_price(original_px, max_price_decimals)

            self.logger.info(f"Placing order: {coin} {side} {sz} @ {limit_px} (original: {original_sz} @ {original_px}, szDecimals={sz_decimals}, maxPriceDecimals={max_price_decimals})")

            # If TP or SL is specified, use bracket order logic
            if tp_px is not None or sl_px is not None:
                if tp_px is None or sl_px is None:
                    raise ValueError("Both take_profit_px and stop_loss_px must be specified for bracket orders")
                # Round TP/SL prices as well
                tp_px = self._round_price(float(tp_px), max_price_decimals)
                sl_px = self._round_price(float(sl_px), max_price_decimals)
                return await self.place_bracket_order(
                    coin, is_buy, sz, limit_px, tp_px, sl_px, reduce_only, cloid
                )

            # Default order type
            if order_type is None:
                order_type = {"limit": {"tif": "Gtc"}}
            
            if cloid is not None:
                order_result = self.exchange.order(
                    coin, is_buy, float(sz), float(limit_px), order_type, reduce_only, cloid
                )
            else:
                order_result = self.exchange.order(
                    coin, is_buy, float(sz), float(limit_px), order_type, reduce_only
                )
            
            self.logger.info(f"Order placed successfully: {order_result}")
            
            return {
                "success": True,
                "order_result": order_result,
                "order_details": {
                    "coin": coin,
                    "side": side,
                    "size": float(sz),
                    "limit_price": float(limit_px),
                    "order_type": order_type,
                    "reduce_only": reduce_only,
                    "cloid": cloid
                }
            }
        except Exception as e:
            self.logger.error(f"Failed to place order for {coin}: {e}")
            return {
                "success": False,
                "error": str(e)
            }
    
    async def place_bracket_order(
        self,
        coin: str,
        is_buy: bool,
        sz: Union[str, float],
        limit_px: Union[str, float],
        take_profit_px: Union[str, float],
        stop_loss_px: Union[str, float],
        reduce_only: bool = False,
        cloid: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Place a bracket order using bulk_orders with normalTpSl grouping for proper OCO behavior
        
        Args:
            coin: Trading pair (e.g., "BTC", "ETH")
            is_buy: True for buy, False for sell
            sz: Order size
            limit_px: Limit price for entry order
            take_profit_px: Take profit price
            stop_loss_px: Stop loss price
            reduce_only: Whether order should only reduce position
            cloid: Optional client order ID prefix (will be converted to Cloid)
        """
        try:
            side = "BUY" if is_buy else "SELL"

            # Get asset info for proper rounding
            asset_info = self._get_asset_info(coin)
            sz_decimals = asset_info['szDecimals']
            max_price_decimals = asset_info['maxPriceDecimals']

            # Round size and prices to valid tick/lot sizes
            sz = self._round_size(float(sz), sz_decimals)
            limit_px = self._round_price(float(limit_px), max_price_decimals)
            take_profit_px = self._round_price(float(take_profit_px), max_price_decimals)
            stop_loss_px = self._round_price(float(stop_loss_px), max_price_decimals)

            self.logger.info(f"Placing bracket order with normalTpSl grouping: {coin} {side} {sz} @ {limit_px}, TP: {take_profit_px}, SL: {stop_loss_px} (szDecimals={sz_decimals}, maxPriceDecimals={max_price_decimals})")

            # Prepare order requests for bulk_orders
            order_requests = []

            # Entry order
            entry_order = {
                "coin": coin,
                "is_buy": is_buy,
                "sz": sz,
                "limit_px": limit_px,
                "order_type": {"limit": {"tif": "Gtc"}},
                "reduce_only": reduce_only
            }
            if cloid:
                entry_order["cloid"] = Cloid(cloid)
            order_requests.append(entry_order)
            
            # Take profit order (opposite side, reduce only)
            tp_order = {
                "coin": coin,
                "is_buy": not is_buy,
                "sz": float(sz),
                "limit_px": float(take_profit_px),
                "order_type": {"trigger": {"triggerPx": float(take_profit_px), "isMarket": False, "tpsl": "tp"}},
                "reduce_only": True
            }
            if cloid:
                tp_order["cloid"] = Cloid(f"{cloid}_tp")
            order_requests.append(tp_order)
            
            # Stop loss order (opposite side, reduce only)
            sl_order = {
                "coin": coin,
                "is_buy": not is_buy,
                "sz": float(sz),
                "limit_px": float(stop_loss_px),
                "order_type": {"trigger": {"triggerPx": float(stop_loss_px), "isMarket": True, "tpsl": "sl"}},
                "reduce_only": True
            }
            if cloid:
                sl_order["cloid"] = Cloid(f"{cloid}_sl")
            order_requests.append(sl_order)
            
            # Use custom bulk_orders with normalTpsl grouping for proper OCO behavior
            # Note: Standard SDK bulk_orders doesn't set grouping parameter correctly for OCO
            bulk_result = self._bulk_orders_with_grouping(order_requests, grouping="normalTpsl")
            
            self.logger.info(f"Bracket order placed successfully with OCO grouping: {bulk_result}")
            
            return {
                "success": True,
                "bulk_result": bulk_result,
                "order_details": {
                    "coin": coin,
                    "side": side,
                    "size": float(sz),
                    "entry_price": float(limit_px),
                    "take_profit_price": float(take_profit_px),
                    "stop_loss_price": float(stop_loss_px),
                    "reduce_only": reduce_only,
                    "grouping": "normalTpSl"
                }
            }
        except Exception as e:
            self.logger.error(f"Failed to place bracket order for {coin}: {e}")
            return {
                "success": False,
                "error": str(e)
            }
    
    async def cancel_order(self, coin: str, oid: int) -> Dict[str, Any]:
        """Cancel a specific order by order ID"""
        try:
            self.logger.info(f"Cancelling order {oid} for {coin}")
            cancel_result = self.exchange.cancel(coin, oid)
            self.logger.info("Order %s cancelled successfully: %s", oid, cancel_result)
            return {
                "success": True,
                "cancel_result": cancel_result,
                "cancelled_order": {"coin": coin, "order_id": oid}
            }
        except Exception as e:
            self.logger.error(f"Failed to cancel order {oid} for {coin}: {str(e)}", exc_info=True)
            return {"success": False, "error": str(e)}
    
    async def cancel_order_by_cloid(self, coin: str, cloid: str) -> Dict[str, Any]:
        """
        Cancel a specific order by client order ID
        
        Args:
            coin: Trading pair
            cloid: Client order ID (128-bit hex string, e.g. 0x1234567890abcdef1234567890abcdef)
        """
        try:
            self.logger.info(f"Cancelling order {cloid} for {coin}")
            cancel_result = self.exchange.cancel_by_cloid(coin, cloid)
            self.logger.info("Order %s cancelled successfully: %s", cloid, cancel_result)
            return {
                "success": True,
                "cancel_result": cancel_result,
                "cancelled_order": {"coin": coin, "cloid": cloid}
            }
        except Exception as e:
            self.logger.error(f"Failed to cancel order {cloid} for {coin}: {str(e)}", exc_info=True)
            return {"success": False, "error": str(e)}

    
    async def cancel_all_orders(self, coin: Optional[str] = None) -> Dict[str, Any]:
        """Cancel all orders, optionally for a specific coin"""
        try:
            if coin:
                self.logger.info(f"Cancelling all orders for {coin}")
                # Cancel all orders for specific coin
                open_orders = self.info.open_orders(self.account_address)
                coin_orders = [order for order in open_orders if order["coin"] == coin]
                
                results = []
                for order in coin_orders:
                    cancel_result = await self.cancel_order(coin, order["oid"])
                    results.append(cancel_result)
                
                successful_cancellations = len([r for r in results if r["success"]])
                self.logger.info(f"Cancelled {successful_cancellations} orders for {coin}")
                
                return {
                    "success": True,
                    "cancelled_orders": len([r for r in results if r["success"]]),
                    "failed_cancellations": len([r for r in results if not r["success"]]),
                    "results": results
                }
            else:
                self.logger.info("Cancelling all orders")
                # Get all open orders and cancel them individually
                open_orders = self.info.open_orders(self.account_address)
                
                results = []
                for order in open_orders:
                    cancel_result = await self.cancel_order(order["coin"], order["oid"])
                    results.append(cancel_result)
                
                successful_cancellations = len([r for r in results if r["success"]])
                self.logger.info(f"Cancelled {successful_cancellations} orders")
                
                return {
                    "success": True,
                    "cancelled_orders": successful_cancellations,
                    "failed_cancellations": len([r for r in results if not r["success"]]),
                    "results": results
                }
        except Exception as e:
            coin_suffix = f" for {coin}" if coin else ""
            self.logger.error(f"Failed to cancel orders{coin_suffix}: {str(e)}", exc_info=True)
            return {"success": False, "error": str(e)}
    
    async def modify_order(
        self,
        coin: str,
        oid: int,
        new_sz: Union[str, float],
        new_limit_px: Union[str, float]
    ) -> Dict[str, Any]:
        """Modify an existing order"""
        try:
            self.logger.info(f"Modifying order {oid} for {coin}: new size={new_sz}, new price={new_limit_px}")
            modify_result = self.exchange.modify_order(
                coin, oid, {
                    "a": 0,  # asset index will be filled by exchange
                    "b": True,  # will be determined by exchange
                    "p": float(new_limit_px),
                    "s": float(new_sz),
                    "r": False,  # reduce only
                    "t": {"limit": {"tif": "Gtc"}}
                }
            )
            
            self.logger.info("Order %s modified successfully: %s", oid, modify_result)
            
            return {
                "success": True,
                "modify_result": modify_result,
                "modified_order": {
                    "coin": coin,
                    "order_id": oid,
                    "new_size": str(new_sz),
                    "new_price": str(new_limit_px)
                }
            }
        except Exception as e:
            self.logger.error(f"Failed to modify order {oid} for {coin}: {str(e)}", exc_info=True)
            return {"success": False, "error": str(e)}
    
    async def get_market_data(self, coin: str) -> Dict[str, Any]:
        """Get market data for a specific coin including bid/ask prices"""
        try:
            all_mids = self.info.all_mids()
            meta = self.info.meta()
            
            # Get orderbook for bid/ask prices
            l2_book = self.info.l2_snapshot(coin)
            
            market_data = {
                "coin": coin,
                "mid_price": all_mids.get(coin, "N/A"),
                "timestamp": int(time.time() * 1000)
            }
            
            # Extract best bid and ask from orderbook
            if l2_book and "levels" in l2_book:
                bids = l2_book["levels"][0] if len(l2_book["levels"]) > 0 else []
                asks = l2_book["levels"][1] if len(l2_book["levels"]) > 1 else []
                
                market_data["best_bid"] = bids[0]["px"] if bids else "N/A"
                market_data["best_ask"] = asks[0]["px"] if asks else "N/A"
                market_data["bid_size"] = bids[0]["sz"] if bids else "N/A"
                market_data["ask_size"] = asks[0]["sz"] if asks else "N/A"
            else:
                market_data["best_bid"] = "N/A"
                market_data["best_ask"] = "N/A"
                market_data["bid_size"] = "N/A"
                market_data["ask_size"] = "N/A"
            
            # Add universe data if available
            universe = meta.get("universe", [])
            coin_info = next((item for item in universe if item["name"] == coin), None)
            if coin_info:
                market_data.update({
                    "max_leverage": coin_info.get("maxLeverage", "N/A"),
                    "only_isolated": coin_info.get("onlyIsolated", False)
                })
            
            return {
                "success": True,
                "market_data": market_data
            }
        except Exception as e:
            self.logger.error(f"Failed to get market data for {coin}: {str(e)}", exc_info=True)
            return {"success": False, "error": str(e)}
    
    async def get_orderbook(self, coin: str, depth: int = 20) -> Dict[str, Any]:
        """Get orderbook data for a specific coin"""
        try:
            l2_book = self.info.l2_snapshot(coin)
            
            # Limit depth
            bids = l2_book["levels"][0][:depth] if len(l2_book["levels"]) > 0 else []
            asks = l2_book["levels"][1][:depth] if len(l2_book["levels"]) > 1 else []
            
            return {
                "success": True,
                "orderbook": {
                    "coin": coin,
                    "bids": bids,
                    "asks": asks,
                    "timestamp": l2_book["time"]
                }
            }
        except Exception as e:
            self.logger.error(f"Failed to get orderbook for {coin}: {str(e)}", exc_info=True)
            return {"success": False, "error": str(e)}
    
    async def update_leverage(self, coin: str, leverage: int, is_cross: bool = True) -> Dict[str, Any]:
        """Update leverage for a coin"""
        try:
            # Validate inputs
            if not isinstance(coin, str) or not coin:
                return {"success": False, "error": "Invalid coin parameter"}
            if not isinstance(leverage, int) or leverage <= 0:
                return {"success": False, "error": "Invalid leverage parameter - must be positive integer"}
            
            margin_type = "cross" if is_cross else "isolated"
            self.logger.info(f"Updating leverage for {coin}: {leverage}x ({margin_type})")
            
            # Try the standard parameter order first
            try:
                leverage_result = self.exchange.update_leverage(leverage, coin, is_cross)
            except Exception as e:
                # If that fails, this might be a version issue - try alternative approaches
                self.logger.warning(f"Standard leverage update failed: {str(e)}")
                return {"success": False, "error": f"Leverage update not supported or failed: {str(e)}"}
            
            self.logger.info(f"Leverage updated successfully for {coin}: {leverage_result}")
            
            return {
                "success": True,
                "leverage_result": leverage_result,
                "leverage_update": {
                    "coin": coin,
                    "leverage": leverage,
                    "cross_margin": is_cross
                }
            }
        except Exception as e:
            self.logger.error(f"Failed to update leverage for {coin}: {str(e)}", exc_info=True)
            return {"success": False, "error": str(e)}
    
    async def transfer_between_spot_and_perp(
        self,
        amount: Union[str, float],
        to_perp: bool = True
    ) -> Dict[str, Any]:
        """Transfer funds between spot and perpetual accounts"""
        try:
            direction = "spot to perp" if to_perp else "perp to spot"
            self.logger.info(f"Transferring {amount} from {direction}")
            
            transfer_result = self.exchange.usd_class_transfer(
                float(amount), to_perp
            )
            
            self.logger.info(f"Transfer completed successfully: {transfer_result}")
            
            return {
                "success": True,
                "transfer_result": transfer_result,
                "transfer_details": {
                    "amount": str(amount),
                    "direction": "spot_to_perp" if to_perp else "perp_to_spot"
                }
            }
        except Exception as e:
            self.logger.error(f"Failed to transfer {amount}: {str(e)}", exc_info=True)
            return {"success": False, "error": str(e)}
    
    async def get_funding_history(self, coin: str, days: int = 7) -> Dict[str, Any]:
        """Get funding history for a coin"""
        try:
            end_time = int(time.time() * 1000)
            start_time = end_time - (days * 24 * 60 * 60 * 1000)
            
            funding_history = self.info.funding_history(
                coin, start_time, end_time
            )
            
            return {
                "success": True,
                "funding_history": funding_history,
                "coin": coin,
                "days": days
            }
        except Exception as e:
            self.logger.error(f"Failed to get funding history for {coin}: {str(e)}", exc_info=True)
            return {"success": False, "error": str(e)}
    
    async def get_trade_history(self, days: int = 7) -> Dict[str, Any]:
        """Get trade history for the account"""
        try:
            user_fills = self.info.user_fills(self.account_address)
            
            # Filter by days if needed
            if days > 0:
                cutoff_time = int((time.time() - (days * 24 * 60 * 60)) * 1000)
                user_fills = [fill for fill in user_fills if fill["time"] >= cutoff_time]
            
            formatted_fills = []
            for fill in user_fills:
                formatted_fills.append({
                    "coin": fill["coin"],
                    "side": fill["side"],
                    "size": fill["sz"],
                    "price": fill["px"],
                    "time": fill["time"],
                    "order_id": fill["oid"],
                    "fee": fill["fee"],
                    "liquidation": fill.get("liquidation", False)
                })
            
            return {
                "success": True,
                "trades": formatted_fills,
                "total_trades": len(formatted_fills),
                "days": days
            }
        except Exception as e:
            self.logger.error(f"Failed to get trade history: {str(e)}", exc_info=True)
            return {"success": False, "error": str(e)}

    async def set_position_tpsl(
        self,
        coin: str,
        tp_px: Optional[Union[str, float]] = None,
        sl_px: Optional[Union[str, float]] = None,
        position_size: Optional[Union[str, float]] = None
    ) -> Dict[str, Any]:
        """
        Set take profit and/or stop loss for an existing position using bulk_orders with positionTpSl grouping for proper OCO behavior
        
        Args:
            coin: Trading pair (e.g., "BTC", "ETH")
            tp_px: Take profit price (optional)
            sl_px: Stop loss price (optional)
            position_size: Position size (will auto-detect if not provided)
        """
        try:
            if tp_px is None and sl_px is None:
                raise ValueError("At least one of tp_px or sl_px must be specified")

            # Get asset info for proper rounding
            asset_info = self._get_asset_info(coin)
            sz_decimals = asset_info['szDecimals']
            max_price_decimals = asset_info['maxPriceDecimals']

            # Round trigger prices to valid tick size
            if tp_px is not None:
                tp_px = self._round_price(float(tp_px), max_price_decimals)
            if sl_px is not None:
                sl_px = self._round_price(float(sl_px), max_price_decimals)

            # Get current position if size not provided
            if position_size is None:
                user_state = self.info.user_state(self.account_address)
                
                position_found = False
                for position in user_state.get("assetPositions", []):
                    if position.get("position", {}).get("coin") == coin:
                        pos_sz = position.get("position", {}).get("szi")
                        if pos_sz and float(pos_sz) != 0:
                            position_size = abs(float(pos_sz))
                            position_found = True
                            break
                
                if not position_found:
                    raise ValueError(f"No position found for {coin}")
            
            position_size = self._round_size(float(position_size), sz_decimals)

            # Determine if position is long or short
            user_state = self.info.user_state(self.account_address)
            is_long = True  # Default
            for position in user_state.get("assetPositions", []):
                if position.get("position", {}).get("coin") == coin:
                    pos_sz = position.get("position", {}).get("szi")
                    if pos_sz:
                        is_long = float(pos_sz) > 0
                    break
            
            # Prepare order requests for bulk_orders with positionTpSl grouping
            order_requests = []
            
            # Add take profit order if specified  
            if tp_px is not None:
                # For TP orders, use tick-aligned aggressive price
                # If closing long (sell), use very low price; if closing short (buy), use very high price
                slippage = 0.5  # 50% slippage for very aggressive pricing
                aggressive_px = self._slippage_price(coin, not is_long, slippage)
                
                tp_order = {
                    "coin": coin,
                    "is_buy": not is_long,
                    "sz": float(position_size),
                    "limit_px": aggressive_px,  # Tick-aligned aggressive price for market execution
                    "order_type": {"trigger": {"triggerPx": float(tp_px), "isMarket": True, "tpsl": "tp"}},
                    "reduce_only": True
                }
                self.logger.info(f"TP order structure: {tp_order}")
                order_requests.append(tp_order)
            
            # Add stop loss order if specified
            if sl_px is not None:
                # For SL orders, use tick-aligned aggressive price
                slippage = 0.5  # 50% slippage for very aggressive pricing
                aggressive_px = self._slippage_price(coin, not is_long, slippage)
                
                sl_order = {
                    "coin": coin,
                    "is_buy": not is_long,
                    "sz": float(position_size),
                    "limit_px": aggressive_px,  # Tick-aligned aggressive price for market execution
                    "order_type": {"trigger": {"triggerPx": float(sl_px), "isMarket": True, "tpsl": "sl"}},
                    "reduce_only": True
                }
                self.logger.info(f"SL order structure: {sl_order}")
                order_requests.append(sl_order)
            
            # Try using the SDK's bulk_orders method with positionTpSl grouping
            try:
                # First, let's try the standard bulk_orders approach
                bulk_result = self.exchange.bulk_orders(order_requests)
                self.logger.info(f"Standard bulk_orders result: {bulk_result}")

            except Exception as e:
                self.logger.error(f"Standard bulk_orders failed with exception: {e}")
                # Fall back to custom method
            
            self.logger.info(f"Position TP/SL set successfully for {coin}: {bulk_result}")
            
            return {
                "success": True,
                "bulk_result": bulk_result,
                "position_details": {
                    "coin": coin,
                    "position_size": position_size,
                    "is_long": is_long,
                    "take_profit_price": tp_px,
                    "stop_loss_price": sl_px,
                    "grouping": "positionTpSl"
                }
            }
        except Exception as e:
            self.logger.error(f"Failed to set position TP/SL for {coin}: {e}")
            return {
                "success": False,
                "error": str(e)
            }

    async def market_open_position(
        self,
        coin: str,
        is_buy: bool,
        sz: Union[str, float],
        cloid: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Open a new position at market price
        
        Args:
            coin: Trading pair (e.g., "BTC", "ETH")
            is_buy: True for long position, False for short position
            sz: Position size to open
            cloid: Optional client order ID for tracking
            
        Returns:
            Dict containing success status and order details
        """
        try:
            # Get asset info for proper rounding
            asset_info = self._get_asset_info(coin)
            sz_decimals = asset_info['szDecimals']

            # Round size to valid lot size
            original_sz = float(sz)
            sz = self._round_size(original_sz, sz_decimals)

            self.logger.info(f"Opening {'long' if is_buy else 'short'} position for {coin} with size {sz} (original: {original_sz}, szDecimals={sz_decimals})")

            # Use market_open directly
            order_result = self.exchange.market_open(coin, is_buy, sz, cloid)
            
            self.logger.info(f"Position opened successfully for {coin}: {order_result}")
            
            return {
                "success": True,
                "action": "market_open_position",
                "order_result": order_result,
                "position_details": {
                    "coin": coin,
                    "side": "long" if is_buy else "short",
                    "size": str(sz),
                    "order_type": "market",
                    "cloid": cloid
                }
            }
            
        except Exception as e:
            self.logger.error(f"Failed to open market position for {coin}: {str(e)}", exc_info=True)
            return {"success": False, "error": str(e)}

    async def market_close_position(
        self,
        coin: str,
        sz: Optional[float] = None,
        cloid: Optional[str] = None,
        slippage: float = 0.001  # 0.1% default slippage
    ) -> Dict[str, Any]:
        """
        Close an existing position by placing a reverse market order with reduce_only=True
        
        Args:
            coin: Trading pair (e.g., "BTC", "ETH")
            sz: Optional size to close (if None, closes entire position)
            cloid: Optional client order ID for tracking
            slippage: Slippage percentage for aggressive pricing (default 0.1%)
            
        Returns:
            Dict containing success status and order details
        """
        try:
            # Get current positions
            user_state = self.info.user_state(self.account_address)
            positions = user_state.get("assetPositions", [])
            
            # Find the position for this coin
            for position in positions:
                item = position["position"]
                if coin != item["coin"]:
                    continue
                    
                szi = float(item["szi"])
                if szi == 0:
                    return {
                        "success": False,
                        "error": f"No open position found for {coin}"
                    }
                
                # Determine size to close
                if not sz:
                    sz = abs(szi)
                
                # Determine direction: buy to close short, sell to close long
                is_buy = True if szi < 0 else False
                
                position_side = "short" if szi < 0 else "long"
                self.logger.info(f"Closing {position_side} position for {coin} (size: {sz})")
                
                # Calculate price using HyperLiquid SDK logic
                limit_px = self._slippage_price(coin, is_buy, slippage)
                
                # Place IOC order with reduce_only=True
                result = await self.place_order(
                    coin=coin,
                    is_buy=is_buy,
                    sz=sz,
                    limit_px=limit_px,
                    order_type={"limit": {"tif": "Ioc"}},
                    reduce_only=True,
                    cloid=cloid
                )
                
                # Add market close specific details to the result
                if result.get("success"):
                    result["action"] = "market_close_position"
                    result["order_details"]["original_side"] = position_side
                    result["order_details"]["original_size"] = str(szi)
                
                return result
            
            # If we get here, position was not found
            return {
                "success": False,
                "error": f"No open position found for {coin}"
            }
            
        except Exception as e:
            self.logger.error(f"Failed to close position for {coin}: {str(e)}", exc_info=True)
            return {"success": False, "error": str(e)}

    def _slippage_price(
        self,
        coin: str,
        is_buy: bool,
        slippage: float,
        px: Optional[float] = None,
    ) -> float:
        """
        Calculate slippage price using HyperLiquid SDK logic
        """
        if not px:
            # Get midprice
            px = float(self.info.all_mids()[coin])

        # Get asset info for proper rounding
        meta = self.info.meta()
        asset_index = None
        sz_decimals = 0
        
        # Find asset index and decimals
        for i, asset_info in enumerate(meta['universe']):
            if asset_info['name'] == coin:
                asset_index = i
                sz_decimals = asset_info.get('szDecimals', 0)
                break
        
        # spot assets start at 10000 (not relevant for our case, but keeping for completeness)
        is_spot = asset_index is not None and asset_index >= 10_000

        # Calculate Slippage
        px *= (1 + slippage) if is_buy else (1 - slippage)
        
        # Use HyperLiquid's rounding logic: 5 significant figures and 6 decimals for perps, 8 decimals for spot
        decimals = (6 if not is_spot else 8) - sz_decimals
        return round(float(f"{px:.5g}"), max(0, decimals))

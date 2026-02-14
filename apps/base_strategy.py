import asyncio
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional, Dict, List

from lib.terminal_utils import LogBuffer, log
from lib.market_manager import MarketManager, MarketInfo
from lib.price_tracker import PriceTracker
from lib.position_manager import PositionManager, Position
from src.bot import TradingBot
from src.websocket_client import OrderbookSnapshot


@dataclass
class StrategyConfig:

    coin: str = "ETH"
    size: float = 5.0
    max_positions: int = 1
    take_profit: float = 0.10
    stop_loss: float = 0.05

    market_check_interval: float = 30.0
    auto_switch_market: bool = True

    price_lookback_seconds: int = 10
    price_history_size: int = 100

    update_interval: float = 0.1
    order_refresh_interval: float = 30.0


class BaseStrategy(ABC):

    def __init__(self, bot: TradingBot, config: StrategyConfig):
        self.bot = bot
        self.config = config

        self.market = MarketManager(
            coin=config.coin,
            market_check_interval=config.market_check_interval,
            auto_switch_market=config.auto_switch_market,
        )

        self.prices = PriceTracker(
            lookback_seconds=config.price_lookback_seconds,
            max_history=config.price_history_size,
        )

        self.positions = PositionManager(
            take_profit=config.take_profit,
            stop_loss=config.stop_loss,
            max_positions=config.max_positions,
        )

        self.running = False
        self._status_mode = False

        self._log_buffer = LogBuffer(max_size=5)

        self._cached_orders: List[dict] = []
        self._last_order_refresh: float = 0
        self._order_refresh_task: Optional[asyncio.Task] = None

    @property
    def is_connected(self) -> bool:
        return self.market.is_connected

    @property
    def current_market(self) -> Optional[MarketInfo]:
        return self.market.current_market

    @property
    def token_ids(self) -> Dict[str, str]:
        return self.market.token_ids

    @property
    def open_orders(self) -> List[dict]:
        return self._cached_orders

    def _refresh_orders_sync(self) -> List[dict]:
        try:
            import asyncio
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                return loop.run_until_complete(self.bot.get_open_orders())
            finally:
                loop.close()
        except Exception:
            return []

    async def _do_order_refresh(self) -> None:
        try:
            orders = await asyncio.to_thread(self._refresh_orders_sync)
            self._cached_orders = orders
        except Exception:
            pass
        finally:
            self._order_refresh_task = None

    def _maybe_refresh_orders(self) -> None:
        
        now = time.time()
        if now - self._last_order_refresh > self.config.order_refresh_interval:
            
            if self._order_refresh_task is not None and not self._order_refresh_task.done():
                return
            self._last_order_refresh = now
            
            self._order_refresh_task = asyncio.create_task(self._do_order_refresh())

    def log(self, msg: str, level: str = "info") -> None:
        
        if self._status_mode:
            self._log_buffer.add(msg, level)
        else:
            log(msg, level)

    async def start(self) -> bool:
        
        self.running = True

        
        @self.market.on_book_update
        async def handle_book(snapshot: OrderbookSnapshot):  
            
            for side, token_id in self.token_ids.items():
                if token_id == snapshot.asset_id:
                    self.prices.record(side, snapshot.mid_price)
                    break

            
            await self.on_book_update(snapshot)

        @self.market.on_market_change
        def handle_market_change(old_slug: str, new_slug: str):  
            self.log(f"Market changed: {old_slug} -> {new_slug}", "warning")
            self.prices.clear()
            self.on_market_change(old_slug, new_slug)

        @self.market.on_connect
        def handle_connect():  
            self.log("WebSocket connected", "success")
            self.on_connect()

        @self.market.on_disconnect
        def handle_disconnect():  
            self.log("WebSocket disconnected", "warning")
            self.on_disconnect()

        
        if not await self.market.start():
            self.running = False
            self.log("Failed to start strategy: Could not connect to market data", "error")
            self.log("Check network connection and ensure Polymarket services are accessible", "info")
            return False

        
        if not await self.market.wait_for_data(timeout=5.0):
            self.log("Timeout waiting for market data", "warning")

        return True

    async def stop(self) -> None:
        
        self.running = False

        
        if self._order_refresh_task is not None:
            self._order_refresh_task.cancel()
            try:
                await self._order_refresh_task
            except asyncio.CancelledError:
                pass
            self._order_refresh_task = None

        await self.market.stop()

    async def run(self) -> None:
        
        try:
            if not await self.start():
                self.log("Failed to start strategy", "error")
                return

            self._status_mode = True

            while self.running:
                
                prices = self._get_current_prices()

                
                await self.on_tick(prices)

                
                await self._check_exits(prices)

                
                self._maybe_refresh_orders()

                
                self.render_status(prices)

                await asyncio.sleep(self.config.update_interval)

        except KeyboardInterrupt:
            self.log("Strategy stopped by user")
        finally:
            await self.stop()
            self._print_summary()

    def _get_current_prices(self) -> Dict[str, float]:
        
        prices = {}
        for side in ["up", "down"]:
            price = self.market.get_mid_price(side)
            if price > 0:
                prices[side] = price
        return prices

    async def _check_exits(self, prices: Dict[str, float]) -> None:
        
        exits = self.positions.check_all_exits(prices)

        for position, exit_type, pnl in exits:
            if exit_type == "take_profit":
                self.log(
                    f"TAKE PROFIT: {position.side.upper()} PnL: +${pnl:.2f}",
                    "success"
                )
            elif exit_type == "stop_loss":
                self.log(
                    f"STOP LOSS: {position.side.upper()} PnL: ${pnl:.2f}",
                    "warning"
                )

            
            await self.execute_sell(position, prices.get(position.side, 0))

    async def execute_buy(self, side: str, current_price: float) -> bool:
        
        token_id = self.token_ids.get(side)
        if not token_id:
            self.log(f"No token ID for {side}", "error")
            return False

        size = self.config.size / current_price
        buy_price = min(current_price + 0.02, 0.99)

        self.log(f"BUY {side.upper()} @ {current_price:.4f} size={size:.2f}", "trade")

        result = await self.bot.place_order(
            token_id=token_id,
            price=buy_price,
            size=size,
            side="BUY"
        )

        if result.success:
            self.log(f"Order placed: {result.order_id}", "success")
            self.positions.open_position(
                side=side,
                token_id=token_id,
                entry_price=current_price,
                size=size,
                order_id=result.order_id,
            )
            return True
        else:
            self.log(f"Order failed: {result.message}", "error")
            return False

    async def execute_sell(self, position: Position, current_price: float) -> bool:
        
        sell_price = max(current_price - 0.02, 0.01)
        pnl = position.get_pnl(current_price)

        result = await self.bot.place_order(
            token_id=position.token_id,
            price=sell_price,
            size=position.size,
            side="SELL"
        )

        if result.success:
            self.log(f"Sell order: {result.order_id} PnL: ${pnl:+.2f}", "success")
            self.positions.close_position(position.id, realized_pnl=pnl)
            return True
        else:
            self.log(f"Sell failed: {result.message}", "error")
            return False

    def _print_summary(self) -> None:
        
        self._status_mode = False
        print()
        stats = self.positions.get_stats()
        self.log("Session Summary:")
        self.log(f"  Trades: {stats['trades_closed']}")
        self.log(f"  Total PnL: ${stats['total_pnl']:+.2f}")
        self.log(f"  Win rate: {stats['win_rate']:.1f}%")

    

    @abstractmethod
    async def on_book_update(self, snapshot: OrderbookSnapshot) -> None:
        
        pass

    @abstractmethod
    async def on_tick(self, prices: Dict[str, float]) -> None:
        
        pass

    @abstractmethod
    def render_status(self, prices: Dict[str, float]) -> None:
        
        pass

    

    def on_market_change(self, old_slug: str, new_slug: str) -> None:
        
        pass

    def on_connect(self) -> None:
        
        pass

    def on_disconnect(self) -> None:
        
        pass


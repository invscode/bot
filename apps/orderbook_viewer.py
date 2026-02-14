#!/usr/bin/env python3
"""
Polymarket Arbitrage Bot - Real-time Orderbook Terminal UI

A beautiful terminal-based user interface for viewing real-time orderbook
data for Polymarket 15-minute prediction markets. This tool provides an
interactive display of market depth, prices, and market information.

Features:
    - Real-time WebSocket orderbook updates with live refresh
    - Dual orderbook display (Up/Down tokens side-by-side)
    - Market countdown timer showing time until market resolution
    - Price history tracking with visual indicators
    - Configurable orderbook depth levels
    - Color-coded display for easy reading
    - Automatic market discovery for selected coin

Usage:
    # View ETH 15-minute market orderbook
    python apps/orderbook_viewer.py --coin ETH

    # View BTC market with custom depth
    python apps/orderbook_viewer.py --coin BTC --levels 10

    # Full argument list
    python apps/orderbook_viewer.py --coin ETH --levels 5

Arguments:
    --coin      Coin symbol (BTC, ETH, SOL, XRP) [default: ETH]
    --levels    Number of price levels to display [default: 5]

Prerequisites:
    - Python 3.8 or higher
    - All dependencies installed (see requirements.txt)
    - Terminal that supports ANSI color codes (most modern terminals)

Note:
    This is a read-only monitoring tool. No trades are executed.
    Press Ctrl+C to exit the application.
"""

import sys
import asyncio
import logging
from pathlib import Path
import yaml

# Suppress noisy logs
logging.getLogger("src.websocket_client").setLevel(logging.WARNING)

# Auto-load .env file
from dotenv import load_dotenv
load_dotenv()

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from lib import MarketManager, PriceTracker, Colors
from lib.terminal_utils import format_countdown


class OrderbookTUI:
    """Real-time orderbook viewer."""

    def __init__(self, coin: str = "ETH"):
        """Initialize TUI."""
        self.coin = coin.upper()
        self.market = MarketManager(coin=self.coin)
        self.prices = PriceTracker()
        self.running = False

    async def run(self) -> None:
        """Run the TUI."""
        self.running = True

        # Register callbacks
        @self.market.on_book_update
        async def handle_book(snapshot):  # pyright: ignore[reportUnusedFunction]
            for side, token_id in self.market.token_ids.items():
                if token_id == snapshot.asset_id:
                    self.prices.record(side, snapshot.mid_price)
                    break

        @self.market.on_connect
        def on_connect():  # pyright: ignore[reportUnusedFunction]
            pass

        @self.market.on_disconnect
        def on_disconnect():  # pyright: ignore[reportUnusedFunction]
            pass

        # Start market manager
        if not await self.market.start():
            print(f"{Colors.RED}Failed to start market manager{Colors.RESET}")
            print(f"{Colors.YELLOW}Possible issues:{Colors.RESET}")
            print(f"  1. Network connection problem - check your internet")
            print(f"  2. No active market for {self.coin}")
            print(f"  3. Gamma API not responding")
            return

        await self.market.wait_for_data(timeout=5.0)

        try:
            while self.running:
                self.render()
                await asyncio.sleep(0.1)
        except KeyboardInterrupt:
            pass
        finally:
            await self.market.stop()

    def render(self) -> None:
        """Render the display."""
        lines = []

        # Header
        ws_status = f"{Colors.GREEN}Connected{Colors.RESET}" if self.market.is_connected else f"{Colors.RED}Disconnected{Colors.RESET}"
        market = self.market.current_market
        countdown = "--:--"
        if market:
            mins, secs = market.get_countdown()
            countdown = format_countdown(mins, secs)

        lines.append(f"{Colors.BOLD}{'='*80}{Colors.RESET}")
        lines.append(f"{Colors.CYAN}Orderbook TUI{Colors.RESET} | {self.coin} | {ws_status} | Ends: {countdown}")
        lines.append(f"{Colors.BOLD}{'='*80}{Colors.RESET}")

        # Market info
        if market:
            lines.append(f"Market: {market.question}")
            lines.append(f"Slug: {market.slug}")
            lines.append("")

        # Orderbook display - UP section
        up_ob = self.market.get_orderbook("up")
        lines.append(f"{Colors.GREEN}{Colors.BOLD}UP Orderbook{Colors.RESET}")
        lines.append(f"{'Bid':>9} {'Size':>9} | {'Ask':>9} {'Size':>9}")
        lines.append("-" * 80)

        # Get 10 levels for UP
        up_bids = up_ob.bids[:10] if up_ob else []
        up_asks = up_ob.asks[:10] if up_ob else []

        for i in range(10):
            up_bid = f"{up_bids[i].price:>9.4f} {up_bids[i].size:>9.1f}" if i < len(up_bids) else f"{'--':>9} {'--':>9}"
            up_ask = f"{up_asks[i].price:>9.4f} {up_asks[i].size:>9.1f}" if i < len(up_asks) else f"{'--':>9} {'--':>9}"
            lines.append(f"{up_bid} | {up_ask}")

        # UP summary
        up_mid = up_ob.mid_price if up_ob else 0
        up_spread = self.market.get_spread("up")
        lines.append("-" * 80)
        lines.append(f"Mid: {Colors.GREEN}{up_mid:.4f}{Colors.RESET}  Spread: {up_spread:.4f}")
        lines.append(f"{Colors.BOLD}{'='*80}{Colors.RESET}")

        # Orderbook display - DOWN section
        down_ob = self.market.get_orderbook("down")
        lines.append(f"{Colors.RED}{Colors.BOLD}DOWN Orderbook{Colors.RESET}")
        lines.append(f"{'Bid':>9} {'Size':>9} | {'Ask':>9} {'Size':>9}")
        lines.append("-" * 80)

        # Get 10 levels for DOWN
        down_bids = down_ob.bids[:10] if down_ob else []
        down_asks = down_ob.asks[:10] if down_ob else []

        for i in range(10):
            down_bid = f"{down_bids[i].price:>9.4f} {down_bids[i].size:>9.1f}" if i < len(down_bids) else f"{'--':>9} {'--':>9}"
            down_ask = f"{down_asks[i].price:>9.4f} {down_asks[i].size:>9.1f}" if i < len(down_asks) else f"{'--':>9} {'--':>9}"
            lines.append(f"{down_bid} | {down_ask}")

        # DOWN summary
        down_mid = down_ob.mid_price if down_ob else 0
        down_spread = self.market.get_spread("down")
        lines.append("-" * 80)
        lines.append(f"Mid: {Colors.RED}{down_mid:.4f}{Colors.RESET}  Spread: {down_spread:.4f}")

        # Price history stats
        up_history = self.prices.get_history_count("up")
        down_history = self.prices.get_history_count("down")

        up_vol = self.prices.get_volatility("up", 60)
        down_vol = self.prices.get_volatility("down", 60)

        lines.append("")
        lines.append(f"History: UP={up_history} DOWN={down_history} | 60s Volatility: UP={up_vol:.4f} DOWN={down_vol:.4f}")

        lines.append(f"{Colors.BOLD}{'='*80}{Colors.RESET}")
        lines.append(f"{Colors.DIM}Press Ctrl+C to exit{Colors.RESET}")

        # Render
        output = "\033[H\033[J" + "\n".join(lines)
        print(output, flush=True)


def load_config():
    """Load configuration from YAML file in the same directory."""
    config_file = Path(__file__).parent / "orderbook_config.yaml"

    if not config_file.exists():
        print(f"{Colors.RED}错误: 配置文件 '{config_file}' 不存在{Colors.RESET}")
        print(f"请在 '{config_file.parent}' 目录下创建配置文件 'orderbook_config.yaml'")
        print(f"\n示例配置文件内容:")
        print(f"""
# Orderbook Viewer 配置文件
coin: ETH
""")
        sys.exit(1)

    try:
        with open(config_file, 'r', encoding='utf-8') as f:
            config = yaml.safe_load(f)
    except yaml.YAMLError as e:
        print(f"{Colors.RED}错误: 配置文件 YAML 格式错误: {e}{Colors.RESET}")
        sys.exit(1)
    except Exception as e:
        print(f"{Colors.RED}错误: 读取配置文件失败: {e}{Colors.RESET}")
        sys.exit(1)

    if config is None:
        print(f"{Colors.RED}错误: 配置文件为空{Colors.RESET}")
        sys.exit(1)

    if 'coin' not in config:
        print(f"{Colors.RED}错误: 配置文件中缺少必需的参数 'coin'{Colors.RESET}")
        sys.exit(1)

    coin = config['coin']

    if not isinstance(coin, str):
        print(f"{Colors.RED}错误: 参数 'coin' 必须是字符串类型{Colors.RESET}")
        sys.exit(1)

    coin = coin.upper().strip()

    if not coin:
        print(f"{Colors.RED}错误: 参数 'coin' 不能为空{Colors.RESET}")
        sys.exit(1)

    valid_coins = ["BTC", "ETH", "SOL", "XRP"]
    if coin not in valid_coins:
        print(f"{Colors.RED}错误: 无效的币种 '{coin}'。支持的币种: {', '.join(valid_coins)}{Colors.RESET}")
        sys.exit(1)

    return coin


def main():
    """Main entry point."""
    coin = load_config()
    tui = OrderbookTUI(coin=coin)

    try:
        asyncio.run(tui.run())
    except KeyboardInterrupt:
        print("\nExiting...")


if __name__ == "__main__":
    main()

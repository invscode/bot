#!/usr/bin/env python3
"""
Polymarket Arbitrage Bot - Flash Crash Strategy Runner

Command-line entry point for running the Flash Crash trading strategy
on Polymarket 15-minute markets. This script provides a convenient way
to start the strategy with customizable parameters.

Usage:
    # Run with default settings
    python apps/flash_crash_runner.py --coin ETH

    # Customize trade size
    python apps/flash_crash_runner.py --coin BTC --size 10

    # Adjust drop threshold and other parameters
    python apps/flash_crash_runner.py --coin BTC --drop 0.25 --lookback 15

    # Full parameter list
    python apps/flash_crash_runner.py \\
        --coin BTC \\
        --drop 0.30 \\
        --size 10.0 \\
        --lookback 10 \\
        --take-profit 0.10 \\
        --stop-loss 0.05

Arguments:
    --coin          Coin symbol (BTC, ETH, SOL, XRP) [default: ETH]
    --drop          Drop threshold as absolute change [default: 0.30]
    --size          Trade size in USDC [default: 5.0]
    --lookback      Detection window in seconds [default: 10]
    --take-profit   Take profit in dollars [default: 0.10]
    --stop-loss     Stop loss in dollars [default: 0.05]

Prerequisites:
    - Python 3.8 or higher
    - All dependencies installed (see requirements.txt)
    - A .env file with POLY_PRIVATE_KEY and POLY_PROXY_WALLET

Risk Warning:
    This strategy involves financial risk. Test thoroughly with small
    amounts before committing larger funds. Past performance does not
    guarantee future results.
"""

import os
import sys
import asyncio
import logging
from pathlib import Path
import yaml

# Suppress noisy logs (but allow INFO level to see WebSocket messages)
logging.getLogger("src.websocket_client").setLevel(logging.INFO)
logging.getLogger("src.bot").setLevel(logging.WARNING)

# Auto-load .env file
from dotenv import load_dotenv
load_dotenv()

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from lib.terminal_utils import Colors
from src.bot import TradingBot
from apps.flash_crash_strategy import FlashCrashStrategy, FlashCrashConfig


def load_config():
    """Load configuration from YAML file in the same directory."""
    config_file = Path(__file__).parent / "flash_crash_config.yaml"

    if not config_file.exists():
        print(f"{Colors.RED}错误: 配置文件 '{config_file}' 不存在{Colors.RESET}")
        print(f"请在 '{config_file.parent}' 目录下创建配置文件 'flash_crash_config.yaml'")
        print(f"\n示例配置文件内容:")
        print(f"""
# Flash Crash Strategy 配置文件
coin: ETH           # 币种 (BTC, ETH, SOL, XRP)
size: 5.0            # 交易金额 (USDC)
drop: 0.30           # 下跌阈值 (概率变化)
lookback: 10         # 回溯窗口 (秒)
take_profit: 0.10    # 止盈 (美元)
stop_loss: 0.05      # 止损 (美元)
debug: false         # 是否启用调试日志
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

    # 验证必需参数
    required_params = ['coin', 'size', 'drop', 'lookback', 'take_profit', 'stop_loss']
    missing_params = [p for p in required_params if p not in config]

    if missing_params:
        print(f"{Colors.RED}错误: 配置文件中缺少必需的参数: {', '.join(missing_params)}{Colors.RESET}")
        sys.exit(1)

    # 验证参数类型和值
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

    # 验证数值参数
    numeric_params = {
        'size': (float, 0.01, float('inf')),
        'drop': (float, 0.0, 1.0),
        'lookback': (int, 1, 300),
        'take_profit': (float, 0.0, float('inf')),
        'stop_loss': (float, 0.0, float('inf'))
    }

    for param_name, (param_type, min_val, max_val) in numeric_params.items():
        value = config[param_name]

        if not isinstance(value, param_type):
            print(f"{Colors.RED}错误: 参数 '{param_name}' 必须是 {param_type.__name__} 类型{Colors.RESET}")
            sys.exit(1)

        if value < min_val or value > max_val:
            print(f"{Colors.RED}错误: 参数 '{param_name}' 的值 {value} 超出有效范围 [{min_val}, {max_val}]{Colors.RESET}")
            sys.exit(1)

    # 可选参数 debug
    debug = config.get('debug', False)
    if not isinstance(debug, bool):
        print(f"{Colors.RED}错误: 参数 'debug' 必须是布尔类型{Colors.RESET}")
        sys.exit(1)

    return {
        'coin': coin,
        'size': config['size'],
        'drop': config['drop'],
        'lookback': config['lookback'],
        'take_profit': config['take_profit'],
        'stop_loss': config['stop_loss'],
        'debug': debug
    }


def display_config(config):
    """Display loaded configuration and wait for user confirmation."""
    print(f"\n{Colors.BOLD}{'='*60}{Colors.RESET}")
    print(f"{Colors.BOLD}  Flash Crash Strategy - {config['coin']} 15-Minute Markets{Colors.RESET}")
    print(f"{Colors.BOLD}{'='*60}{Colors.RESET}\n")

    print(f"{Colors.CYAN}配置参数:{Colors.RESET}")
    print(f"  币种 (coin):           {Colors.GREEN}{config['coin']}{Colors.RESET}")
    print(f"  交易金额 (size):        {Colors.GREEN}${config['size']:.2f}{Colors.RESET}")
    print(f"  下跌阈值 (drop):       {Colors.GREEN}{config['drop']:.2f}{Colors.RESET}")
    print(f"  回溯窗口 (lookback):    {Colors.GREEN}{config['lookback']}s{Colors.RESET}")
    print(f"  止盈 (take_profit):     {Colors.GREEN}+${config['take_profit']:.2f}{Colors.RESET}")
    print(f"  止损 (stop_loss):       {Colors.GREEN}-${config['stop_loss']:.2f}{Colors.RESET}")
    print(f"  调试模式 (debug):       {Colors.GREEN}{config['debug']}{Colors.RESET}")
    print()

    print(f"{Colors.YELLOW}按 {Colors.BOLD}回车键{Colors.YELLOW} 继续执行，按 {Colors.BOLD}Ctrl+C{Colors.YELLOW} 退出{Colors.RESET}")

    try:
        input()
    except KeyboardInterrupt:
        print(f"\n{Colors.YELLOW}用户取消{Colors.RESET}")
        sys.exit(0)
    except EOFError:
        # 在某些情况下 input() 可能抛出 EOFError
        print(f"\n{Colors.YELLOW}检测到 EOF，继续执行{Colors.RESET}")


def main():
    """Main entry point."""
    # Load configuration from YAML file
    config = load_config()

    # Enable debug logging if requested
    if config['debug']:
        logging.basicConfig(level=logging.DEBUG)
        logging.getLogger("src.websocket_client").setLevel(logging.DEBUG)

    # Display configuration and wait for confirmation
    display_config(config)

    # Check environment
    private_key = os.environ.get("POLY_PRIVATE_KEY")
    safe_address = os.environ.get("POLY_PROXY_WALLET")

    if not private_key or not safe_address:
        print(f"{Colors.RED}错误: POLY_PRIVATE_KEY 和 POLY_PROXY_WALLET 必须设置{Colors.RESET}")
        print("请在 .env 文件中设置或导出为环境变量")
        sys.exit(1)

    # Create bot from config file
    # TradingBot now only accepts config_path parameter
    config_path = Path(__file__).parent.parent / "config.yaml"

    try:
        from src.config import ConfigNotFoundError
        bot = TradingBot(config_path=str(config_path))
    except (ConfigNotFoundError, Exception) as e:
        print(f"{Colors.RED}错误: {e}{Colors.RESET}")
        sys.exit(1)

    if not bot.is_initialized():
        print(f"{Colors.RED}错误: 初始化交易机器人失败{Colors.RESET}")
        sys.exit(1)

    # Create strategy config
    strategy_config = FlashCrashConfig(
        coin=config['coin'],
        size=config['size'],
        drop_threshold=config['drop'],
        price_lookback_seconds=config['lookback'],
        take_profit=config['take_profit'],
        stop_loss=config['stop_loss'],
    )

    # Create and run strategy
    strategy = FlashCrashStrategy(bot=bot, config=strategy_config)

    try:
        asyncio.run(strategy.run())
    except KeyboardInterrupt:
        print("\n用户中断")
    except Exception as e:
        print(f"\n{Colors.RED}错误: {e}{Colors.RESET}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()

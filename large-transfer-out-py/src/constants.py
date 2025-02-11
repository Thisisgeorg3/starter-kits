DAY_LOOKBACK_WINDOW = 1  # 1 day

# CHAIN_ID -> LOOKBACK_WINDOW_VALUE_THRESHOLD_IN_WEI (if the account had less at the lookback time, we alert), LARGE_TRANSFER_THRESHOLD_IN_WEI
THRESHOLDS = {1: (20000000000000000000, 50000000000000000000),  # mainnet - 20ETH->50 ETH
              56: (100000000000000000000, 250000000000000000000),  # binance - 100BNB->250 BNB
              137: (30000000000000000000000, 75000000000000000000000),  # polygon - 30,000 -> 75,000 MATIC
              43114: (2000000000000000000000, 5000000000000000000000),  # avalanche  - 2000 -> 5000 AVAX
              10: (20000000000000000000, 50000000000000000000),  # optimism - 20ETH->50 ETH
              250: (100000000000000000000000, 250000000000000000000000),  # fantom - 100,000 -> 250,000 FTM
              42161: (20000000000000000000, 50000000000000000000)  # arbitrum - 20ETH->50 ETH
              }

# 0xd78ad95... is the swap topic for Uniswap v2 & 0xc42079f... is the swap topic for Uniswap v3
SWAP_TOPICS = ["0xd78ad95fa46c994b6551d0da85fc275fe613ce37657fb8d5e3d130840159d822",
               "0xc42079f94a6350d7e6235f29174924f928cc2ac818eb64fed8004e115fbcca67"]

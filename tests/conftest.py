import os
import sys

# 讓測試能直接 import repo 根目錄的模組（scraper、deduplicator…）
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

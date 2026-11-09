import sys
import os

print(f"[Python Interpreter Path]: {sys.executable}")
print(f"[Working Directory]: {os.getcwd()}")

try:
    from vivida_etl import VividaIngestionPipeline, generate_event_id, get_headless_chrome_options
    print("✅ Custom 'vivida_etl' module successfully loaded!")
    
    # Test deterministic ID generation
    test_id = generate_event_id("Test Event", "Tacoma Dome", "2026-11-20T10:00:00Z")
    print(f"Generated Event ID: {test_id}")
    
    # Test headless options setup
    opts = get_headless_chrome_options()
    print(f"Chrome Headless Arguments: {opts.arguments}")

except ImportError as e:
    print(f"❌ Failed to import vivida_etl module: {e}")
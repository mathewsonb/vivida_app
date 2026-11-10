# import required libraries and packages
from bs4 import BeautifulSoup
import pandas as pd
import re

# import the file and clean it, then write it back to itself
input = "vivida_app/scraper_cache/rendered_html.html"

# open the source file in read mode
with open(input, "r", encoding="utf-8") as io:
    file = io.read()

# make soup out of the input
soup = BeautifulSoup(file, "html.parser")

# write the soup back to the source
with open(input, "w", encoding="utf-8") as io:
    io.write(soup.prettify())

# ================================
# import os
# from bs4 import BeautifulSoup

# file_path = "vivida_app/dev/sandbox.html"

# if not os.path.exists(file_path):
#     print(f"File not found: {file_path}")
# else:
#     file_size_kb = os.path.getsize(file_path) / 1024
#     print(f"--- File Overview ---")
#     print(f"Path: {file_path}")
#     print(f"Size: {file_size_kb:.2f} KB\n")

#     with open(file_path, "r", encoding="utf-8") as f:
#         html_content = f.read()

#     soup = BeautifulSoup(html_content, "html.parser")
    
#     # Title Check
#     title = soup.title.string.strip() if soup.title else "No <title> found"
#     print(f"Page Title: {title}")

#     # Check for Chrome Error Page Indicators
#     error_codes = soup.find_all(class_="error-code")
#     if error_codes:
#         print(f"⚠️ Detected Chrome Error Code(s): {[e.get_text(strip=True) for e in error_codes]}")
#     else:
#         print("✓ No Chrome internal error classes found.")

#     # Search for <article> elements
#     articles = soup.find_all("article")
#     print(f"\n--- Content Inspection ---")
#     print(f"<article> elements found: {len(articles)}")

#     # Sample Headings / Text Preview
#     headings = [h.get_text(strip=True) for h in soup.find_all(["h1", "h2", "h3", "h4"]) if h.get_text(strip=True)]
#     print(f"\nSample Headings (up to 10):")
#     for h in headings[:10]:
#         print(f" - {h}")

#     if len(html_content) > 0:
#         print("\nFirst 300 characters of raw HTML:")
#         print(html_content[:300].strip())
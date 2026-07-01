from playwright.sync_api import sync_playwright
import sys

url = sys.argv[1]
with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(ignore_https_errors=True)
    try:
        page.goto(url, timeout=25000, wait_until="networkidle")
        page.wait_for_timeout(2500)
        html = page.content()
        with open("rendered.html", "w") as f:
            f.write(html)
        print("SUCCESS, length:", len(html))
    except Exception as e:
        print("ERROR:", e)
    browser.close()

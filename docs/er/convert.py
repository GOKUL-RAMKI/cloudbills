import asyncio
from playwright.async_api import async_playwright

async def convert_svg_to_png():
    input_svg = "file:///D:/stuff/second-yr-sem-3/dbms/db_package/clouDBills/docs/er/cloud_cost_overview.svg"
    output_png = r"D:\stuff\second-yr-sem-3\dbms\db_package\clouDBills\docs\er\cloud_cost_overview.png"

    print("Launching background rendering engine...")
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        
        # Open a wide view field
        await page.set_viewport_size({"width": 3840, "height": 2160})
        
        print("Loading SVG layout details...")
        # wait_until="commit" tells it to move forward the moment the file code itself drops in
        await page.goto(input_svg, wait_until="commit")
        
        # Explicit tiny wait to let layout components settle
        await page.wait_for_timeout(500)
        
        print("Capturing high-resolution diagram...")
        # Use full_page=False to avoid font evaluation blocks
        await page.screenshot(
            path=output_png,
            full_page=False,
            timeout=5000  # Sets an aggressive 5-second max cap limit
        )
        
        await browser.close()
    print(f"\nSuccess! Perfect image file generated at:\n -> {output_png}")

if __name__ == "__main__":
    asyncio.run(convert_svg_to_png())

"""Captures evidence screenshots of the running Streamlit UI with Playwright (uses the installed
Chrome, no browser download).  Start the UI first:  streamlit run app.py
Usage:  python tools/capture_screenshots.py   ->  Documentation/screenshots/*.png
"""
from pathlib import Path

from playwright.sync_api import sync_playwright

OUT = Path(__file__).resolve().parents[2] / "Documentation" / "screenshots"
URL = "http://localhost:8501"


def wait_idle(page, timeout=240_000):
    page.wait_for_timeout(800)
    page.wait_for_function("() => !document.querySelector('[data-testid=\"stStatusWidget\"]')", timeout=timeout)
    page.wait_for_timeout(800)


def tab(page, name):
    page.get_by_role("tab", name=name).click()
    page.wait_for_timeout(1200)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.goto(URL)
        page.get_by_role("tab", name="💬 Ask").wait_for(timeout=120_000)
        wait_idle(page)

        def ask(question, file):
            box = page.get_by_role("textbox", name="Question", exact=True)
            box.fill(question)
            box.press("Enter")
            page.wait_for_timeout(500)
            page.get_by_role("button", name="Ask", exact=True).click()
            wait_idle(page)
            page.get_by_text("Retrieved evidence").first.click()
            page.wait_for_timeout(600)
            page.screenshot(path=OUT / file, full_page=True)

        ask("What is required to read the VIN on BCM-X1?", "01_cited_answer_vin.png")
        ask("Which NRC do I get if I write DID 0x4A10 in the extended session without unlocking security?", "02_rule_engine_correction.png")
        ask("What does DID 0xF1A5 contain?", "03_identifier_guard.png")

        tab(page, "🧪 Validate request")
        page.get_by_role("textbox", name="Request bytes", exact=True).fill("2E 4A 10 00 3C")
        page.get_by_role("button", name="Validate").click()
        wait_idle(page)
        page.screenshot(path=OUT / "04_request_validator.png", full_page=True)

        tab(page, "⚙️ Generate tests")
        page.get_by_role("button", name="Generate DRAFT test cases").click()
        wait_idle(page)
        page.get_by_text("TC-BCM-021-POSITIVE").first.click()
        page.wait_for_timeout(600)
        page.screenshot(path=OUT / "05_test_generation.png", full_page=True)

        tab(page, "✅ Review & export")
        page.get_by_text("APPROVED", exact=True).first.click()
        wait_idle(page)
        page.get_by_role("button", name="Export").click()
        wait_idle(page)
        page.get_by_text("Audit log").first.click()
        page.wait_for_timeout(600)
        page.screenshot(path=OUT / "06_review_export_audit.png", full_page=True)

        tab(page, "📊 Coverage")
        page.screenshot(path=OUT / "07_coverage.png", full_page=True)
        tab(page, "📚 Knowledge base")
        page.screenshot(path=OUT / "08_knowledge_base_eda.png", full_page=True)
        tab(page, "📈 Evaluation")
        page.screenshot(path=OUT / "09_evaluation.png", full_page=True)
        browser.close()
    print("saved to", OUT)


if __name__ == "__main__":
    main()

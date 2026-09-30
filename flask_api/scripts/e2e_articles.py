"""End-to-end browser test of the Articles page (/articles) against a running server.

    pip install playwright            # uses your installed Chrome; no browser download
    python scripts/e2e_articles.py --url http://127.0.0.1:8000 [--shots screenshots/]

Start the server with DEMO_PAGE=1 first. It covers publish -> allow, model reject
(risk > 0.5) with the sentence and trigger words highlighted, Show, stale feedback
after edits, reopening "Rejected" to rewrite, gate revise with highlights across
formatting, gate reject, link URLs, drafts (no moderation call), the
free-publish quota, fail-closed errors and phone width. It clears this page's
localStorage in the test browser only.
"""
import argparse
import sys
from pathlib import Path
from playwright.sync_api import sync_playwright

ap = argparse.ArgumentParser()
ap.add_argument("--url", default="http://127.0.0.1:8000")
ap.add_argument("--shots", default=None, help="folder for screenshots (optional)")
args = ap.parse_args()
URL = args.url.rstrip("/") + "/articles"
SHOTS = args.shots
if SHOTS:
    Path(SHOTS).mkdir(parents=True, exist_ok=True)
results = []


def check(name, cond, detail=""):
    results.append((name, bool(cond), detail))
    print(("PASS " if cond else "FAIL ") + name + (f"  [{detail}]" if detail else ""))


def write(page, title, body_html):
    page.click("#write-btn")
    page.fill("#title", title)
    page.evaluate("""(html) => { const b = document.getElementById('body'); b.innerHTML = html;
                     b.dispatchEvent(new Event('input', {bubbles: true})); }""", body_html)


def highlights(page):
    return page.evaluate("() => CSS.highlights.has('mod-revise') ? [...CSS.highlights.get('mod-revise')].map(r => r.toString()) : []")


def word_highlights(page):
    return page.evaluate("() => CSS.highlights.has('mod-word') ? [...CSS.highlights.get('mod-word')].map(r => r.toString()) : []")


with sync_playwright() as p:
    browser = p.chromium.launch(channel="chrome", headless=True)
    page = browser.new_page(viewport={"width": 1280, "height": 900})
    calls = []
    page.on("request", lambda r: calls.append(r.url) if "/v1/moderate" in r.url else None)
    page.goto(URL)
    page.evaluate("localStorage.clear()")
    page.reload()

    # 1. empty state + quota
    check("empty state shown", "No articles yet" in page.inner_text("#list"))
    check("quota 2 of 2", "2 of 2 free article publishes left" in page.inner_text("#quota"))
    SHOTS and page.screenshot(path=f"{SHOTS}/a1_empty.png")

    # 2. allow -> published
    write(page, "Cotton bedsheets for wholesale buyers", "<p>Cotton bedsheets in king and queen sizes, 300 thread count. Bulk orders welcome.</p>")
    page.click("#publish-btn")
    page.wait_for_selector("#modal", state="hidden")
    check("allow: modal closed", page.is_hidden("#modal"))
    check("allow: listed as published", "Cotton bedsheets for wholesale buyers" in page.inner_text("#list"))
    check("allow: quota now 1 of 2", "1 of 2" in page.inner_text("#quota"))

    # 3. model reject (risk > 0.5) -> only the bad sentence highlighted, trigger words marked, rewrite
    write(page, "PVC pipes for contractors", "<p>High quality PVC pipes, ISI marked, available in all sizes. Earn 50000 per week from home, no experience needed.</p>")
    page.click("#publish-btn")
    page.wait_for_selector(".banner.reject")
    hl = highlights(page)
    check("model reject: banner shown", page.is_visible(".banner.reject"))
    check("model reject: highlight is the problem sentence", hl == ["Earn 50000 per week from home, no experience needed"], str(hl))
    check("model reject: trigger words marked", len(word_highlights(page)) >= 1 and page.locator(".issues .trigger").count() >= 1,
          str(word_highlights(page)))
    check("model reject: modal stays open to rewrite", page.is_visible("#modal"))
    check("model reject: not published, quota unchanged", "1 of 2" in page.inner_text("#quota"))
    SHOTS and page.screenshot(path=f"{SHOTS}/a2_model_reject.png")
    page.locator(".issues li", has_text="Body").locator("[data-issue]").first.click()
    sel = page.evaluate("() => window.getSelection().toString()")
    check("model reject: Show selects the text", sel == "Earn 50000 per week from home, no experience needed", sel)
    page.keyboard.press("End")
    page.keyboard.type(" Thanks.")
    check("model reject: editing marks feedback as stale", "Publish again to re-check" in page.inner_text("#banner") and highlights(page) == [])
    page.click("#close-btn")
    page.click("[data-tab='rejected']")
    check("model reject: kept under Rejected", "PVC pipes for contractors" in page.inner_text("#list"))
    check("model reject: row says what to rewrite", "1 part to rewrite" in page.inner_text("#list"), page.inner_text("#list")[:160])

    # 4. reopen the rejected article -> feedback + highlight restored, ready to rewrite
    page.click("[data-action='edit']")
    page.wait_for_selector(".banner.reject")
    check("reopen: feedback restored", highlights(page) == ["Earn 50000 per week from home, no experience needed"], str(highlights(page)))
    page.click("#close-btn")

    # 5. gate revise across bold formatting
    write(page, "King size bedsheets", "<p>Cotton bedsheets in <b>king and queen</b> sizes, 300 thread count.</p><p>We also accept <i>payment in crypto</i>.</p>")
    page.click("#publish-btn")
    page.wait_for_selector(".banner.revise")
    hl = highlights(page)
    check("rule revise: 'payment in crypto' highlighted across <i>", hl == ["payment in crypto"], str(hl))
    check("rule revise: instruction shown", "Payments must go through BONC" in page.inner_text("#banner"))
    SHOTS and page.screenshot(path=f"{SHOTS}/a3_rule_revise.png")
    page.click("#close-btn")

    # 6. reject (gate block) -> reason, and the matched phrase highlighted to rewrite
    write(page, "Investment opportunity", "<p>Invest now and double your money in 7 days.</p>")
    page.click("#publish-btn")
    page.wait_for_selector(".banner.reject")
    check("reject: banner with policy area", "fraud and scams" in page.inner_text("#banner"))
    check("reject: matched phrase highlighted to rewrite", highlights(page) == ["double your money"], str(highlights(page)))
    SHOTS and page.screenshot(path=f"{SHOTS}/a4_reject.png")
    page.click("#close-btn")
    page.click("[data-tab='rejected']")
    lst = page.inner_text("#list")
    check("reject: kept under Rejected with reason", "Investment opportunity" in lst and "fraud and scams" in lst, lst[:200])

    # 7. scam domain hidden behind link text -> rejected
    write(page, "Our supplier portal", '<p>Log in to <a href="https://paypa1-verify.example/login">our supplier portal</a> to see prices.</p>')
    page.click("#publish-btn")
    page.wait_for_selector(".banner.reject")
    check("link: hidden scam URL is checked and rejected", page.is_visible(".banner.reject"))
    page.click("#close-btn")

    # 8. drafts skip moderation
    before = len(calls)
    write(page, "Draft about solar panels", "<p>Solar panels 330W mono PERC. They come with a 25 year warranty.</p>")
    page.click("#draft-btn")
    page.wait_for_selector("#modal", state="hidden")
    check("draft: no moderation call", len(calls) == before, f"{len(calls) - before} calls")
    check("draft: listed under Drafts", "Draft about solar panels" in page.inner_text("#list"))

    # 9. publish the draft (allowed), then quota exhausted
    page.click("[data-action='edit']")
    page.click("#publish-btn")
    page.wait_for_selector("#modal", state="hidden")
    check("quota: 0 of 2 after second publish", "0 of 2" in page.inner_text("#quota"))
    write(page, "Office chairs", "<p>Office chairs with lumbar support. They are available in mesh and leather.</p>")
    before = len(calls)
    page.click("#publish-btn")
    check("quota: third publish blocked before calling the API",
          "No free article publishes left" in page.inner_text("#banner") and len(calls) == before)
    page.click("#close-btn")

    # 10. service down -> fail closed
    page.evaluate("localStorage.clear()"); page.reload()
    page.route("**/v1/moderate", lambda route: route.fulfill(status=503, content_type="application/json",
               body='{"error":{"code":"model_not_ready","message":"model not loaded"}}'))
    write(page, "Solar panels", "<p>Solar panels 330W mono PERC. They come with a 25 year warranty.</p>")
    page.click("#publish-btn")
    page.wait_for_selector(".banner.error")
    check("error: fail closed (not published)", "No articles yet" in page.inner_text("#list")
          and "hasn't been published" in page.inner_text("#banner"))
    page.unroute("**/v1/moderate")
    page.click("#close-btn")

    # 11. phone width: no horizontal scroll
    page.set_viewport_size({"width": 390, "height": 844})
    page.click("#write-btn")
    overflow = page.evaluate("() => document.documentElement.scrollWidth > window.innerWidth")
    check("mobile: no horizontal scroll", not overflow)
    SHOTS and page.screenshot(path=f"{SHOTS}/a5_mobile_modal.png")
    browser.close()

failed = [r for r in results if not r[1]]
print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
sys.exit(1 if failed else 0)

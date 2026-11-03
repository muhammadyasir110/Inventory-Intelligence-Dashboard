import _testenv  # noqa: F401  (isolated test database; must be first)
"""
Frontend checks.
  * static: frontend/build.py --check (templates in sync, no inline handlers or
    inline scripts under the CSP, JavaScript syntax via node when available);
  * browser (Playwright + Chromium, skipped if unavailable): starts the real
    server on a scratch database, logs in, previews and imports a file,
    visits every page and fails on any JavaScript error or CSP violation.
"""
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
BACKEND = os.path.join(ROOT, "backend")

try:
    from playwright.sync_api import sync_playwright
    HAVE_PW = True
except Exception:  # pragma: no cover
    HAVE_PW = False

PAGES = ["overview", "inventory", "alerts", "overstock", "expiry", "safety-stock", "warehouses",
         "suppliers", "erp", "optimization", "forecasting", "scenarios", "procurement",
         "decisions", "upload", "config"]


class FrontendStatic(unittest.TestCase):
    def test_build_check(self):
        r = subprocess.run([sys.executable, os.path.join(ROOT, "frontend", "build.py"), "--check"],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


def _free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p


@unittest.skipUnless(HAVE_PW and os.getenv("IIP_SKIP_BROWSER") != "1", "Playwright not available")
class FrontendBrowser(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="iip_ui_")
        cls.port = _free_port()
        env = {**os.environ, "DATABASE_URL": f"sqlite:///{os.path.join(cls.tmp, 'ui.db')}",
               "IIP_ENV": "development", "IIP_PBKDF2_ITERATIONS": "1000", "IIP_LOG_LEVEL": "WARNING"}
        env.pop("IIP_ADMIN_PASSWORD", None)
        cls.proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "main:app", "--host", "127.0.0.1",
                                     "--port", str(cls.port)], cwd=BACKEND, env=env,
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        import urllib.request
        for _ in range(100):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{cls.port}/api/health", timeout=1)
                break
            except Exception:
                time.sleep(0.2)

    @classmethod
    def tearDownClass(cls):
        cls.proc.terminate()
        cls.proc.wait(timeout=20)

    def test_pages_render_without_errors_under_csp(self):
        base = f"http://127.0.0.1:{self.port}"
        errors = []
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page()
            page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(base + "/")
            page.fill("#loginUsername", "admin")
            page.fill("#loginPassword", "admin123")
            page.click("#loginForm button[type=submit]")
            page.wait_for_selector("#landingScreen:not(.hidden), #appShell:not(.hidden)", timeout=15000)
            # landing: upload with preview-before-commit
            sample = os.path.join(ROOT, "sample_data", "sample_inventory.csv")
            page.set_input_files("#landingUploadZone input[type=file]", sample)
            page.wait_for_selector("[data-upload-confirm]", timeout=30000)   # slow machines: parsing + validation
            page.click("[data-upload-confirm]")
            page.wait_for_selector("#appShell:not(.hidden)", timeout=30000)
            for view in PAGES:
                page.click(f'.nav-item[data-view="{view}"]')
                page.wait_for_timeout(900)
            page.click('.nav-item[data-view="procurement"]')
            page.wait_for_selector("#poTableBody", timeout=10000)
            page.click("#createPoBatchBtn")
            page.wait_for_selector('[data-po-action="approve"]', timeout=15000)
            body = page.inner_text("#viewMount")
            self.assertNotIn("undefined", body)
            self.assertNotIn("NaN", body)
            # stock adjustment from the item dialog is ledgered and shown
            page.click("[data-open-item]")
            page.wait_for_selector("#adjSubmit", timeout=10000)
            page.fill("#adjCount", "123")
            page.click("#adjSubmit")
            # (string-evaluated waits are blocked by our CSP - no 'unsafe-eval' - so use a selector)
            page.wait_for_selector("#adjLedger:has-text('cycle_count')", timeout=10000)
            self.assertEqual(page.input_value("#editItemQty"), "123")
            browser.close()
        csp = [e for e in errors if "Content Security Policy" in e or "Refused to" in e]
        self.assertEqual(csp, [], f"CSP violations: {csp}")
        js = [e for e in errors if "Failed to load resource" not in e]
        self.assertEqual(js, [], f"JavaScript errors: {js}")

    def test_team_invitation_and_account_screens(self):
        """Runs after test_pages_* (alphabetical), so the demo organisation has data.
        Owner invites a member (console e-mail mode -> link shown), the invitee accepts
        in a fresh browser context, and owner-only tools are hidden from the new member."""
        base = f"http://127.0.0.1:{self.port}"
        errors = []
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page()
            page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(base + "/")
            page.wait_for_selector("#demoHint:not(.hidden)", timeout=10000)     # development only
            page.fill("#loginUsername", "admin")
            page.fill("#loginPassword", "admin123")
            page.click("#loginForm button[type=submit]")
            page.wait_for_selector("#appShell:not(.hidden)", timeout=15000)
            page.click('.nav-item[data-view="config"]')
            page.wait_for_selector("#inviteForm", timeout=10000)
            self.assertTrue(page.is_visible("#exportOrgBtn"))                    # owner tools visible to owner
            page.fill("#invEmail", "new.member@example.com")
            page.select_option("#invRole", "inventory_manager")
            page.click("#inviteForm button[type=submit]")
            page.wait_for_selector("#inviteLinkBox:not(.hidden)", timeout=10000)
            link = page.input_value("#inviteLink")
            self.assertIn("#/invite?token=", link)
            page.wait_for_selector("#invitesBody:has-text('new.member@example.com')", timeout=10000)

            guest = browser.new_context().new_page()
            guest.on("pageerror", lambda e: errors.append(str(e)))
            guest.goto(link.replace("http://127.0.0.1:8000", base))
            guest.wait_for_selector("#iaUser", timeout=10000)
            self.assertIn("Inventory manager", guest.inner_text("#accountCard"))
            guest.fill("#iaUser", "new_member")
            guest.fill("#iaPw", "Member-pass-12345")
            guest.click("#accountCard button[type=submit]")
            guest.wait_for_selector("#appShell:not(.hidden)", timeout=15000)
            guest.click('.nav-item[data-view="config"]')
            guest.wait_for_selector("#changePwForm", timeout=10000)
            self.assertFalse(guest.is_visible("#exportOrgBtn"))                  # not the owner
            self.assertFalse(guest.is_visible("#inviteForm"))                    # not an administrator

            anon = browser.new_context().new_page()
            anon.goto(base + "/")
            anon.click("#forgotPwBtn")
            anon.fill("#fpEmail", "nobody@example.com")
            anon.click("#forgotForm button[type=submit]")
            anon.wait_for_selector("#accountMsg:has-text('If an active account')", timeout=10000)
            browser.close()
        csp = [e for e in errors if "Content Security Policy" in e or "Refused to" in e]
        self.assertEqual(csp, [], f"CSP violations: {csp}")
        js = [e for e in errors if "Failed to load resource" not in e]
        self.assertEqual(js, [], f"JavaScript errors: {js}")

    def test_transfer_execute_flow(self):
        """Runs after test_team_* (alphabetical). Imports a file with one SKU in two warehouses,
        then executes the proposed transfer from the Warehouses page."""
        import json
        import urllib.request
        base = f"http://127.0.0.1:{self.port}"
        hdr = ("SKU,Product Name,Category,Quantity On Hand,Reorder Point,Safety Stock,Max Stock Level,"
               "Unit Cost,Unit Price,Lead Time Days,Daily Avg Demand,Expiry Date,Last Restock Date,Supplier,Warehouse\n")
        csv = (hdr + "TRF-1,Cooking Oil 1L,Groceries,500,20,10,100,300,360,5,4,,2026-06-01,Metro Logistics,Karachi Central\n"
                     "TRF-1,Cooking Oil 1L,Groceries,2,30,10,100,300,360,5,4,,2026-06-01,Metro Logistics,Lahore North\n").encode()
        tok = json.loads(urllib.request.urlopen(urllib.request.Request(
            base + "/api/auth/login", data=json.dumps({"username": "admin", "password": "admin123"}).encode(),
            headers={"Content-Type": "application/json"})).read())["token"]
        b = "----iip"
        body = (f"--{b}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"trf.csv\"\r\n"
                "Content-Type: text/csv\r\n\r\n").encode() + csv + f"\r\n--{b}--\r\n".encode()
        urllib.request.urlopen(urllib.request.Request(base + "/api/upload", data=body, headers={
            "Authorization": f"Bearer {tok}", "Content-Type": f"multipart/form-data; boundary={b}"})).read()
        errors = []
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page()
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.on("dialog", lambda d: d.accept("50"))
            page.goto(base + "/")
            page.fill("#loginUsername", "admin")
            page.fill("#loginPassword", "admin123")
            page.click("#loginForm button[type=submit]")
            page.wait_for_selector("#appShell:not(.hidden)", timeout=15000)
            page.click('.nav-item[data-view="warehouses"]')
            page.wait_for_selector("#transferBody [data-transfer-idx]", timeout=10000)
            self.assertIn("TRF-1", page.inner_text("#transferBody"))
            page.click("#transferBody [data-transfer-idx]")
            page.wait_for_selector(".toast:has-text('posted')", timeout=10000)
            browser.close()
        items = json.loads(urllib.request.urlopen(urllib.request.Request(
            base + "/api/skus?search=TRF-1", headers={"Authorization": f"Bearer {tok}"})).read())["items"]
        self.assertEqual(sorted(i["quantity_on_hand"] for i in items), [52.0, 450.0])
        self.assertEqual([e for e in errors if "Failed to load resource" not in e], [])

    def test_two_factor_login_flow(self):
        """Runs last (alphabetical): the demo owner turns on two-factor login in Settings,
        signs in again with a code, then turns it off again."""
        from core import totp
        base = f"http://127.0.0.1:{self.port}"
        errors = []
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page()
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(base + "/")
            page.fill("#loginUsername", "admin")
            page.fill("#loginPassword", "admin123")
            page.click("#loginForm button[type=submit]")
            page.wait_for_selector("#appShell:not(.hidden)", timeout=15000)
            page.click('.nav-item[data-view="config"]')
            page.wait_for_selector("#mfaStartBtn:not(.hidden)", timeout=10000)
            page.click("#mfaStartBtn")
            page.wait_for_selector("#mfaSetupBox:not(.hidden)", timeout=10000)
            secret = page.inner_text("#mfaSecret").strip()
            page.fill("#mfaEnableCode", totp.code_at(secret))
            page.click("#mfaEnableForm button[type=submit]")
            page.wait_for_selector("#mfaCodesBox:not(.hidden)", timeout=10000)
            self.assertEqual(len(page.inner_text("#mfaCodes").split()), 10)

            fresh = browser.new_context().new_page()
            fresh.on("pageerror", lambda e: errors.append(str(e)))
            fresh.goto(base + "/")
            fresh.fill("#loginUsername", "admin")
            fresh.fill("#loginPassword", "admin123")
            fresh.click("#loginForm button[type=submit]")
            fresh.wait_for_selector("#mfaStep:not(.hidden)", timeout=10000)
            fresh.fill("#loginMfaCode", totp.code_at(secret, time.time() + 30))   # next step (current one was used)
            fresh.click("#loginForm button[type=submit]")
            fresh.wait_for_selector("#appShell:not(.hidden)", timeout=15000)

            fresh.click('.nav-item[data-view="config"]')
            fresh.wait_for_selector("#mfaDisableForm:not(.hidden)", timeout=10000)
            fresh.fill("#mfaDisablePw", "admin123")
            fresh.fill("#mfaDisableCode", page.inner_text("#mfaCodes").split()[0])     # a recovery code
            fresh.on("dialog", lambda d: d.accept())
            fresh.click("#mfaDisableForm button[type=submit]")
            fresh.wait_for_selector("#mfaStartBtn:not(.hidden)", timeout=10000)
            browser.close()
        self.assertEqual([e for e in errors if "Failed to load resource" not in e], [])


if __name__ == "__main__":
    unittest.main()

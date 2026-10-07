# ruff: noqa: E501
"""A local, deterministic "employer" website for the browser assistant (V0.10).

    from tests.fake_site import FakeApplicationSite
    site = FakeApplicationSite().start()        # 127.0.0.1:<free port>
    site.url("/apply")
    site.submit_clicks                           # how many times anything tried to submit
    site.stop()

One real HTTP server (stdlib only), served to a real Chromium -- the same pages are used
by the backend tests and by the browser E2E (`e2e/serve.py`). `/apply` has every kind of
control the assistant supports plus the ones it must leave alone:

    text / email / tel / url / number / date / textarea / select / radio / checkbox group /
    a single checkbox / three file inputs
    a required field, a character-limited field (`Postal code`, 5), a field that rewrites
    what is typed (`Reference code`, forced to upper case), a field only the user fills
    (`Current employer`), a dynamic field (`visa details` appears after answering "Yes")
    two legal confirmations, and
    a final "Submit application" button that is *instrumented*: any click on it, any form
    submission, is counted (`submit_clicks`) and so can be proven never to have happened.

Other pages: `/challenge` (a visible CAPTCHA widget), `/login` (a password field),
`/interstitial` ("Just a moment..."), `/forbidden` (HTTP 403), `/empty` (no form), `/spa`
(the form is built by script after a moment), `/ids` (generated ids such as ":r1:"),
`/away` (redirects to the same server under another host name: another website),
`/relabel?label=` (one field whose label changes), `/pdf-only` (a file input that takes PDF only).

V0.11 (review & submit) adds pages whose final button really submits:

    /submit-apply        a realistic application form; submitting it POSTs to the server,
                         which counts the received application (`received`) and answers
                         with a confirmation page ("Thank you for your application.
                         Application ID: EXM-2026-0001")
    /submit-ambiguous    the same form, but after the press the page only says "Processing..."
                         and never confirms anything (an ambiguous result)
    /submit-two-buttons  a form with two buttons that could both submit it

Every press of their final button is counted (`submit_clicks`), whether or not the form
then submits -- so a test can prove there was exactly one, or none.
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlsplit

_STYLE = """
<style>
 body { font-family: sans-serif; margin: 24px; max-width: 760px; }
 label { display: block; margin-top: 12px; font-weight: 600; }
 fieldset { margin-top: 12px; } fieldset label { display: inline; font-weight: 400; margin-right: 12px; }
 input, textarea, select { display: block; margin-top: 4px; min-width: 280px; }
 input[type=radio], input[type=checkbox] { display: inline; min-width: 0; }
 button { margin-top: 18px; padding: 8px 14px; }
</style>
"""

# Every click on the submit button and every submission of the form is reported here.
_INSTRUMENT = """
<script>
 function report(kind) { navigator.sendBeacon('/__submit?kind=' + kind); }
 document.addEventListener('click', function (e) {
   var t = e.target.closest('button, input[type=submit]');
   if (t && t.id === 'submit-btn') report('click');
 }, true);
 document.addEventListener('submit', function (e) { e.preventDefault(); report('submit'); }, true);
</script>
"""

FORM = """
<form id="apply-form" aria-label="Application form" novalidate>
 <h2>Apply: Senior SAP ABAP Developer</h2>
 <input type="text" name="hp_website" tabindex="-1" autocomplete="off" aria-hidden="true"
        style="position:absolute;left:-9999px">

 <label for="first_name">First name *</label>
 <input id="first_name" name="first_name" type="text" required>

 <label for="last_name">Last name *</label>
 <input id="last_name" name="last_name" type="text" required>

 <label for="email">Email address *</label>
 <input id="email" name="email" type="email" required>

 <label for="phone">Phone number</label>
 <input id="phone" name="phone" type="tel">

 <label for="linkedin">LinkedIn profile</label>
 <input id="linkedin" name="linkedin" type="url">

 <label for="salary">Expected annual salary (EUR)</label>
 <input id="salary" name="salary" type="number" min="0">

 <label for="start_date">Earliest start date</label>
 <input id="start_date" name="start_date" type="date">

 <label for="language">Application language</label>
 <select id="language" name="language">
  <option value="">Please choose</option>
  <option value="de">German</option>
  <option value="en">English</option>
 </select>

 <label for="country">Country</label>
 <select id="country" name="country">
  <option value="">Please choose</option>
  <option value="DE">Germany</option>
  <option value="AT">Austria</option>
  <option value="CH">Switzerland</option>
 </select>

 <fieldset>
  <legend>Are you legally authorized to work in Germany? *</legend>
  <label><input type="radio" name="authorized" value="yes"> Yes</label>
  <label><input type="radio" name="authorized" value="no"> No</label>
 </fieldset>

 <fieldset>
  <legend>Do you require visa sponsorship?</legend>
  <label><input type="radio" name="sponsorship" value="yes"> Yes</label>
  <label><input type="radio" name="sponsorship" value="no"> No</label>
 </fieldset>

 <div id="visa-details-box" style="display:none">
  <label for="visa_details">Please explain your visa status</label>
  <textarea id="visa_details" name="visa_details" rows="3"></textarea>
 </div>

 <label for="notice">Notice period</label>
 <input id="notice" name="notice" type="text">

 <label for="years">Years of experience</label>
 <input id="years" name="years" type="number" min="0">

 <fieldset>
  <legend>Which technologies do you work with?</legend>
  <label><input type="checkbox" name="tech" value="python"> Python</label>
  <label><input type="checkbox" name="tech" value="java"> Java</label>
  <label><input type="checkbox" name="tech" value="go"> Go</label>
 </fieldset>

 <label for="relocate"><input id="relocate" name="relocate" type="checkbox"> Open to relocation</label>

 <label for="why">Why do you want to work for us?</label>
 <textarea id="why" name="why" rows="4" maxlength="300"></textarea>

 <label for="employer">Current employer</label>
 <input id="employer" name="employer" type="text">

 <label for="postal">Postal code</label>
 <input id="postal" name="postal" type="text" maxlength="5">

 <label for="reference">Reference code</label>
 <input id="reference" name="reference" type="text">

 <label for="cv">Resume / CV *</label>
 <input id="cv" name="resume" type="file" accept=".pdf,.doc,.docx" required>

 <label for="cover">Cover letter</label>
 <input id="cover" name="cover_letter" type="file" accept=".pdf,.doc,.docx">

 <label for="other">Other documents</label>
 <input id="other" name="attachments" type="file">

 <label><input id="privacy" name="privacy" type="checkbox" required> I agree to the privacy policy *</label>
 <label><input id="truth" name="truth" type="checkbox"> I certify that the information above is true and complete</label>

 <button type="button" id="save-btn">Save draft</button>
 <button type="submit" id="submit-btn">Submit application</button>
</form>
<script>
 // The "Reference code" field rewrites what is typed: the browser shows something else
 // than what was entered.
 document.getElementById('reference').addEventListener('input', function (e) {
   e.target.value = e.target.value.toUpperCase();
 });
 // A dynamic question: answering "Yes" reveals a follow-up.
 document.querySelectorAll('input[name=sponsorship]').forEach(function (r) {
   r.addEventListener('change', function () {
     document.getElementById('visa-details-box').style.display =
       document.querySelector('input[name=sponsorship]:checked').value === 'yes' ? 'block' : 'none';
   });
 });
 // Proof that the page saw real input events (as a React or Vue form would).
 window.__events = {};
 document.querySelectorAll('input, textarea, select').forEach(function (el) {
   ['input', 'change'].forEach(function (name) {
     el.addEventListener(name, function () {
       var key = (el.name || el.id) + ':' + name;
       window.__events[key] = (window.__events[key] || 0) + 1;
     });
   });
 });
</script>
"""

# V0.11: count presses of the final button, but let the form really submit.
_COUNT_CLICKS = """
<script>
 document.addEventListener('click', function (e) {
   var t = e.target.closest('button, input[type=submit]');
   if (t && t.id === 'submit-btn') navigator.sendBeacon('/__submit?kind=click');
 }, true);
</script>
"""

SUBMIT_FORM = """
<form id="apply-form" aria-label="Application form" method="post" action="/submit-apply"
      enctype="multipart/form-data" novalidate>
 <h2>Apply: Integration Engineer</h2>
 <label for="first_name">First name *</label>
 <input id="first_name" name="first_name" type="text" required>
 <label for="last_name">Last name *</label>
 <input id="last_name" name="last_name" type="text" required>
 <label for="email">Email address *</label>
 <input id="email" name="email" type="email" required>
 <label for="phone">Phone number</label>
 <input id="phone" name="phone" type="tel">
 <label for="years">Years of experience</label>
 <input id="years" name="years" type="number" min="0">
 <fieldset>
  <legend>Are you legally authorized to work in Germany? *</legend>
  <label><input type="radio" name="authorized" value="yes" required> Yes</label>
  <label><input type="radio" name="authorized" value="no"> No</label>
 </fieldset>
 <label for="motivation">Why do you want to join us? *</label>
 <textarea id="motivation" name="motivation" rows="4" required></textarea>
 <label for="cv">Resume / CV *</label>
 <input id="cv" name="resume" type="file" accept=".pdf,.doc,.docx" required>
 <label for="cover">Cover letter</label>
 <input id="cover" name="cover_letter" type="file" accept=".pdf,.doc,.docx">
 <label><input id="privacy" name="privacy" type="checkbox" required> I agree to the processing of my personal data for this application in accordance with the privacy policy *</label>
 <label><input id="talent_pool" name="talent_pool" type="checkbox"> Keep my profile in the talent pool for future opportunities</label>
 <button type="button" id="save-btn">Save draft</button>
 <button type="submit" id="submit-btn">Submit application</button>
</form>
"""

_AMBIGUOUS = """
<script>
 document.getElementById('apply-form').addEventListener('submit', function (e) {
   e.preventDefault();
   document.getElementById('status').textContent = 'Processing...';
 });
</script>
<p id="status" role="status"></p>
"""

CONFIRMATION = (
    "<title>Application received - Example GmbH</title><body><h1>Thank you for your "
    "application!</h1><p>Your application has been submitted successfully.</p>"
    "<p>Application ID: {ref}</p></body>"
)

PAGES = {
    "/apply": f"<title>Apply - Example GmbH</title>{_STYLE}<body>{FORM}{_INSTRUMENT}</body>",
    "/challenge": (
        f"<title>Apply - Example GmbH</title>{_STYLE}<body>"
        '<div class="g-recaptcha" data-sitekey="test-key" '
        'style="width:304px;height:78px;border:1px solid #888">I\'m not a robot</div>'
        '<form id="apply-form"><label for="email">Email address</label>'
        '<input id="email" name="email" type="email">'
        f'<button type="submit" id="submit-btn">Submit application</button></form>{_INSTRUMENT}</body>'
    ),
    "/login": (
        f"<title>Sign in</title>{_STYLE}<body><form><h2>Sign in to apply</h2>"
        '<label for="user">Email</label><input id="user" name="user" type="email">'
        '<label for="pw">Password</label><input id="pw" name="pw" type="password">'
        '<button type="submit" id="submit-btn">Sign in</button></form></body>'
    ),
    "/interstitial": (
        "<title>Just a moment...</title><body><h1>Checking your browser before accessing the "
        "site.</h1></body>"
    ),
    "/empty": f"<title>Job posting</title>{_STYLE}<body><h1>Senior SAP ABAP Developer</h1>"
    '<p>Read the description.</p><a class="button" href="/apply">Apply now</a></body>',
    "/spa": (
        f'<title>Apply (SPA)</title>{_STYLE}<body><div id="root">Loading...</div>'
        "<script>setTimeout(function(){document.getElementById('root').innerHTML="
        '\'<form id="apply-form"><label for="email">Email address</label>'
        '<input id="email" name="email" type="email"></form>\';},400);</script></body>'
    ),
    "/pdf-only": (
        f'<title>Apply</title>{_STYLE}<body><form id="f">'
        '<label for="cv">Resume / CV</label><input id="cv" name="resume" type="file" '
        'accept=".pdf">'
        f'<button type="submit" id="submit-btn">Submit application</button></form>{_INSTRUMENT}</body>'
    ),
    "/submit-apply": (
        f"<title>Apply - Example GmbH</title>{_STYLE}<body>{SUBMIT_FORM}{_COUNT_CLICKS}</body>"
    ),
    "/submit-ambiguous": (
        f"<title>Apply - Example GmbH</title>{_STYLE}<body>"
        f"{SUBMIT_FORM}{_AMBIGUOUS}{_COUNT_CLICKS}</body>"
    ),
    "/submit-two-buttons": (
        f"<title>Apply - Example GmbH</title>{_STYLE}<body>"
        + SUBMIT_FORM.replace(
            '<button type="button" id="save-btn">Save draft</button>',
            '<button type="submit" id="apply-now">Apply now</button>',
        )
        + f"{_AMBIGUOUS}{_COUNT_CLICKS}</body>"
    ),
    "/ids": (
        f'<title>Apply</title>{_STYLE}<body><form id="f">'
        '<label for=":r1:">Phone number</label><input id=":r1:" type="tel">'
        '<label for=":r2:">Last name</label><input id=":r2:" type="text">'
        f"</form>{_INSTRUMENT}</body>"
    ),
}


class _Handler(BaseHTTPRequestHandler):
    server: "_Server"

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - silence the server
        return

    def _send(
        self, status: int, body: bytes, content_type: str = "text/html; charset=utf-8"
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        parts = urlsplit(self.path)
        path = parts.path
        site = self.server.site
        if path == "/__stats":
            self._send(200, json.dumps(site.stats()).encode(), "application/json")
            return
        if path == "/forbidden":
            self._send(403, b"<title>Access denied</title><body>Forbidden</body>")
            return
        if path == "/away":
            host = self.headers.get("Host", "127.0.0.1").split(":")[0]
            other = "localhost" if host != "localhost" else "127.0.0.1"
            self.send_response(302)
            self.send_header("Location", f"http://{other}:{site.port}/apply")
            self.end_headers()
            return
        if path == "/relabel":
            from html import escape
            from urllib.parse import parse_qs

            label: str = parse_qs(parts.query).get("label", ["Email address"])[0]
            page = (
                f'<title>Apply (relabelled)</title>{_STYLE}<body><form id="apply-form">'
                f'<label for="email">{escape(label)}</label>'
                f'<input id="email" name="email" type="email"></form>{_INSTRUMENT}</body>'
            )
            self._send(200, page.encode())
            return
        if path == "/application-received":
            from html import escape
            from urllib.parse import parse_qs

            ref = parse_qs(parts.query).get("ref", ["EXM-UNKNOWN"])[0]
            self._send(200, CONFIRMATION.format(ref=escape(ref)).encode())
            return
        static = PAGES.get(path)
        if static is None:
            self._send(404, b"<title>Not found</title>")
            return
        site.page_loads[path] = site.page_loads.get(path, 0) + 1
        self._send(200, static.encode())

    def do_POST(self) -> None:  # noqa: N802
        parts = urlsplit(self.path)
        if parts.path == "/__submit":
            self.server.site.record_submit(parts.query)
            self._send(204, b"")
            return
        if parts.path == "/submit-apply":
            # A real submission: read (and discard) the form, count it, confirm it.
            length = int(self.headers.get("Content-Length") or 0)
            if length:
                self.rfile.read(length)
            reference = self.server.site.receive_application()
            self.send_response(303)
            self.send_header("Location", f"/application-received?ref={reference}")
            self.end_headers()
            return
        self._send(404, b"")


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    site: "FakeApplicationSite"


class FakeApplicationSite:
    def __init__(self, port: int = 0) -> None:
        self._requested_port = port
        self._server: _Server | None = None
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self.submissions: list[str] = []
        self.page_loads: dict[str, int] = {}
        #: Applications the server really received (V0.11 pages).
        self.received: list[str] = []

    @property
    def port(self) -> int:
        assert self._server is not None
        return int(self._server.server_address[1])

    def start(self) -> "FakeApplicationSite":
        self._server = _Server(("127.0.0.1", self._requested_port), _Handler)
        self._server.site = self
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()

    def url(self, path: str = "/apply", *, host: str = "127.0.0.1") -> str:
        return f"http://{host}:{self.port}{path}"

    def record_submit(self, query: str) -> None:
        with self._lock:
            self.submissions.append(query or "unknown")

    def receive_application(self) -> str:
        with self._lock:
            reference = f"EXM-2026-{len(self.received) + 1:04d}"
            self.received.append(reference)
            return reference

    @property
    def submit_clicks(self) -> int:
        """How many times the submit button was clicked or the form submitted."""
        with self._lock:
            return len(self.submissions)

    def stats(self) -> dict[str, Any]:
        with self._lock:
            return {
                "submissions": list(self.submissions),
                "page_loads": dict(self.page_loads),
                "received": list(self.received),
            }

    def __enter__(self) -> "FakeApplicationSite":
        return self.start()

    def __exit__(self, *exc: object) -> None:
        self.stop()

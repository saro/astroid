#!/usr/bin/env python3
"""Spike (c): WebKitGTK 6.0 mechanisms the thread view depends on.

Run under xvfb:  xvfb-run -a python3 devel/spike_webkit.py

Proves:
1. load_html + UserScript injection at document-start
2. Python -> JS: evaluate_javascript with JSON arg, JSON ack back via
   the returned JSC.Value (the page_client transport)
3. JS -> Python: script message handler (window.webkit.messageHandlers)
4. srcdoc iframe renders and its scrollHeight is readable (body sizing)
5. CSP <meta> blocks a remote image inside the iframe while data: URIs load
"""

import json
import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("WebKit", "6.0")
from gi.repository import GLib, Gtk, WebKit  # noqa: E402

USER_SCRIPT = r"""
window.Astroid = {
  state: { messages: [], focused: null },
  handle: function (msg) {
    switch (msg.type) {
      case "ping":
        return { type: "ack", success: true, echo: msg.payload };
      case "add_message": {
        const d = document.createElement("div");
        d.id = "msg_" + msg.mid;
        d.className = "email";

        const iframe = document.createElement("iframe");
        iframe.setAttribute("sandbox", "allow-same-origin");
        iframe.srcdoc = msg.html;
        d.appendChild(iframe);
        document.body.appendChild(d);
        this.state.messages.push(msg.mid);
        return { type: "ack", success: true, count: this.state.messages.length };
      }
      case "check_iframe": {
        const f = document.querySelector("#msg_" + msg.mid + " iframe");
        if (!f || !f.contentWindow)
          return { type: "ack", success: false };
        const doc = f.contentWindow.document;
        const img = doc.querySelector("img.remote");
        const dimg = doc.querySelector("img.data");
        return {
          type: "ack",
          success: true,
          scrollHeight: doc.body.scrollHeight,
          // naturalWidth == 0 -> image did not load
          remote_loaded: img ? img.naturalWidth > 0 : null,
          data_loaded: dimg ? dimg.naturalWidth > 0 : null,
          remote_current_src: img ? img.currentSrc : null
        };
      }
    }
    return { type: "ack", success: false };
  }
};
window.webkit.messageHandlers.astroid.postMessage(
    JSON.stringify({ event: "ready" }));
"""

# 1x1 px PNG
DATA_PNG = ("data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAA"
            "fFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==")

CSP = "default-src 'none'; style-src 'unsafe-inline'; img-src data:"

IFRAME_HTML = f"""<html><head>
<meta http-equiv="Content-Security-Policy" content="{CSP}">
</head><body>
<p>hello iframe</p>
<img class="data" src="{DATA_PNG}">
<img class="remote" src="https://example.com/tracker.png">
</body></html>"""

PAGE_HTML = "<html><body><div id='message_container'></div></body></html>"


class Spike:
    def __init__(self, app):
        self.results = {}
        self.fail = False
        self.app = app

        self.ucm = WebKit.UserContentManager()
        self.ucm.register_script_message_handler("astroid", None)
        self.ucm.connect("script-message-received::astroid", self.on_js_message)
        self.ucm.add_script(WebKit.UserScript.new(
            USER_SCRIPT,
            WebKit.UserContentInjectedFrames.TOP_FRAME,
            WebKit.UserScriptInjectionTime.START,
            None, None))

        self.session = WebKit.NetworkSession.new_ephemeral()
        self.webview = WebKit.WebView(
            user_content_manager=self.ucm,
            network_session=self.session)

        settings = self.webview.get_settings()
        settings.set_enable_javascript(True)

        self.webview.connect("web-process-terminated",
                             lambda wv, reason: print("web process terminated:",
                                                      reason, file=sys.stderr))
        self.webview.connect("load-changed",
                             lambda wv, ev: print("load-changed:", ev,
                                                  file=sys.stderr))
        self.webview.connect("load-failed",
                             lambda wv, ev, uri, err: print("load-failed:", uri,
                                                            err, file=sys.stderr))

        self.window = Gtk.ApplicationWindow(application=self.app)
        self.window.set_child(self.webview)
        self.window.set_default_size(800, 600)
        self.window.present()

        self.webview.load_html(PAGE_HTML, "file:///astroid-spike/")

    # -- JS -> Python -------------------------------------------------------

    def on_js_message(self, ucm, js_value):
        msg = json.loads(js_value.to_string())
        if msg.get("event") == "ready":
            self.results["js_to_python"] = True
            self.step_ping()

    # -- Python -> JS round trips --------------------------------------------

    def send(self, msg, on_ack):
        script = f"JSON.stringify(Astroid.handle({json.dumps(msg)}))"
        def cb(webview, result):
            try:
                value = webview.evaluate_javascript_finish(result)
                on_ack(json.loads(value.to_string()))
            except GLib.Error as e:
                print("FAIL: evaluate_javascript:", e, file=sys.stderr)
                self.finish(fail=True)
        self.webview.evaluate_javascript(script, -1, None, None, None, cb)

    def step_ping(self):
        def ack(a):
            self.results["py_to_js_roundtrip"] = (
                a["success"] and a["echo"] == "marco-polo")
            self.step_add_message()
        self.send({"type": "ping", "payload": "marco-polo"}, ack)

    def step_add_message(self):
        def ack(a):
            self.results["add_message"] = a["success"] and a["count"] == 1
            # give the iframe + image fetch attempts a moment
            GLib.timeout_add(1500, self.step_check_iframe)
        self.send({"type": "add_message", "mid": "m1", "html": IFRAME_HTML}, ack)

    def step_check_iframe(self):
        def ack(a):
            self.results["iframe_renders"] = a["success"] and a["scrollHeight"] > 0
            self.results["csp_blocks_remote"] = a["remote_loaded"] is False
            self.results["data_uri_loads"] = a["data_loaded"] is True
            self.finish()
        self.send({"type": "check_iframe", "mid": "m1"}, ack)
        return False

    def finish(self, fail=False):
        self.fail = fail
        self.app.quit()


def main():
    app = Gtk.Application(application_id="org.astroid.spike")
    spike = {}

    def on_activate(app):
        spike["s"] = Spike(app)
        # safety timeout
        GLib.timeout_add_seconds(30, lambda: (app.quit(), False)[1])

    app.connect("activate", on_activate)
    app.run([])

    s = spike["s"]
    print()
    ok = not s.fail
    for k in ("js_to_python", "py_to_js_roundtrip", "add_message",
              "iframe_renders", "csp_blocks_remote", "data_uri_loads"):
        v = s.results.get(k)
        print(f"  {k:24s} {'OK' if v else 'FAIL'}")
        ok = ok and bool(v)
    print()
    print("SPIKE PASSED" if ok else "SPIKE FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

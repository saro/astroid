/* Astroid thread view DOM engine.
 *
 * JS port of the C++ web-process extension
 * (src/modes/thread_view/webextension/tvextension.cc). Runs as a
 * WebKitUserScript in the top frame; the Python side calls
 * Astroid.handle(msg) via evaluate_javascript and receives the returned
 * ack object. Spontaneous events go through
 * window.webkit.messageHandlers.astroid.postMessage.
 */

window.Astroid = (function () {
  "use strict";

  const STEP = 35, BIG_JUMP = 150, PAGE_JUMP = 600;

  let state = { edit_mode: false, messages: [] };   // from 'state' message
  let allowed_uris = [];
  let allow_remote = false;
  let indent_messages = false;
  let focused_message = null;   // mid
  let focused_element = -1;     // index into state elements (0 = message)
  let part_css = "";

  function post(ev) {
    try {
      window.webkit.messageHandlers.astroid.postMessage(JSON.stringify(ev));
    } catch (e) { /* handler not registered (tests) */ }
  }

  function debug(msg) { post({ event: "debug", msg: String(msg) }); }

  function ack(success) {
    return { type: "ack", success: !!success,
             focus: { mid: focused_message || "", element: focused_element } };
  }

  /* ---- helpers --------------------------------------------------------- */

  function msg_div(mid) {
    return document.getElementById("message_" + mid);
  }

  function state_of(mid) {
    return state.messages.find(m => m.mid === mid) || null;
  }

  function csp_meta(relaxed) {
    const img = relaxed ? "img-src * data: cid:" : "img-src data: cid:";
    return '<meta http-equiv="Content-Security-Policy" content="' +
      "default-src 'none'; style-src 'unsafe-inline'; " + img + '">';
  }

  /* ---- page ------------------------------------------------------------- */

  function handle_page(m) {
    allowed_uris = m.allowed_uris || [];
    part_css = m.part_css || "";

    let style = document.getElementById("astroid_css");
    if (!style) {
      style = document.createElement("style");
      style.id = "astroid_css";
      document.head.appendChild(style);
    }
    style.textContent = m.css || "";
    return ack(true);
  }

  /* ---- message DOM construction ----------------------------------------- */

  function set_text(el, sel, text) {
    const e = el.querySelector(sel);
    if (e) e.innerText = text == null ? "" : text;
  }

  function header_row(title, value, important) {
    const r = document.createElement("div");
    r.className = "field" + (important ? " important" : "");
    const t = document.createElement("div");
    t.className = "title";
    t.innerText = title;
    const v = document.createElement("div");
    v.className = "value";
    v.innerText = value;
    r.appendChild(t); r.appendChild(v);
    return r;
  }

  function address_str(a) {
    if (!a) return "";
    return a.full_address || a.email || "";
  }

  function address_list_str(al) {
    if (!al || !al.length) return "";
    return al.map(address_str).join(", ");
  }

  function build_headers(div, m) {
    const header = div.querySelector(".header");
    header.innerHTML = "";
    header.appendChild(header_row("From", address_str(m.sender), true));
    if (m.to && m.to.length)
      header.appendChild(header_row("To", address_list_str(m.to), false));
    if (m.cc && m.cc.length)
      header.appendChild(header_row("Cc", address_list_str(m.cc), false));
    header.appendChild(header_row("Date", m.date_verbose || m.date_pretty, false));

    set_text(div, ".subject", m.subject);
    set_text(div, ".preview", m.preview || "");

    const tags = div.querySelector(".tags");
    if (tags) tags.innerHTML = m.tag_string || "";

    const av = div.querySelector(".avatar");
    if (av && m.gravatar) av.src = m.gravatar;
  }

  function body_iframe_html(content) {
    return "<html><head>" + csp_meta(allow_remote) +
      "<style>" + part_css + "</style></head><body>" +
      content + "</body></html>";
  }

  function rewrite_cids(html, attachments) {
    if (!attachments) return html;
    for (const a of attachments) {
      if (a.cid && a.content) {
        html = html.split("cid:" + a.cid).join(a.content);
      }
    }
    return html;
  }

  function create_body_part(m, c, container) {
    const tpl = document.getElementById("body_template");
    const part = tpl.cloneNode(true);
    part.removeAttribute("id");
    part.id = "part_" + m.mid + "_" + c.id;
    part.classList.add("body_part");
    if (!c.use || (c.sibling && !c.preferred)) part.classList.add("hidden");

    const iframe = part.querySelector("iframe");
    let content = c.content || "";
    content = rewrite_cids(content, m.attachments);
    iframe.srcdoc = body_iframe_html(content);

    container.appendChild(part);
  }

  function walk_chunks(m, c, container) {
    if (!c) return;
    if (c.viewable && (c.preferred || !c.sibling)) {
      create_body_part(m, c, container);
    } else if (c.viewable) {
      // non-preferred sibling: collapsed marker
      const tpl = document.getElementById("sibling_template");
      const s = tpl.cloneNode(true);
      s.removeAttribute("id");
      s.id = "sibling_" + m.mid + "_" + c.id;
      s.querySelector(".message").innerText =
        "Alternative part (" + c.mime_type + ") - press Enter to view";
      container.appendChild(s);
    }
    for (const k of (c.kids || [])) walk_chunks(m, k, container);
  }

  function insert_attachments(m, div) {
    if (!m.attachments || !m.attachments.length) return;
    const tpl = document.getElementById("attachment_template");
    const container = tpl.cloneNode(true);
    container.removeAttribute("id");
    container.id = "attachments_" + m.mid;
    const table = container.querySelector("table.attachment");
    const proto_row = table.querySelector("tr");
    table.innerHTML = "";

    for (const a of m.attachments) {
      const row = proto_row.cloneNode(true);
      row.id = "attachment_" + m.mid + "_" + a.id;
      row.classList.add("attachment");
      row.querySelector(".filename").innerText =
        (a.filename || "(unnamed)") + "";
      row.querySelector(".filesize").innerText = a.human_size || "";
      const img = row.querySelector("img");
      if (img && a.thumbnail) img.src = a.thumbnail;
      table.appendChild(row);
    }

    const email_container = div.querySelector(".email_container");
    const body = email_container.querySelector(".body");
    email_container.insertBefore(container, body);
  }

  function handle_add_message(m) {
    const tpl = document.getElementById("email_template");
    const div = tpl.cloneNode(true);
    div.removeAttribute("id");
    div.id = "message_" + m.mid;
    div.dataset.mid = m.mid;

    build_headers(div, m);

    const body = div.querySelector(".body");
    body.innerHTML = "";
    if (m.missing_content) {
      body.innerHTML = "<i>Message content is missing.</i>";
    } else if (m.root) {
      walk_chunks(m, m.root, body);
    }

    insert_attachments(m, div);

    if (indent_messages && m.level > 0) {
      div.style.marginLeft = (m.level * 20) + "px";
    }

    const container = document.getElementById("message_container");
    container.appendChild(div);
    return ack(true);
  }

  function handle_update_message(msg) {
    const m = msg.message;
    const div = msg_div(m.mid);
    if (!div) return ack(false);

    if (msg.kind === "tags") {
      const tags = div.querySelector(".tags");
      if (tags) tags.innerHTML = m.tag_string || "";
      return ack(true);
    }

    // visible_parts: rebuild fully
    div.remove();
    return handle_add_message(m);
  }

  function handle_remove_message(m) {
    const div = msg_div(m.mid);
    if (div) div.remove();
    return ack(!!div);
  }

  function handle_clear() {
    const container = document.getElementById("message_container");
    container.innerHTML = '<span id="placeholder"></span>';
    focused_message = null;
    focused_element = -1;
    return ack(true);
  }

  /* ---- state / mark / hide / indent ------------------------------------- */

  function handle_state(m) {
    state = m;
    return ack(true);
  }

  function handle_mark(m) {
    const div = msg_div(m.mid);
    if (!div) return ack(false);
    div.classList.toggle("marked", !!m.marked);
    return ack(true);
  }

  function handle_hidden(m) {
    const div = msg_div(m.mid);
    if (!div) return ack(false);
    div.classList.toggle("hide", !!m.hidden);
    return ack(true);
  }

  function handle_indent(m) {
    indent_messages = !!m.indent;
    for (const ms of state.messages) {
      const div = msg_div(ms.mid);
      if (div) div.style.marginLeft =
        indent_messages && ms.level > 0 ? (ms.level * 20) + "px" : "";
    }
    return ack(true);
  }

  function handle_allow_remote(m) {
    allow_remote = !!m.allow;
    return ack(true);
  }

  function handle_info(m) {
    const div = msg_div(m.mid);
    if (!div) return ack(false);
    const sel = m.warning ? ".email_warning" : ".email_info";
    const el = div.querySelector(sel);
    el.innerText = m.txt || "";
    el.classList.toggle("show", !!m.set);
    return ack(true);
  }

  /* ---- focus + navigation ------------------------------------------------- */

  function element_dom(mid, idx) {
    const ms = state_of(mid);
    if (!ms) return null;
    if (idx <= 0 || !ms.elements || idx >= ms.elements.length)
      return msg_div(mid);
    const el = ms.elements[idx];
    // elements: id-addressable parts created with sid
    return document.getElementById(el.sid) ||
      document.getElementById("part_" + mid + "_" + el.id) ||
      document.getElementById("attachment_" + mid + "_" + el.id) ||
      msg_div(mid);
  }

  function apply_focus(mid, element) {
    document.querySelectorAll(".focused").forEach(
      e => e.classList.remove("focused"));

    focused_message = mid;
    focused_element = element;

    if (!mid) return;
    const div = msg_div(mid);
    if (div) div.classList.add("focused");

    if (element > 0) {
      const el = element_dom(mid, element);
      if (el && el !== div) el.classList.add("focused");
    }
  }

  function scroll_to_element(el) {
    if (!el) return;
    const r = el.getBoundingClientRect();
    if (r.top < 0 || r.bottom > window.innerHeight) {
      el.scrollIntoView({ block: "nearest" });
    }
  }

  function handle_focus(m) {
    apply_focus(m.mid, m.element == null ? 0 : m.element);
    scroll_to_element(element_dom(m.mid, focused_element));
    return ack(true);
  }

  function visible_messages() {
    return state.messages.filter(ms => {
      const d = msg_div(ms.mid);
      return d != null;
    });
  }

  function focus_next_element(force_change) {
    const msgs = visible_messages();
    if (!msgs.length) return;

    let mi = msgs.findIndex(ms => ms.mid === focused_message);
    if (mi < 0) { mi = 0; focused_element = -1; }

    const ms = msgs[mi];
    const div = msg_div(ms.mid);
    const collapsed = div && div.classList.contains("hide");
    const nelems = (!collapsed && ms.elements) ? ms.elements.length : 1;

    if (focused_element + 1 < nelems) {
      // skip unfocusable elements
      let ne = focused_element + 1;
      while (ne < nelems && ms.elements[ne] && ms.elements[ne].focusable === false)
        ne++;
      if (ne < nelems) {
        apply_focus(ms.mid, ne);
        scroll_to_element(element_dom(ms.mid, ne));
        return;
      }
    }

    if (mi + 1 < msgs.length) {
      apply_focus(msgs[mi + 1].mid, 0);
      scroll_to_element(msg_div(msgs[mi + 1].mid));
    } else if (!force_change) {
      window.scrollBy(0, STEP);
    }
  }

  function focus_previous_element(force_change) {
    const msgs = visible_messages();
    if (!msgs.length) return;

    let mi = msgs.findIndex(ms => ms.mid === focused_message);
    if (mi < 0) { mi = 0; }

    const ms = msgs[mi];

    if (focused_element > 0) {
      let pe = focused_element - 1;
      while (pe > 0 && ms.elements[pe] && ms.elements[pe].focusable === false)
        pe--;
      apply_focus(ms.mid, pe);
      scroll_to_element(element_dom(ms.mid, pe));
      return;
    }

    if (mi > 0) {
      const prev = msgs[mi - 1];
      const pdiv = msg_div(prev.mid);
      const pcollapsed = pdiv && pdiv.classList.contains("hide");
      const last = (!pcollapsed && prev.elements) ? prev.elements.length - 1 : 0;
      apply_focus(prev.mid, last);
      scroll_to_element(element_dom(prev.mid, last));
    } else if (!force_change) {
      window.scrollBy(0, -STEP);
    }
  }

  function focus_message_delta(dir, focus_top) {
    const msgs = visible_messages();
    if (!msgs.length) return;
    let mi = msgs.findIndex(ms => ms.mid === focused_message);
    if (mi < 0) mi = 0;
    else mi += dir;
    mi = Math.max(0, Math.min(msgs.length - 1, mi));
    apply_focus(msgs[mi].mid, 0);
    const div = msg_div(msgs[mi].mid);
    if (div) div.scrollIntoView({ block: focus_top ? "start" : "nearest" });
  }

  function update_focus_to_view() {
    // focus the first message visible in the viewport
    const msgs = visible_messages();
    for (const ms of msgs) {
      const div = msg_div(ms.mid);
      const r = div.getBoundingClientRect();
      if (r.bottom > 0 && r.top < window.innerHeight) {
        if (focused_message !== ms.mid) apply_focus(ms.mid, 0);
        return;
      }
    }
  }

  function handle_navigate(m) {
    const dir = m.direction;     // "none"|"specific"|"up"|"down"
    const kind = m.kind;
    const down = dir === "down";

    switch (kind) {
      case "visual_element":
        if (down) focus_next_element(false);
        else focus_previous_element(false);
        break;

      case "visual":
        window.scrollBy(0, down ? STEP : -STEP);
        update_focus_to_view();
        break;

      case "visual_big":
        window.scrollBy(0, down ? BIG_JUMP : -BIG_JUMP);
        update_focus_to_view();
        break;

      case "visual_page":
        window.scrollBy(0, down ? PAGE_JUMP : -PAGE_JUMP);
        update_focus_to_view();
        break;

      case "element":
        if (dir === "specific") {
          const ms = state_of(m.mid);
          let idx = 0;
          if (ms && ms.elements) {
            idx = ms.elements.findIndex(e => e.id === m.element);
            if (idx < 0) idx = 0;
          }
          apply_focus(m.mid, idx);
          scroll_to_element(element_dom(m.mid, idx));
        } else {
          if (down) focus_next_element(true);
          else focus_previous_element(true);
        }
        break;

      case "message":
        focus_message_delta(down ? 1 : -1, !!m.focus_top);
        break;

      case "focus_view":
        update_focus_to_view();
        break;

      case "extreme":
        if (down) {
          window.scrollTo(0, document.body.scrollHeight);
          const msgs = visible_messages();
          if (msgs.length) apply_focus(msgs[msgs.length - 1].mid, 0);
        } else {
          window.scrollTo(0, 0);
          const msgs = visible_messages();
          if (msgs.length) apply_focus(msgs[0].mid, 0);
        }
        break;
    }

    return ack(true);
  }

  /* ---- dispatcher ----------------------------------------------------------- */

  function handle(msg) {
    try {
      switch (msg.type) {
        case "page": return handle_page(msg);
        case "clear_messages": return handle_clear();
        case "add_message": return handle_add_message(msg.message);
        case "update_message": return handle_update_message(msg);
        case "remove_message": return handle_remove_message(msg);
        case "state": return handle_state(msg);
        case "mark": return handle_mark(msg);
        case "hidden": return handle_hidden(msg);
        case "indent": return handle_indent(msg);
        case "allow_remote_images": return handle_allow_remote(msg);
        case "focus": return handle_focus(msg);
        case "navigate": return handle_navigate(msg);
        case "info": return handle_info(msg);
        case "ping": return ack(true);
      }
      debug("unknown message type: " + msg.type);
      return ack(false);
    } catch (e) {
      debug("exception: " + e + " " + (e.stack || ""));
      return ack(false);
    }
  }

  return { handle: handle, _state: () => state };
})();

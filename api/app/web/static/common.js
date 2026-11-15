// Shared helpers for every page. Rules (checked by tests/unit/test_page_sources.py): text is only ever written
// with textContent / text nodes, never as markup; no inline handlers; same-origin requests only.

export async function api(method, path, body) {
  const init = { method, credentials: "same-origin", headers: {} };
  if (body !== undefined) {
    init.headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(body);
  }
  try {
    const response = await fetch(path, init);
    let data = null;
    if ((response.headers.get("content-type") || "").includes("application/json")) {
      try {
        data = await response.json();
      } catch (_) {
        data = null;
      }
    }
    return { status: response.status, data };
  } catch (_) {
    return { status: 0, data: null };
  }
}

const ALLOWED_ATTRIBUTES = new Set([
  "class", "type", "value", "href", "id", "for", "name", "disabled", "hidden", "maxlength", "rows", "autocomplete",
  "role", "required", "placeholder", "selected", "checked", "colspan", "scope", "title", "datetime",
]);

// el("button", { class: "btn", onclick: fn }, "text", childNode): children that are strings become text nodes.
export function el(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props || {})) {
    if (value === undefined || value === null || value === false) continue;
    if (key.startsWith("on") && typeof value === "function") {
      node.addEventListener(key.slice(2), value);
    } else if (ALLOWED_ATTRIBUTES.has(key) || key.startsWith("aria-") || key.startsWith("data-")) {
      node.setAttribute(key, value === true ? "" : String(value));
    }
  }
  for (const child of children.flat()) {
    if (child === undefined || child === null || child === false) continue;
    node.appendChild(typeof child === "string" || typeof child === "number" ? document.createTextNode(String(child)) : child);
  }
  return node;
}

export function clear(node) {
  while (node.firstChild) node.removeChild(node.firstChild);
}

// Show one [data-view] section and hide the others.
export function show(viewId) {
  for (const section of document.querySelectorAll("[data-view]")) {
    section.hidden = section.id !== viewId;
  }
}

export function setStatus(node, kind, text) {
  node.className = "status" + (kind ? " " + kind : "");
  node.textContent = text || "";
  node.hidden = !text;
}

export function fmtTime(iso) {
  if (!iso) return "";
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? "" : date.toLocaleString();
}

export const GENERIC_ERROR = "Something went wrong. Please try again.";

// One fixed wording per failure; server text is only used for validation messages (422).
export function messageFor(status, data) {
  const code = data && data.error;
  if (status === 401) return "You were signed out. Please sign in again.";
  if (status === 403) return "You are not allowed to do this.";
  if (code === "last_admin") return "This would remove the last active admin. Make another admin first.";
  if (code === "already_handled") return "Someone else already handled this person. The list was refreshed.";
  if (code === "sync_busy") return "A sync is already waiting or running.";
  if (status === 409) return "That already exists.";
  if (status === 422) return data && typeof data.message === "string" ? data.message : "That is not valid.";
  if (status === 429) return "Too many attempts. Please wait a little and try again.";
  return GENERIC_ERROR;
}

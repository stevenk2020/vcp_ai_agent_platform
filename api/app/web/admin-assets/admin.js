// Admin area. Sent only to a signed-in admin (the server gates this file). Every action calls the same
// authenticated, origin-checked API as any other client; text from the server is only written with textContent.
import { api, el, clear, setStatus, fmtTime, messageFor, GENERIC_ERROR } from "/static/common.js";

const panel = document.getElementById("panel");
const statusLine = document.getElementById("status");
const ROLES = ["user", "builder", "admin"];

function say(kind, text) {
  setStatus(statusLine, kind, text);
}

function fail(response) {
  say("error", messageFor(response.status, response.data));
}

// ---- confirmation dialog (native <dialog>; Escape or Cancel resolves null) -----------------------------------
function confirmDialog({ title, message, roleChoice = false, defaultRole = "user", okLabel = "Confirm", danger = false }) {
  const dialog = document.getElementById("confirm");
  const form = document.getElementById("confirm-form");
  const select = document.getElementById("confirm-role");
  const ok = document.getElementById("confirm-ok");
  const opener = document.activeElement;
  document.getElementById("confirm-title").textContent = title;
  document.getElementById("confirm-message").textContent = message;
  document.getElementById("confirm-role-field").hidden = !roleChoice;
  clear(select);
  for (const role of ROLES) select.appendChild(el("option", { value: role, selected: role === defaultRole }, role));
  ok.textContent = okLabel;
  ok.className = danger ? "danger" : "";
  return new Promise((resolve) => {
    const controller = new AbortController();
    const finish = (value) => {
      controller.abort();
      if (dialog.open) dialog.close();
      if (opener && opener.focus) opener.focus();
      resolve(value);
    };
    const { signal } = controller;
    form.addEventListener("submit", (event) => { event.preventDefault(); finish({ role: select.value }); }, { signal });
    document.getElementById("confirm-cancel").addEventListener("click", () => finish(null), { signal });
    dialog.addEventListener("close", () => finish(null), { signal });
    dialog.showModal();
    ok.focus();
  });
}

function roleSelect(label, current) {
  return el("select", { "aria-label": label }, ROLES.map((role) => el("option", { value: role, selected: role === current }, role)));
}

function table(headers, rows) {
  return el("div", { class: "table-wrap" },
    el("table", { class: "table" },
      el("thead", {}, el("tr", {}, headers.map((h) => el("th", { scope: "col" }, h)))),
      el("tbody", {}, rows)));
}

function cell(label, ...children) {
  return el("td", { "data-label": label }, ...children);
}

// ---- Waiting (approval queue) --------------------------------------------------------------------------------
async function renderQueue() {
  const response = await api("GET", "/v1/admin/users?status=pending");
  if (response.status !== 200) return fail(response);
  clear(panel);
  panel.appendChild(el("h2", {}, "Waiting for approval"));
  const people = response.data.items;
  if (people.length === 0) {
    panel.appendChild(el("p", { class: "muted" }, "Nothing is waiting."));
    return;
  }
  const rows = people.map((person) => {
    const select = roleSelect(`Role for ${person.email}`, "user");
    const decide = async (action) => {
      const role = select.value;
      const approving = action === "approve";
      const choice = await confirmDialog({
        title: approving ? "Approve this person?" : "Reject this person?",
        message: approving ? `${person.email} will be able to sign in as ${role}.` : `${person.email} will not be able to sign in.`,
        okLabel: approving ? "Approve" : "Reject",
        danger: !approving,
      });
      if (!choice) return;
      const result = await api("POST", `/v1/admin/users/${person.id}/${action}?expect_status=pending`, approving ? { role } : undefined);
      if (result.status === 200) say("ok", approving ? `${person.email} approved as ${role}.` : `${person.email} rejected.`);
      else fail(result);
      await render();
    };
    return el("tr", {},
      cell("Email", person.email),
      cell("Name", person.name || "(not given)"),
      cell("Reason", person.request_reason || "(not given)"),
      cell("Requested", fmtTime(person.request_submitted_at || person.created_at)),
      cell("Decision", el("div", { class: "row" },
        select,
        el("button", { type: "button", onclick: () => decide("approve") }, "Approve"),
        el("button", { type: "button", class: "secondary", onclick: () => decide("reject") }, "Reject"))));
  });
  panel.appendChild(table(["Email", "Name", "Reason", "Requested", "Decision"], rows));
}

// ---- tabs ----------------------------------------------------------------------------------------------------
const TABS = {
  queue: renderQueue,
};

function currentTab() {
  const name = location.hash.replace("#", "");
  return TABS[name] ? name : "queue";
}

async function render() {
  const name = currentTab();
  for (const link of document.querySelectorAll("nav.tabs a")) {
    if (link.dataset.tab === name) link.setAttribute("aria-current", "page");
    else link.removeAttribute("aria-current");
  }
  try {
    await TABS[name]();
  } catch (_) {
    say("error", GENERIC_ERROR);
  }
}

window.addEventListener("hashchange", () => { say("", ""); render(); });
render();

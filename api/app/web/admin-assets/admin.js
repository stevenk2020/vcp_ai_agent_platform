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

// ---- Allowlist -----------------------------------------------------------------------------------------------
async function renderAllowlist() {
  const response = await api("GET", "/v1/admin/allowlist");
  if (response.status !== 200) return fail(response);
  clear(panel);
  panel.appendChild(el("h2", {}, "Who may sign in"));
  panel.appendChild(el("p", { class: "muted" },
    "People whose email or domain is listed here can sign in without waiting for approval, with the role shown."));

  const kind = el("select", { id: "entry-kind", "aria-label": "Kind" },
    el("option", { value: "email" }, "email"), el("option", { value: "domain" }, "domain"));
  const value = el("input", { id: "entry-value", type: "text", maxlength: "254", required: true, autocomplete: "off",
    placeholder: "name@example.com or example.com", "aria-label": "Email address or domain" });
  const role = roleSelect("Default role", "user");
  const form = el("form", { class: "row" }, kind, value, role, el("button", { type: "submit" }, "Add"));
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const result = await api("POST", "/v1/admin/allowlist", { kind: kind.value, value: value.value.trim(), default_role: role.value });
    if (result.status === 201) say("ok", `${result.data.value} added as ${result.data.default_role}.`);
    else fail(result);
    await render();
  });
  panel.appendChild(form);

  const entries = response.data.items;
  if (entries.length === 0) {
    panel.appendChild(el("p", { class: "muted" }, "The allowlist is empty."));
    return;
  }
  const rows = entries.map((entry) => el("tr", {},
    cell("Kind", entry.kind),
    cell("Value", entry.value),
    cell("Default role", entry.default_role),
    cell("Added by", entry.added_by_email || "(unknown)"),
    cell("Added", fmtTime(entry.created_at)),
    cell("", el("button", { type: "button", class: "secondary", onclick: async () => {
      const choice = await confirmDialog({
        title: "Remove this entry?",
        message: `Removing ${entry.value} signs out the people who were allowed only by it, immediately. They are suspended until an admin lets them back in.`,
        okLabel: "Remove",
        danger: true,
      });
      if (!choice) return;
      const result = await api("DELETE", `/v1/admin/allowlist/${entry.id}`);
      if (result.status === 204) say("ok", `${entry.value} removed.`);
      else fail(result);
      await render();
    } }, "Remove"))));
  panel.appendChild(table(["Kind", "Value", "Default role", "Added by", "Added", ""], rows));
}

// ---- People --------------------------------------------------------------------------------------------------
async function renderPeople() {
  const response = await api("GET", "/v1/admin/users");
  if (response.status !== 200) return fail(response);
  clear(panel);
  panel.appendChild(el("h2", {}, "People"));
  const people = response.data.items;
  if (people.length === 0) {
    panel.appendChild(el("p", { class: "muted" }, "Nobody has signed in yet."));
    return;
  }
  const rows = people.map((person) => {
    const select = roleSelect(`Role for ${person.email}`, person.role);
    // every action states the status this row showed; the server refuses it if someone else changed the person since
    const act = async (path, body, okText, confirmOptions) => {
      const choice = await confirmDialog(confirmOptions);
      if (!choice) return;
      const result = await api("POST", `/v1/admin/users/${person.id}/${path}?expect_status=${person.status}`, body);
      if (result.status === 200) say("ok", okText);
      else fail(result);
      await render();
    };
    const buttons = [];
    if (person.status === "active") {
      buttons.push(el("button", { type: "button", onclick: () => act("role", { role: select.value },
        `${person.email} is now ${select.value}.`,
        { title: "Change role?", message: `${person.email} will become ${select.value}.`, okLabel: "Change role" }) }, "Change role"));
      buttons.push(el("button", { type: "button", class: "secondary", onclick: () => act("suspend", undefined,
        `${person.email} suspended.`,
        { title: "Suspend this person?", message: `${person.email} is signed out at once and cannot sign in until an admin reactivates them.`,
          okLabel: "Suspend", danger: true }) }, "Suspend"));
    } else if (person.status === "suspended" || person.status === "rejected") {
      buttons.push(el("button", { type: "button", onclick: () => act("approve", { role: select.value },
        `${person.email} reactivated as ${select.value}.`,
        { title: "Reactivate this person?", message: `${person.email} will be able to sign in again as ${select.value}.`,
          okLabel: "Reactivate" }) }, "Reactivate"));
    } else {
      buttons.push(el("span", { class: "muted" }, "Decide in the Waiting tab"));
    }
    return el("tr", {},
      cell("Email", person.email),
      cell("Name", person.name || ""),
      cell("Status", person.status),
      cell("Role", person.role),
      cell("Last sign-in", fmtTime(person.last_login_at)),
      cell("Actions", el("div", { class: "row" }, person.status === "pending" ? null : select, ...buttons)));
  });
  panel.appendChild(table(["Email", "Name", "Status", "Role", "Last sign-in", "Actions"], rows));
}

// ---- Audit log -----------------------------------------------------------------------------------------------
const AUDIT_PAGE = 50;

async function renderAudit() {
  const probe = await api("GET", "/v1/admin/audit?limit=1");
  if (probe.status !== 200) return fail(probe);
  clear(panel);
  panel.appendChild(el("h2", {}, "Audit log"));

  const action = el("select", { id: "audit-action", "aria-label": "Action" }, el("option", { value: "" }, "All actions"),
    probe.data.actions.map((name) => el("option", { value: name }, name)));
  const person = el("input", { id: "audit-person", type: "text", maxlength: "254", autocomplete: "off",
    placeholder: "email starts with…", "aria-label": "Person (email)" });
  const apply = el("button", { type: "submit" }, "Apply");
  const form = el("form", { class: "row" }, action, person, apply);
  const body = el("tbody", {});
  const older = el("button", { type: "button", class: "secondary", hidden: true }, "Older events");
  const note = el("p", { class: "muted", hidden: true });
  let lastId = null;

  const load = async (append) => {
    const query = [`limit=${AUDIT_PAGE}`];
    if (action.value) query.push(`action=${encodeURIComponent(action.value)}`);
    if (person.value.trim()) query.push(`person=${encodeURIComponent(person.value.trim())}`);
    if (append && lastId) query.push(`before=${lastId}`);
    const response = await api("GET", `/v1/admin/audit?${query.join("&")}`);
    if (response.status !== 200) return fail(response);
    say("", "");
    if (!append) clear(body);
    for (const event of response.data.items) {
      body.appendChild(el("tr", {},
        cell("Time", fmtTime(event.at)),
        cell("Actor", event.actor_email || "(none)"),
        cell("Action", event.action),
        cell("Target", event.target || ""),
        cell("Outcome", event.outcome),
        cell("IP", event.ip || ""),
        cell("Details", JSON.stringify(event.detail || {}))));
    }
    if (response.data.items.length) lastId = response.data.items[response.data.items.length - 1].id;
    const more = response.data.items.length === AUDIT_PAGE;
    older.hidden = !more;
    note.textContent = body.children.length === 0 ? "Nothing matched." : more ? "" : "No more events.";
    note.hidden = !note.textContent;
  };

  form.addEventListener("submit", (event) => { event.preventDefault(); lastId = null; load(false); });
  older.addEventListener("click", () => load(true));
  panel.appendChild(form);
  panel.appendChild(el("div", { class: "table-wrap" }, el("table", { class: "table" },
    el("thead", {}, el("tr", {}, ["Time", "Actor", "Action", "Target", "Outcome", "IP", "Details"].map((h) => el("th", { scope: "col" }, h)))),
    body)));
  panel.appendChild(note);
  panel.appendChild(older);
  await load(false);
}

// ---- Data (sync status and run history) ---------------------------------------------------------------------
const STATE_LABEL = { healthy: "Healthy", stale: "Stale", failing: "Failing", no_data: "No data yet" };
const STATE_BADGE = { healthy: "ok", stale: "warn", failing: "bad", no_data: "none" };
const REFRESH_MS = 15000;
let dataTimer = null;

function stopRefresh() {
  if (dataTimer !== null) {
    clearInterval(dataTimer);
    dataTimer = null;
  }
}

function minutes(seconds) {
  if (seconds === null || seconds === undefined) return "";
  return seconds < 60 ? "under a minute" : `${Math.floor(seconds / 60)} min`;
}

function whenCell(label, iso) {
  if (!iso) return cell(label, "");
  return cell(label, el("span", { title: iso }, fmtTime(iso)));
}

async function runNow(item, reload) {
  const answer = await confirmDialog({
    title: "Sync now?",
    message: "This reads the whole source table now. Normal syncs run every 10 minutes. Continue?",
    okLabel: "Sync now",
  });
  if (!answer) return;
  const response = await api("POST", `/v1/admin/sync/${encodeURIComponent(item.table)}/run`);
  await reload(); // reloading clears the status line, so the message comes after it
  if (response.status === 202) say("ok", "Requested: the run will start within a few seconds.");
  else fail(response);
}

function statusBlock(item, reload) {
  const facts = [
    ["Data as of", item.data_as_of ? `${fmtTime(item.data_as_of)} (${minutes(item.age_seconds)} ago)` : "never"],
    ["Rows", item.copied_rows === null ? "" : `${item.copied_rows} copied of ${item.source_rows} read`],
    ["Failed or refused runs in a row", String(item.consecutive_failures)],
    ["Latest outcome", [item.last_outcome, item.last_reason].filter(Boolean).join(": ") || "none yet"],
    ["Right now", item.running ? "a sync is running" : item.request_pending ? "a sync was requested" : "idle"],
  ];
  const list = el("dl", { class: "facts" });
  for (const [term, value] of facts) list.append(el("dt", {}, term), el("dd", {}, value));
  const badge = el("span", { class: "badge " + (STATE_BADGE[item.state] || "none") }, STATE_LABEL[item.state] || "Unknown");
  const busy = item.running || item.request_pending;
  const button = el("button", { type: "button", disabled: busy, onclick: () => runNow(item, reload) }, "Sync now");
  return el("section", { "aria-label": `Table ${item.table}` }, el("h3", {}, item.table, " ", badge), list,
    el("div", { class: "row" }, button, busy ? el("span", { class: "muted" }, "A sync is already waiting or running.") : null));
}

async function renderData() {
  stopRefresh();
  clear(panel);
  panel.appendChild(el("h2", {}, "Data"));
  panel.appendChild(el("p", { class: "muted" },
    "Copies of source tables that the platform answers questions from. They refresh about every 10 minutes."));
  const blocks = el("div", {});
  const runsBody = el("tbody", {});
  const runsNote = el("p", { class: "muted", hidden: true });
  panel.appendChild(blocks);
  panel.appendChild(el("h3", {}, "Latest runs"));
  panel.appendChild(el("div", { class: "table-wrap" }, el("table", { class: "table" },
    el("thead", {}, el("tr", {}, ["Started", "Table", "Trigger", "Outcome", "Reason", "Rows", "Duration", "Message"].map((h) => el("th", { scope: "col" }, h)))),
    runsBody)));
  panel.appendChild(runsNote);

  const load = async () => {
    const [status, runs] = await Promise.all([api("GET", "/v1/admin/sync"), api("GET", "/v1/admin/sync/runs?limit=20")]);
    if (status.status !== 200) return fail(status);
    if (runs.status !== 200) return fail(runs);
    say("", "");
    clear(blocks);
    if (status.data.tables.length === 0) {
      blocks.appendChild(el("p", {}, "No synced table is registered yet. The sync service registers its tables when it starts."));
    }
    for (const item of status.data.tables) blocks.appendChild(statusBlock(item, load));
    clear(runsBody);
    for (const run of runs.data.items) {
      runsBody.appendChild(el("tr", {},
        whenCell("Started", run.started_at),
        cell("Table", run.table),
        cell("Trigger", run.trigger),
        cell("Outcome", run.outcome),
        cell("Reason", run.reason_code || ""),
        cell("Rows", run.copied_rows === null ? (run.source_rows === null ? "" : `${run.source_rows} read`) : `${run.copied_rows}`),
        cell("Duration", run.duration_ms === null ? "" : `${(run.duration_ms / 1000).toFixed(1)} s`),
        cell("Message", run.error_text || "")));
    }
    runsNote.textContent = runs.data.items.length === 0 ? "No runs yet." : "";
    runsNote.hidden = !runsNote.textContent;
  };

  await load();
  // refresh while this tab is open and visible; the timer is cleared as soon as another tab is shown
  dataTimer = setInterval(() => {
    if (currentTab() !== "data") return stopRefresh();
    if (document.visibilityState === "visible") load();
  }, REFRESH_MS);
}

// ---- tabs ----------------------------------------------------------------------------------------------------
const TABS = {
  queue: renderQueue,
  allowlist: renderAllowlist,
  people: renderPeople,
  data: renderData,
  audit: renderAudit,
};

function currentTab() {
  const name = location.hash.replace("#", "");
  return TABS[name] ? name : "queue";
}

async function render() {
  const name = currentTab();
  if (name !== "data") stopRefresh();
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

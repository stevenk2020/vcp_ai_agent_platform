import { api, clear, setStatus, show, messageFor, GENERIC_ERROR } from "/static/common.js";

const $ = (id) => document.getElementById(id);

const RESULT_TEXT = {
  refused: ["This account cannot sign in", "This account cannot sign in. If you think this is a mistake, contact an administrator."],
  error: ["Sign-in did not complete", "Sign-in did not complete. Please try again."],
};

function showMessage(title, text) {
  $("message-title").textContent = title;
  $("message-text").textContent = text;
  show("view-message");
}

function showInside(access) {
  $("inside-email").textContent = access.email;
  $("inside-role").textContent = access.role || "";
  $("admin-link").hidden = access.role !== "admin";
  show("view-inside");
}

function showRequest(access) {
  $("request-email").textContent = access.email;
  setStatus($("request-note"), "", "");
  if (access.submitted) {
    $("request-form").hidden = true;
    $("sent-name").textContent = access.name || "(not given)";
    $("sent-reason").textContent = access.reason || "(not given)";
    $("request-sent").hidden = false;
  } else {
    $("request-form").hidden = false;
    $("request-sent").hidden = true;
  }
  show("view-request");
}

async function load(result) {
  const [access, health] = await Promise.all([api("GET", "/v1/access"), api("GET", "/v1/health")]);
  if (access.status === 200 && access.data) {
    if (access.data.status === "active") return showInside(access.data);
    return showRequest(access.data);
  }
  if (access.status === 401 && result && RESULT_TEXT[result]) return showMessage(...RESULT_TEXT[result]);
  if (access.status !== 401) return showMessage("Something went wrong", GENERIC_ERROR);
  // the email-code form only exists when the server says mail delivery is on
  const mail = health.data && health.data.mail_backend;
  $("code-form").hidden = !(mail && mail !== "disabled");
  show("view-signin");
}

async function signOut() {
  await api("POST", "/v1/auth/logout");
  location.assign("/");
}

function wireCodeForm() {
  const form = $("code-form");
  let codeSent = false;
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const note = $("signin-note");
    const email = $("code-email").value.trim();
    if (!codeSent) {
      const response = await api("POST", "/v1/auth/code/request", { email });
      if (response.status === 202) {
        codeSent = true;
        $("code-field").hidden = false;
        $("code-submit").textContent = "Sign in";
        setStatus(note, "ok", "If this address can receive mail, a code has been sent. Enter it here.");
        $("code-value").focus();
      } else {
        setStatus(note, "error", messageFor(response.status, response.data));
      }
      return;
    }
    const response = await api("POST", "/v1/auth/code/verify", { email, code: $("code-value").value.trim() });
    if (response.status === 200 && response.data && response.data.status !== "refused") return load(null);
    if (response.status === 200) return showMessage(...RESULT_TEXT.refused);
    if (response.status === 401) return setStatus(note, "error", "The code is invalid or has expired.");
    setStatus(note, "error", messageFor(response.status, response.data));
  });
}

function wireRequestForm() {
  $("request-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const name = $("request-name").value.trim();
    const reason = $("request-reason").value.trim();
    const response = await api("POST", "/v1/access/request", { name: name || null, reason: reason || null });
    if (response.status === 401) return showMessage("You were signed out", "You were signed out. Please sign in again.");
    if (response.status === 200 || response.status === 201) return load(null);
    setStatus($("request-note"), "error", messageFor(response.status, response.data));
  });
  $("request-refresh").addEventListener("click", () => load(null));
}

function main() {
  const params = new URLSearchParams(location.search);
  const result = params.get("signin");
  if (result) history.replaceState(null, "", "/");
  $("signout-inside").addEventListener("click", signOut);
  $("signout-request").addEventListener("click", signOut);
  wireCodeForm();
  wireRequestForm();
  load(result);
}

main();

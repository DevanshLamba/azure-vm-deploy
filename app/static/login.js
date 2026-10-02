/* CloudTasks sign-in page. External file because the Content-Security-Policy blocks inline scripts.
   The password is only ever sent in the POST body to /api/auth/login; it is never stored. */
"use strict";

const $ = (s) => document.querySelector(s);
const form = $("#login-form");
const user = $("#username");
const pass = $("#password");
const error = $("#login-error");
const submit = $("#login-submit");
const reveal = $("#reveal");
const caps = $("#caps-hint");
const themeBtn = $("#theme-toggle");

function setTheme(theme) {
  document.documentElement.dataset.theme = theme;
  try { localStorage.setItem("ct-theme", theme); } catch { /* private mode */ }
  themeBtn.setAttribute("aria-label", theme === "dark" ? "Switch to light theme" : "Switch to dark theme");
  document.querySelector('meta[name="theme-color"]').setAttribute("content", theme === "dark" ? "#1B1920" : "#FBF7F2");
}
setTheme(document.documentElement.dataset.theme === "dark" ? "dark" : "light");
themeBtn.addEventListener("click", () => setTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark"));

function showError(message) {
  error.textContent = message;
  error.hidden = false;
  form.classList.remove("shake");
  void form.offsetWidth; // restart the animation
  form.classList.add("shake");
}

reveal.addEventListener("click", () => {
  const show = pass.type === "password";
  pass.type = show ? "text" : "password";
  reveal.setAttribute("aria-pressed", String(show));
  reveal.setAttribute("aria-label", show ? "Hide password" : "Show password");
  pass.focus();
});

pass.addEventListener("keyup", (e) => { caps.hidden = !(e.getModifierState && e.getModifierState("CapsLock")); });
pass.addEventListener("blur", () => { caps.hidden = true; });
for (const el of [user, pass]) el.addEventListener("input", () => { error.hidden = true; el.removeAttribute("aria-invalid"); });

form.addEventListener("submit", async (e) => {
  e.preventDefault();
  if (!user.value.trim() || !pass.value) {
    const empty = !user.value.trim() ? user : pass;
    empty.setAttribute("aria-invalid", "true");
    showError("Please enter your username and password.");
    empty.focus();
    return;
  }
  submit.disabled = true;
  submit.textContent = "Signing in…";
  error.hidden = true;
  try {
    const res = await fetch("/api/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify({ username: user.value.trim(), password: pass.value }),
    });
    if (res.ok) {
      submit.textContent = "Welcome!";
      location.replace("/");
      return;
    }
    let detail = "";
    try { detail = (await res.json()).detail; } catch { /* not JSON */ }
    if (res.status === 429) showError(typeof detail === "string" ? detail : "Too many attempts. Please wait a few minutes.");
    else if (res.status === 401) showError("Wrong username or password.");
    else showError("Something went wrong. Please try again.");
    pass.value = "";
    pass.focus();
  } catch {
    showError("Can't reach the server. Check your connection and try again.");
  } finally {
    if (submit.textContent !== "Welcome!") {
      submit.disabled = false;
      submit.textContent = "Sign in";
    }
  }
});

user.focus();

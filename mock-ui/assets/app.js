// Shared shell for the mock UI: sidebar, top bar, role switcher, toasts.
// All data is fictional sample data. Nothing is sent anywhere.

const ROLES = {
  administrator: { label: "Administrator", user: "Priya Raman", initials: "PR" },
  bd_manager: { label: "BD Manager", user: "Marcus Hale", initials: "MH" },
  recruiter: { label: "Recruiter", user: "Dana Okafor", initials: "DO" },
  account_manager: { label: "Account Manager", user: "Leah Kim", initials: "LK" },
  sales_manager: { label: "Sales Manager", user: "Tom Becker", initials: "TB" },
  client: { label: "Client (hiring manager)", user: "Rachel Lee · Northfield Axle", initials: "RL" },
};

const STAFF = ["administrator", "bd_manager", "recruiter", "account_manager", "sales_manager"];

// Pending approvals per role (matches the sample queue on approvals.html).
const QUEUE_COUNT = { administrator: 0, bd_manager: 3, recruiter: 4, account_manager: 0, sales_manager: 1 };

const NAV = [
  { group: "Overview" },
  { id: "dashboard", href: "index.html", label: "Dashboard", roles: STAFF },
  { id: "approvals", href: "approvals.html", label: "Approvals", roles: STAFF, badge: true },
  { group: "Client acquisition" },
  { id: "companies", href: "companies.html", label: "Companies & outreach", roles: ["administrator", "bd_manager", "sales_manager", "account_manager"] },
  { id: "replies", href: "replies.html", label: "Client replies", roles: ["administrator", "bd_manager", "account_manager", "sales_manager"] },
  { group: "Recruiting" },
  { id: "jobs", href: "jobs.html", label: "Jobs", roles: STAFF },
  { id: "candidates", href: "candidates.html", label: "Candidate ranking", roles: ["administrator", "recruiter", "account_manager"] },
  { id: "linkedin", href: "linkedin-tasks.html", label: "LinkedIn tasks", roles: ["administrator", "recruiter"] },
  { id: "match", href: "match.html", label: "Match & submission", roles: ["administrator", "recruiter", "account_manager"] },
  { id: "followups", href: "followups.html", label: "Follow-ups", roles: STAFF },
  { group: "Settings" },
  { id: "admin", href: "admin.html", label: "Users & approvers", roles: ["administrator"] },
  { id: "status", href: "build-status.html", label: "Build status", roles: STAFF },
  { group: "Client" },
  { id: "portal", href: "client-portal.html", label: "Client portal", roles: ["client", "administrator"] },
];

function readRole() {
  try {
    const saved = localStorage.getItem("rap-mock-role");
    if (saved && ROLES[saved]) return saved;
  } catch (e) { /* storage unavailable: fall back to page default */ }
  return document.body.dataset.defaultRole || "recruiter";
}

function saveRole(role) {
  try { localStorage.setItem("rap-mock-role", role); } catch (e) { /* ignore */ }
}

function toast(message) {
  let el = document.querySelector(".toast");
  if (!el) {
    el = document.createElement("div");
    el.className = "toast";
    document.body.appendChild(el);
  }
  el.textContent = message;
  el.classList.add("show");
  clearTimeout(el._t);
  el._t = setTimeout(() => el.classList.remove("show"), 2600);
}

function renderShell(role) {
  const page = document.body.dataset.page;
  const sidebar = document.getElementById("sidebar");
  const items = NAV.map((item) => {
    if (item.group) return `<div class="nav-group">${item.group}</div>`;
    if (!item.roles.includes(role)) return "";
    const count = item.badge && QUEUE_COUNT[role] ? `<span class="count">${QUEUE_COUNT[role]}</span>` : "";
    return `<a href="${item.href}" class="${item.id === page ? "active" : ""}">${item.label}${count}</a>`;
  }).join("");
  sidebar.innerHTML = `
    <div class="logo"><div class="logo-mark">CBS</div><div>Talent Agents<small>Recruiting platform</small></div></div>
    <nav class="nav">${items}</nav>`;
  // Drop group headings with no visible links under them.
  sidebar.querySelectorAll(".nav-group").forEach((g) => {
    const next = g.nextElementSibling;
    if (!next || next.classList.contains("nav-group")) g.remove();
  });

  const r = ROLES[role];
  document.getElementById("topbar").innerHTML = `
    <div class="search">Search companies, jobs, candidates…</div>
    <div class="spacer"></div>
    <label class="role-switch">Viewing as
      <select id="role-select" aria-label="Switch role">
        ${Object.entries(ROLES).map(([k, v]) => `<option value="${k}" ${k === role ? "selected" : ""}>${v.label}</option>`).join("")}
      </select>
    </label>
    <div class="row" style="gap:10px"><div class="avatar">${r.initials}</div><div style="font-size:13px;line-height:1.2"><b>${r.user}</b><div class="faint">${r.label}</div></div></div>`;
  document.getElementById("role-select").addEventListener("change", (e) => {
    saveRole(e.target.value);
    applyRole(e.target.value);
  });
}

let currentRole = null;

function applyRole(role) {
  currentRole = role;
  renderShell(role);
  document.querySelectorAll("[data-roles]").forEach((el) => {
    const allowed = el.dataset.roles.split(/\s+/);
    el.classList.toggle("hidden-for-role", !allowed.includes(role));
  });
  const navItem = NAV.find((n) => n.id === document.body.dataset.page);
  const blocked = navItem && !navItem.roles.includes(role);
  const content = document.querySelector(".content");
  let notice = document.getElementById("role-blocked");
  if (blocked && !notice) {
    notice = document.createElement("div");
    notice.id = "role-blocked";
    notice.className = "notice warn";
    notice.style.marginBottom = "16px";
    content.prepend(notice);
  }
  if (notice) {
    notice.style.display = blocked ? "flex" : "none";
    notice.textContent = `The ${ROLES[role].label} role cannot open this screen in the real application. It is shown here for the mock only.`;
  }
  document.dispatchEvent(new CustomEvent("rolechange", { detail: role }));
}

document.addEventListener("DOMContentLoaded", () => {
  const banner = document.createElement("div");
  banner.className = "mock-banner";
  banner.textContent = "Mock UI: sample data only. Switch roles at the top right to see what each user sees.";
  document.body.prepend(banner);
  applyRole(readRole());

  // Any element with data-toast shows a message instead of doing a real action.
  document.body.addEventListener("click", (e) => {
    const t = e.target.closest("[data-toast]");
    if (t) { e.preventDefault(); toast(t.dataset.toast); }
  });
});

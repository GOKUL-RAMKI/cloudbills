(function renderLayout() {
  const header = document.getElementById("app-header");
  if (!header) return;
  const page = document.body.dataset.page || "dashboard";
  const links = [
    ["dashboard", "/", "Dashboard"],
    ["resources", "/pages/resources.html", "Resources"],
    ["recommendations", "/pages/recommendations.html", "Approvals"],
    ["billing", "/pages/billing.html", "Billing"],
    ["budgets", "/pages/budgets.html", "Budgets"],
    ["audit", "/pages/audit.html", "Audit"],
  ];
  header.className = "app-header";
  header.innerHTML = `
    <a class="brand" href="/">
      <span class="brand-mark">CB</span>
      <span>clou<em>DB</em>ills</span>
    </a>
    <nav class="app-nav">
      ${links
        .map(
          ([key, href, label]) =>
            `<a href="${href}" class="${key === page ? "active" : ""}">${label}</a>`
        )
        .join("")}
    </nav>
    <div class="acting-user">Acting as <b>Ananya Rao</b> &middot; FinOps Admin</div>
  `;
})();

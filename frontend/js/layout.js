(function renderLayout() {
  const header = document.getElementById("app-header");
  if (!header) return;
  const page = document.body.dataset.page || "dashboard";
  const links = [
    ["dashboard", "/", "Overview"],
    ["resources", "/pages/resources.html", "Resources"],
    ["recommendations", "/pages/recommendations.html", "Approvals"],
    ["billing", "/pages/billing.html", "Billing"],
    ["budgets", "/pages/budgets.html", "Budgets"],
    ["audit", "/pages/audit.html", "Audit"],
  ];
  header.className = "app-header";
  header.innerHTML = `
    <div class="header-inner">
      <a class="brand" href="/"><span>clou<em>DB</em>ills</span></a>
      <nav class="app-nav" aria-label="Main">
        ${links
          .map(
            ([key, href, label]) =>
              `<a href="${href}"${key === page ? ' class="active" aria-current="page"' : ""}>${label}</a>`
          )
          .join("")}
      </nav>
      <div class="acting-user">
        <span>Acting as <b>Ananya Rao</b></span>
        <span class="acting-role">FinOps admin</span>
      </div>
    </div>
  `;
  if (!document.querySelector(".skip-link")) {
    header.insertAdjacentHTML(
      "beforebegin",
      '<a class="skip-link" href="#main">Skip to content</a>'
    );
  }
})();

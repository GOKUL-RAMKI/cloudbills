async function loadDashboard() {
  const kpis = document.getElementById("kpis");
  try {
    const [dashboard, idle, orphaned, missing, alerts] = await Promise.all([
      api("/dashboard"),
      api("/detections/idle"),
      api("/detections/orphaned-volumes"),
      api("/detections/missing-tags"),
      api("/budgets/alerts?acknowledged=false"),
    ]);

    const s = dashboard.summary;
    const delta = Number(s.monthly_spend) - Number(s.previous_month_spend);
    const deltaLabel =
      s.previous_month_spend > 0
        ? `${delta >= 0 ? "+" : ""}${compactMoney(delta)} vs last month`
        : "No prior-month baseline";

    const cards = [
      ["Monthly spend", compactMoney(s.monthly_spend), deltaLabel, "accent"],
      ["Estimated savings", compactMoney(s.estimated_savings), "Open opportunities in approvals", "blue"],
      ["Pending approvals", num(s.pending_approvals), "Awaiting a decision", "amber"],
      ["Open budget alerts", num(s.open_alerts), "Budgets over their warning line", s.open_alerts > 0 ? "red" : "accent"],
      ["Active resources", `${num(s.active_resources)} / ${num(s.total_resources)}`, "Resources in scope", ""],
      ["Idle compute", num(s.idle_candidates), "Flagged by the idle compute rule", ""],
      ["Orphaned volumes", num(s.orphaned_volumes), "Storage with no active parent", ""],
      ["Missing tags", num(s.missing_tags), "Resources missing FinOps tags", ""],
    ];

    kpis.innerHTML = cards
      .map(
        ([label, value, sub, tone]) => `
        <div class="card kpi ${tone}">
          <div class="kpi-label">${escapeHtml(label)}</div>
          <div class="kpi-value">${escapeHtml(value)}</div>
          <div class="kpi-sub">${escapeHtml(sub)}</div>
        </div>`
      )
      .join("");

    renderSpendChart(s.spend_by_month || []);
    renderTopProjects(s.top_projects || []);
    renderRecent(s.recent_recommendations || []);
    renderAlerts(alerts.items || []);
    renderDetections(idle.items || [], orphaned.items || [], missing.items || []);
  } catch (error) {
    document.getElementById("dashboard-error").innerHTML =
      `<div class="notice error">Failed to load dashboard: ${escapeHtml(error.message)}</div>`;
  }
}

function renderSpendChart(rows) {
  const container = document.getElementById("spend-chart");
  if (!rows.length) {
    container.innerHTML = `<div class="empty"><strong>No billing facts</strong>Upload a batch to populate the trend.</div>`;
    return;
  }
  const max = Math.max(...rows.map((row) => Number(row.amount || 0)), 1);
  container.setAttribute("role", "img");
  container.setAttribute(
    "aria-label",
    `Monthly spend: ${rows
      .map((row) => `${monthLabel(row.month)} ${money(row.amount)}`)
      .join(", ")}`
  );
  container.innerHTML = rows
    .map((row) => {
      const amount = Number(row.amount || 0);
      const height = Math.max(6, Math.round((amount / max) * 100));
      return `
        <div class="bar-col">
          <div class="bar-value">${escapeHtml(compactMoney(amount))}</div>
          <div class="bar" style="height:${height}%" title="${escapeHtml(monthLabel(row.month))}: ${escapeHtml(money(amount))}"></div>
          <div class="bar-label">${escapeHtml(monthLabel(row.month))}</div>
        </div>`;
    })
    .join("");
}

function renderTopProjects(rows) {
  const body = document.getElementById("top-projects");
  if (!rows.length) {
    body.innerHTML = `<tr><td colspan="4" class="empty">No project spend recorded for this month.</td></tr>`;
    return;
  }
  const max = Math.max(...rows.map((row) => Number(row.amount || 0)), 1);
  body.innerHTML = rows
    .map((row) => {
      const amount = Number(row.amount || 0);
      const share = Math.max(4, Math.round((amount / max) * 100));
      return `
        <tr>
          <td>${escapeHtml(row.project_name)}</td>
          <td class="muted">${escapeHtml(row.team_name)}</td>
          <td class="num">${escapeHtml(money(amount))}</td>
          <td>
            <div class="progress" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${share}" title="${share}% of top project spend">
              <span style="width:${share}%"></span>
            </div>
          </td>
        </tr>`;
    })
    .join("");
}

function renderRecent(rows) {
  const body = document.getElementById("recent-recommendations");
  if (!rows.length) {
    body.innerHTML = `<tr><td colspan="4" class="empty">No recommendations generated yet.</td></tr>`;
    return;
  }
  body.innerHTML = rows
    .map(
      (row) => `
      <tr>
        <td>
          <div>${escapeHtml(row.resource_name)}</div>
          <div class="muted small">${escapeHtml(sentenceCase(row.recommendation_type))}</div>
        </td>
        <td class="muted">${escapeHtml(row.project_name)}</td>
        <td class="num">${escapeHtml(money(row.estimated_monthly_saving))}</td>
        <td>${badge(row.status)}</td>
      </tr>`
    )
    .join("");
}

function renderAlerts(rows) {
  const list = document.getElementById("budget-alerts");
  if (!rows.length) {
    list.innerHTML = `<div class="empty"><strong>All clear</strong>No unacknowledged budget alerts.</div>`;
    return;
  }
  list.innerHTML = rows
    .map(
      (row) => `
      <div class="list-item">
        <div>
          <div class="title">${escapeHtml(row.project_name || row.team_name || "Budget")}</div>
          <div class="sub">${escapeHtml(monthLabel(row.budget_month))}, ${escapeHtml(money(row.current_spend))} of ${escapeHtml(money(row.budget_amount))}</div>
        </div>
        <div style="text-align:right">
          ${badge(row.utilization_percentage >= 100 ? "over_budget" : "warning")}
          <div class="small muted" style="margin-top:5px">${escapeHtml(num(row.utilization_percentage, 1))}%</div>
        </div>
      </div>`
    )
    .join("");
}

function detectionCard(title, subtitle, rows, renderRow, link) {
  const content = rows.length
    ? rows
        .slice(0, 4)
        .map((row) => `<div class="list-item">${renderRow(row)}</div>`)
        .join("")
    : `<div class="empty"><strong>Nothing detected</strong>${escapeHtml(subtitle)}</div>`;
  return `
    <div class="card">
      <div class="card-pad section-head" style="margin-bottom:0">
        <div>
          <h3>${escapeHtml(title)}</h3>
          <p>${escapeHtml(subtitle)}</p>
        </div>
        ${link ? `<a class="btn btn-ghost btn-sm" href="${link}">View</a>` : ""}
      </div>
      <div class="list">${content}</div>
    </div>`;
}

function renderDetections(idle, orphaned, missing) {
  const container = document.getElementById("detections");
  const idleHtml = detectionCard(
    `Idle compute (${idle.length})`,
    "CPU below threshold for 21 days in non-production.",
    idle,
    (row) => `
      <div>
        <div class="title">${escapeHtml(row.resource_name)}</div>
        <div class="sub">${escapeHtml(row.project_name || "Unassigned")}, avg ${escapeHtml(num(row.average_cpu_percent, 1))}%</div>
      </div>
      <div class="num">${escapeHtml(money(row.estimated_monthly_waste))}</div>`,
    "/pages/resources.html"
  );
  const orphanHtml = detectionCard(
    `Orphaned volumes (${orphaned.length})`,
    "Storage with no active parent attachment.",
    orphaned,
    (row) => `
      <div>
        <div class="title">${escapeHtml(row.resource_name)}</div>
        <div class="sub">${escapeHtml(row.project_name || "Unassigned")}, ${escapeHtml(row.region_code)}</div>
      </div>
      <div class="num">${escapeHtml(money(row.estimated_monthly_waste))}</div>`,
    "/pages/resources.html"
  );
  const tagHtml = detectionCard(
    `Missing tags (${missing.length})`,
    "Active resources missing owner, project or environment.",
    missing,
    (row) => `
      <div>
        <div class="title">${escapeHtml(row.resource_name)}</div>
        <div class="sub">Missing: ${escapeHtml((row.missing_keys || []).join(", "))}</div>
      </div>
      <div>${badge("warning")}</div>`,
    "/pages/resources.html"
  );
  container.innerHTML = idleHtml + orphanHtml + tagHtml;
}

document.addEventListener("DOMContentLoaded", loadDashboard);

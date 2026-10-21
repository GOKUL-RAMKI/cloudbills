const budgetState = { lookups: null, rows: [], month: "", editing: null };

const $ = (id) => document.getElementById(id);

async function initBudgets() {
  try {
    budgetState.lookups = await api("/lookups");
    fillSelect(
      $("budget-project"),
      budgetState.lookups.projects,
      "project_id",
      (project) => `${project.project_name} (${project.environment_code})`
    );
    fillSelect(
      $("budget-team"),
      budgetState.lookups.teams,
      "team_id",
      (team) => `${team.team_name} (${team.cost_center})`
    );
    $("budget-month-filter").value = currentMonth();
    budgetState.month = currentMonth();
    bindBudgetEvents();
    await Promise.all([loadBudgets(), loadAlerts()]);
  } catch (error) {
    toast(`Could not initialise budgets: ${error.message}`, "error");
  }
}

function bindBudgetEvents() {
  $("refresh-budgets").addEventListener("click", () => Promise.all([loadBudgets(), loadAlerts()]));
  $("add-budget").addEventListener("click", () => openBudgetModal(null));
  $("budget-form").addEventListener("submit", saveBudget);
  $("budget-scope").addEventListener("change", toggleScopeFields);
  $("apply-budget-filter").addEventListener("click", () => {
    budgetState.month = $("budget-month-filter").value;
    loadBudgets();
  });
  $("clear-budget-filter").addEventListener("click", () => {
    budgetState.month = "";
    $("budget-month-filter").value = "";
    loadBudgets();
  });
  $("budgets-body").addEventListener("click", (event) => {
    const button = event.target.closest("button[data-action]");
    if (!button) return;
    const id = Number(button.dataset.id);
    if (button.dataset.action === "edit") openBudgetModal(id);
    if (button.dataset.action === "delete") deleteBudget(id);
    if (button.dataset.action === "check") recheckBudget(id);
  });
  $("alerts-body").addEventListener("click", (event) => {
    const button = event.target.closest("button[data-alert]");
    if (button) acknowledgeAlert(Number(button.dataset.alert));
  });
}

function toggleScopeFields() {
  const scope = $("budget-scope").value;
  $("budget-project-field").style.display = scope === "project" ? "" : "none";
  $("budget-team-field").style.display = scope === "team" ? "" : "none";
}

async function loadBudgets() {
  const path = budgetState.month ? `/budgets?month=${budgetState.month}` : "/budgets";
  try {
    const data = await api(path);
    budgetState.rows = data.items;
    renderBudgets(data.items);
  } catch (error) {
    toast(`Could not load budgets: ${error.message}`, "error");
  }
}

function renderBudgets(rows) {
  const body = $("budgets-body");
  $("budget-count").textContent = `${num(rows.length)} budget(s)`;
  if (!rows.length) {
    body.innerHTML = `<tr><td colspan="7" class="empty"><strong>No budgets</strong>Add a project or team budget for this month.</td></tr>`;
    return;
  }
  body.innerHTML = rows
    .map((row) => {
      const scope = row.project_name
        ? `<div>${escapeHtml(row.project_name)}</div><div class="muted small">Project</div>`
        : `<div>${escapeHtml(row.team_name)}</div><div class="muted small">Team</div>`;
      const used = Math.min(100, Math.max(0, Number(row.utilization_percentage || 0)));
      return `
        <tr>
          <td>${scope}</td>
          <td class="nowrap">${escapeHtml(monthLabel(row.budget_month))}</td>
          <td class="num">${escapeHtml(money(row.budget_amount))}</td>
          <td class="num">${escapeHtml(money(row.current_spend))}</td>
          <td>
            <div class="progress ${escapeHtml(row.budget_status)}" title="${escapeHtml(num(row.utilization_percentage, 1))}%">
              <span style="width:${used}%"></span>
            </div>
            <div class="muted small" style="margin-top:4px">${escapeHtml(num(row.utilization_percentage, 1))}% &middot; warning at ${escapeHtml(num(row.warning_percentage, 0))}%</div>
          </td>
          <td>${badge(row.budget_status)}</td>
          <td>
            <div class="actions">
              <button class="btn btn-sm" data-action="check" data-id="${row.budget_id}">Recheck</button>
              <button class="btn btn-sm" data-action="edit" data-id="${row.budget_id}">Edit</button>
              <button class="btn btn-sm btn-danger" data-action="delete" data-id="${row.budget_id}">Delete</button>
            </div>
          </td>
        </tr>`;
    })
    .join("");
}

function openBudgetModal(id) {
  const form = $("budget-form");
  form.reset();
  budgetState.editing = id;
  $("budget-id").value = id || "";
  $("budget-scope").disabled = Boolean(id);
  $("budget-project").disabled = Boolean(id);
  $("budget-team").disabled = Boolean(id);
  $("budget-month").disabled = Boolean(id);
  $("budget-warning").value = "80";

  if (id) {
    const row = budgetState.rows.find((item) => item.budget_id === id);
    if (!row) return;
    $("budget-modal-title").textContent = "Edit budget";
    $("budget-scope").value = row.project_id ? "project" : "team";
    if (row.project_id) $("budget-project").value = String(row.project_id);
    if (row.team_id) $("budget-team").value = String(row.team_id);
    $("budget-month").value = String(row.budget_month).slice(0, 7);
    $("budget-amount").value = row.budget_amount;
    $("budget-warning").value = row.warning_percentage;
    $("budget-save").textContent = "Save changes";
  } else {
    $("budget-modal-title").textContent = "Add budget";
    $("budget-month").value = currentMonth();
    $("budget-amount").value = "";
    $("budget-save").textContent = "Create budget";
  }
  toggleScopeFields();
  openModal("budget-modal");
}

async function saveBudget(event) {
  event.preventDefault();
  const id = $("budget-id").value;
  const amount = Number($("budget-amount").value);
  const warning = Number($("budget-warning").value);
  if (!amount || amount <= 0) {
    toast("Budget amount must be greater than zero", "error");
    return;
  }
  const button = $("budget-save");
  button.disabled = true;
  try {
    if (id) {
      await api(`/budgets/${id}`, {
        method: "PUT",
        body: { budget_amount: amount, warning_percentage: warning, user_id: 1 },
      });
      toast("Budget updated");
    } else {
      const scope = $("budget-scope").value;
      const body = {
        budget_month: `${$("budget-month").value}-01`,
        budget_amount: amount,
        warning_percentage: warning,
        user_id: 1,
      };
      if (scope === "project") body.project_id = Number($("budget-project").value);
      else body.team_id = Number($("budget-team").value);
      await api("/budgets", { method: "POST", body });
      toast("Budget created");
    }
    closeModal("budget-modal");
    await Promise.all([loadBudgets(), loadAlerts()]);
  } catch (error) {
    toast(error.message, "error");
  } finally {
    button.disabled = false;
  }
}

async function deleteBudget(id) {
  const row = budgetState.rows.find((item) => item.budget_id === id);
  const label = row ? row.project_name || row.team_name : `budget ${id}`;
  if (!window.confirm(`Delete the budget for ${label}?`)) return;
  try {
    await api(`/budgets/${id}`, { method: "DELETE", body: { user_id: 1 } });
    toast("Budget deleted");
    await Promise.all([loadBudgets(), loadAlerts()]);
  } catch (error) {
    toast(error.message, "error");
  }
}

async function recheckBudget(id) {
  try {
    await api(`/budgets/${id}/check`, { method: "POST", body: { user_id: 1 } });
    toast("Budget re-evaluated");
    await Promise.all([loadBudgets(), loadAlerts()]);
  } catch (error) {
    toast(error.message, "error");
  }
}

async function loadAlerts() {
  try {
    const data = await api("/budgets/alerts");
    const body = $("alerts-body");
    if (!data.items.length) {
      body.innerHTML = `<tr><td colspan="8" class="empty">No budget alerts raised.</td></tr>`;
      return;
    }
    body.innerHTML = data.items
      .map(
        (row) => `
        <tr>
          <td>${escapeHtml(row.project_name || row.team_name || "Budget")}</td>
          <td class="nowrap">${escapeHtml(monthLabel(row.budget_month))}</td>
          <td class="num">${escapeHtml(money(row.current_spend))}</td>
          <td class="num">${escapeHtml(money(row.budget_amount))}</td>
          <td class="num">${escapeHtml(num(row.utilization_percentage, 1))}%</td>
          <td class="small muted nowrap">${escapeHtml(formatDate(row.triggered_at, true))}</td>
          <td>${badge(row.is_acknowledged ? "closed" : "warning")}</td>
          <td>
            ${
              row.is_acknowledged
                ? '<span class="muted small">Acknowledged</span>'
                : `<button class="btn btn-sm" data-alert="${row.alert_id}">Acknowledge</button>`
            }
          </td>
        </tr>`
      )
      .join("");
  } catch (error) {
    toast(`Could not load alerts: ${error.message}`, "error");
  }
}

async function acknowledgeAlert(alertId) {
  try {
    await api(`/budgets/alerts/${alertId}/ack`, { method: "POST", body: { user_id: 1 } });
    toast("Alert acknowledged");
    await loadAlerts();
  } catch (error) {
    toast(error.message, "error");
  }
}

document.addEventListener("DOMContentLoaded", initBudgets);

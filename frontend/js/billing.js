const billingState = { lookups: null, month: "", projectId: "" };

const $ = (id) => document.getElementById(id);

async function initBilling() {
  try {
    billingState.lookups = await api("/lookups");
    fillSelect(
      $("billing-project"),
      billingState.lookups.projects,
      "project_id",
      (project) => `${project.project_name} (${project.environment_code})`,
      "All projects"
    );
    fillSelect(
      $("upload-account"),
      billingState.lookups.accounts,
      "account_id",
      (account) => `${account.provider_name} / ${account.account_name}`,
      "Infer account from first row"
    );
    $("billing-month").value = currentMonth();
    billingState.month = currentMonth();
    bindBillingEvents();
    await refreshBilling();
  } catch (error) {
    toast(`Could not initialise billing: ${error.message}`, "error");
  }
}

function bindBillingEvents() {
  $("refresh-billing").addEventListener("click", refreshBilling);
  $("apply-billing-filter").addEventListener("click", applyFilters);
  $("clear-billing-filter").addEventListener("click", () => {
    $("billing-project").value = "";
    $("billing-month").value = "";
    applyFilters();
  });
  $("upload-form").addEventListener("submit", uploadBatch);
  $("batches-body").addEventListener("click", (event) => {
    const button = event.target.closest("button[data-batch]");
    if (button) showBatchErrors(Number(button.dataset.batch));
  });
}

function applyFilters() {
  billingState.projectId = $("billing-project").value;
  billingState.month = $("billing-month").value;
  refreshBilling();
}

async function refreshBilling() {
  await Promise.all([loadBilling(), loadMonthly(), loadShared(), loadBatches()]);
}

async function loadBilling() {
  const params = new URLSearchParams();
  if (billingState.projectId) params.set("project_id", billingState.projectId);
  if (billingState.month) params.set("month", billingState.month);
  params.set("limit", "400");
  try {
    const data = await api(`/billing?${params.toString()}`);
    $("billing-total").textContent = money(data.total_cost);
    const body = $("billing-body");
    if (!data.items.length) {
      body.innerHTML = `<tr><td colspan="8" class="empty"><strong>No billing facts</strong>Upload a CSV or adjust the filters.</td></tr>`;
      return;
    }
    body.innerHTML = data.items
      .map(
        (row) => `
        <tr>
          <td class="nowrap">${escapeHtml(formatDate(row.usage_start, true))}</td>
          <td>${escapeHtml(row.resource_name || "Unattributed")}<div class="muted small">${escapeHtml(row.resource_id || "-")}</div></td>
          <td>${escapeHtml(row.project_name || "Shared / unassigned")}</td>
          <td>${escapeHtml(row.service_name)}</td>
          <td class="num">${escapeHtml(num(row.usage_quantity, 2))}</td>
          <td class="num">${escapeHtml(money(row.billed_cost))}</td>
          <td>${escapeHtml(row.provider_name)}<div class="muted small">${escapeHtml(row.account_name)}</div></td>
          <td class="mono muted">${row.source_batch_id}</td>
        </tr>`
      )
      .join("");
  } catch (error) {
    toast(`Could not load billing: ${error.message}`, "error");
  }
}

async function loadMonthly() {
  const params = new URLSearchParams();
  if (billingState.month) params.set("month", billingState.month);
  try {
    const data = await api(`/billing/monthly?${params.toString()}`);
    const body = $("monthly-body");
    if (!data.items.length) {
      body.innerHTML = `<tr><td colspan="5" class="empty">No rollup rows for this period.</td></tr>`;
      return;
    }
    body.innerHTML = data.items
      .map(
        (row) => `
        <tr>
          <td class="nowrap">${escapeHtml(monthLabel(row.billing_month))}</td>
          <td>${escapeHtml(row.project_name)}</td>
          <td class="muted">${escapeHtml(row.team_name)}</td>
          <td class="num">${row.distinct_resources_billed}</td>
          <td class="num">${escapeHtml(money(row.total_aggregate_cost))}</td>
        </tr>`
      )
      .join("");
  } catch (error) {
    toast(`Could not load monthly rollup: ${error.message}`, "error");
  }
}

async function loadShared() {
  const month = billingState.month || currentMonth();
  try {
    const data = await api(`/billing/shared-allocation?month=${month}`);
    const body = $("shared-body");
    if (!data.items.length) {
      body.innerHTML = `<tr><td colspan="5" class="empty">No shared infrastructure spend for this period.</td></tr>`;
      return;
    }
    body.innerHTML = data.items
      .map(
        (row) => `
        <tr>
          <td>${escapeHtml(row.shared_infra_name)}</td>
          <td>${escapeHtml(row.consumer_project)}</td>
          <td class="muted">${escapeHtml(row.responsible_team)}</td>
          <td class="num">${escapeHtml(num(row.allocation_percentage, 1))}%</td>
          <td class="num">${escapeHtml(money(row.allocated_amount))}</td>
        </tr>`
      )
      .join("");
  } catch (error) {
    toast(`Could not load shared allocation: ${error.message}`, "error");
  }
}

async function loadBatches() {
  try {
    const data = await api("/batches");
    const body = $("batches-body");
    if (!data.items.length) {
      body.innerHTML = `<tr><td colspan="9" class="empty">No import batches yet.</td></tr>`;
      return;
    }
    body.innerHTML = data.items
      .map(
        (row) => `
        <tr>
          <td class="mono muted">${row.batch_id}</td>
          <td>${escapeHtml(row.file_name)}</td>
          <td>${escapeHtml(row.provider_name)}<div class="muted small">${escapeHtml(row.account_name)}</div></td>
          <td>${badge(row.status)}</td>
          <td class="num">${row.row_count}</td>
          <td class="num">${row.accepted_count}</td>
          <td class="num" style="color:${row.rejected_count > 0 ? "var(--red)" : "inherit"}">${row.rejected_count}</td>
          <td class="small muted nowrap">${escapeHtml(formatDate(row.imported_at, true))}</td>
          <td><button class="btn btn-sm" data-batch="${row.batch_id}">View</button></td>
        </tr>`
      )
      .join("");
  } catch (error) {
    toast(`Could not load batches: ${error.message}`, "error");
  }
}

async function uploadBatch(event) {
  event.preventDefault();
  const fileInput = $("upload-file");
  if (!fileInput.files.length) {
    toast("Choose a CSV file first", "error");
    return;
  }
  const form = new FormData();
  form.append("file", fileInput.files[0]);
  const accountId = $("upload-account").value;
  if (accountId) form.append("account_id", accountId);
  form.append("user_id", "1");

  const button = $("upload-submit");
  const result = $("upload-result");
  button.disabled = true;
  result.innerHTML = `<div class="notice">Uploading and validating ...</div>`;
  try {
    const data = await api("/batches/upload", { method: "POST", body: form });
    const rejected = data.batch.rejected_count;
    const errors = data.errors || [];
    result.innerHTML = `
      <div class="notice ${rejected > 0 ? "" : "success"}">
        ${escapeHtml(data.message)}
        ${errors.length ? `<div class="small" style="margin-top:6px">First error: ${escapeHtml(errors[0].error_message)}</div>` : ""}
      </div>`;
    toast(rejected > 0 ? "Batch processed with quarantined rows" : "Batch validated and loaded");
    fileInput.value = "";
    await Promise.all([loadBilling(), loadMonthly(), loadShared(), loadBatches()]);
  } catch (error) {
    result.innerHTML = `<div class="notice error">${escapeHtml(error.message)}</div>`;
    toast(error.message, "error");
  } finally {
    button.disabled = false;
  }
}

async function showBatchErrors(batchId) {
  const body = $("errors-body");
  body.innerHTML = `<div class="empty"><span class="spinner"></span></div>`;
  openModal("errors-modal");
  try {
    const data = await api(`/batches/${batchId}/errors`);
    if (!data.items.length) {
      body.innerHTML = `<div class="empty"><strong>No rejected rows</strong>This batch passed validation.</div>`;
      return;
    }
    body.innerHTML = `
      <div class="table-wrap">
        <table>
          <thead><tr><th>Row</th><th>Code</th><th>Message</th><th>Payload</th></tr></thead>
          <tbody>
            ${data.items
              .map(
                (item) => `<tr>
                  <td class="mono">${escapeHtml(item.raw_row_index)}</td>
                  <td class="mono">${escapeHtml(item.error_code || "-")}</td>
                  <td>${escapeHtml(item.error_message)}</td>
                  <td class="mono small" style="max-width:360px;white-space:normal">${escapeHtml(JSON.stringify(item.raw_payload))}</td>
                </tr>`
              )
              .join("")}
          </tbody>
        </table>
      </div>`;
  } catch (error) {
    body.innerHTML = `<div class="notice error">${escapeHtml(error.message)}</div>`;
  }
}

document.addEventListener("DOMContentLoaded", initBilling);

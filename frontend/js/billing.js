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
  $("preview-upload").addEventListener("click", previewBatch);
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
      body.innerHTML = `<tr><td colspan="9" class="empty"><strong>No billing facts</strong>Upload a CSV or adjust the filters.</td></tr>`;
      return;
    }
    body.innerHTML = data.items
      .map(
        (row) => `
        <tr>
          <td class="nowrap">${escapeHtml(formatDate(row.usage_start, true))}</td>
          <td>${escapeHtml(row.resource_name || "Unattributed")}<div class="muted small">${escapeHtml(row.resource_id || "-")}</div></td>
          <td>${escapeHtml(row.project_name || "Shared / unassigned")}</td>
          <td>${escapeHtml(row.service_name)}<div class="muted small">${escapeHtml(sentenceCase(row.charge_category))}</div></td>
          <td class="num">${escapeHtml(num(row.usage_quantity, 2))}</td>
          <td class="num">${escapeHtml(money(row.billed_cost))}</td>
          <td class="num">${escapeHtml(money(row.effective_cost))}</td>
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
      body.innerHTML = `<tr><td colspan="10" class="empty">No import batches yet.</td></tr>`;
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
          <td class="num" style="color:${row.rejected_count > 0 ? "var(--accent)" : "inherit"}">${row.rejected_count}</td>
          <td class="num">${row.duplicate_count}</td>
          <td class="small muted nowrap">${escapeHtml(formatDate(row.imported_at, true))}</td>
          <td><button class="btn btn-sm" data-batch="${row.batch_id}">View</button></td>
        </tr>`
      )
      .join("");
  } catch (error) {
    toast(`Could not load batches: ${error.message}`, "error");
  }
}

async function previewBatch() {
  const fileInput = $("upload-file");
  if (!fileInput.files.length) {
    toast("Choose a CSV file first", "error");
    return;
  }
  const form = new FormData();
  form.append("file", fileInput.files[0]);

  const button = $("preview-upload");
  const target = $("upload-preview");
  button.disabled = true;
  target.innerHTML = `<div class="notice">Reading headers and checking accounts…</div>`;
  try {
    const data = await api("/batches/preview", { method: "POST", body: form });
    target.innerHTML = renderPreview(data);
  } catch (error) {
    target.innerHTML = `<div class="notice error">${escapeHtml(error.message)}</div>`;
    toast(error.message, "error");
  } finally {
    button.disabled = false;
  }
}

function renderPreview(data) {
  const accounts = data.accounts.length
    ? data.accounts
        .map(
          (account) => `<tr>
            <td>${escapeHtml(account.provider)}</td>
            <td class="mono">${escapeHtml(account.billing_account_id)}</td>
            <td class="mono">${escapeHtml(account.sub_account_id || "-")}</td>
            <td>${account.resolved ? "Registered" : "Not found"}</td>
          </tr>`
        )
        .join("")
    : `<tr><td colspan="4" class="empty">No provider/account values found.</td></tr>`;

  const samples = data.sample_rows
    .map((row) => {
      const values = row.values || {};
      return `<tr>
        <td class="mono">${row.row_index}</td>
        <td>${escapeHtml(values.ServiceProviderName || "-")}</td>
        <td class="mono">${escapeHtml(values.BillingAccountId || "-")}</td>
        <td class="nowrap">${escapeHtml(formatDate(values.ChargePeriodStart))}</td>
        <td>${escapeHtml(values.ServiceName || "-")}</td>
        <td class="num">${escapeHtml(money(values.BilledCost))}</td>
        <td>${row.error ? escapeHtml(row.error) : "-"}</td>
      </tr>`;
    })
    .join("");

  const warnings = data.warnings.length
    ? `<div class="notice" style="margin-top:10px">${data.warnings.map(escapeHtml).join("<br>")}</div>`
    : "";
  const unknown = data.unknown_columns.length
    ? `<details style="margin-top:10px"><summary>${data.unknown_columns.length} unknown column(s) preserved in focus_payload.extra</summary><div class="small muted" style="margin-top:6px">${data.unknown_columns.map(escapeHtml).join(", ")}</div></details>`
    : "";

  return `
    <div class="notice success">
      ${data.row_count} row(s), ${data.valid_row_count} structurally valid,
      ${data.parse_error_count} row-width error(s). Total BilledCost ${escapeHtml(money(data.total_billed_cost))}
      in ${escapeHtml(data.currencies.join(", ") || "no currency")}.
    </div>
    ${warnings}
    ${unknown}
    <div class="table-wrap" style="margin-top:12px">
      <table>
        <thead><tr><th>Provider</th><th>Billing account</th><th>Sub-account</th><th>Status</th></tr></thead>
        <tbody>${accounts}</tbody>
      </table>
    </div>
    <div class="table-wrap" style="margin-top:12px">
      <table>
        <thead><tr><th>Row</th><th>Provider</th><th>Billing account</th><th>Period start</th><th>Service</th><th class="num">Billed</th><th>Issue</th></tr></thead>
        <tbody>${samples}</tbody>
      </table>
    </div>`;
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

  const button = $("upload-submit");
  const result = $("upload-result");
  button.disabled = true;
  result.innerHTML = `<div class="notice">Uploading and loading FOCUS rows…</div>`;
  try {
    const data = await api("/batches/upload", { method: "POST", body: form });
    const rejected = data.batch.rejected_count;
    const duplicate = data.batch.duplicate_count;
    const errors = data.errors || [];
    result.innerHTML = `
      <div class="notice ${rejected > 0 ? "" : "success"}">
        ${escapeHtml(data.message)}
        ${errors.length ? `<div class="small" style="margin-top:6px">First error: ${escapeHtml(errors[0].error_message)}</div>` : ""}
      </div>`;
    toast(
      rejected > 0
        ? "Batch processed with quarantined rows"
        : duplicate > 0
        ? "Batch loaded; duplicate rows were skipped"
        : "Batch validated and loaded"
    );
    fileInput.value = "";
    $("upload-preview").innerHTML = "";
    await Promise.all([loadBilling(), loadMonthly(), loadShared(), loadBatches()]);
  } catch (error) {
    const batch = error.data && error.data.batch;
    result.innerHTML = `
      <div class="notice error">${escapeHtml(error.message)}
        ${batch ? `<div class="small" style="margin-top:6px">Batch ${batch.batch_id} is ${escapeHtml(batch.status)}.</div>` : ""}
      </div>`;
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

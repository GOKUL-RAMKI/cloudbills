const recState = { rows: [], status: "" };

const $ = (id) => document.getElementById(id);

const ACTION_DEFAULTS = {
  idle_compute: "stop_instance",
  rightsizing: "resize_instance",
  unattached_volume: "delete_volume",
  unattached_elastic_ip: "release_ip",
  missing_tag: "stop_instance",
  budget_overrun: "stop_instance",
};

async function initRecommendations() {
  bindRecommendationEvents();
  await loadRecommendations();
}

function bindRecommendationEvents() {
  $("refresh-recommendations").addEventListener("click", loadRecommendations);
  $("status-tabs").addEventListener("click", (event) => {
    const tab = event.target.closest(".tab");
    if (!tab) return;
    document.querySelectorAll("#status-tabs .tab").forEach((item) => item.classList.remove("active"));
    tab.classList.add("active");
    recState.status = tab.dataset.status;
    loadRecommendations();
  });
  $("recommendations-body").addEventListener("click", handleRecommendationAction);
  $("decision-form").addEventListener("submit", submitDecision);
  $("execute-form").addEventListener("submit", submitExecute);
  $("savings-form").addEventListener("submit", submitSavings);
}

async function loadRecommendations() {
  const params = new URLSearchParams();
  if (recState.status) params.set("status", recState.status);
  params.set("limit", "300");
  try {
    const data = await api(`/recommendations?${params.toString()}`);
    recState.rows = data.items;
    renderRecommendations(data);
  } catch (error) {
    toast(`Could not load queue: ${error.message}`, "error");
  }
}

function renderRecommendations(data) {
  const body = $("recommendations-body");
  $("recommendation-count").textContent = `${num(data.total)} recommendation(s)`;
  if (!data.items.length) {
    body.innerHTML = `<tr><td colspan="9" class="empty"><strong>Queue is empty</strong>No recommendations match this filter.</td></tr>`;
    return;
  }
  body.innerHTML = data.items
    .map((row) => {
      const actions = [`<button class="btn btn-sm" data-action="view" data-id="${row.recommendation_id}">View</button>`];
      if (row.status === "detected") {
        actions.push(`<button class="btn btn-sm btn-primary" data-action="request" data-id="${row.recommendation_id}">Submit for review</button>`);
      }
      if (row.status === "under_review") {
        actions.push(`<button class="btn btn-sm btn-primary" data-action="approve" data-id="${row.recommendation_id}">Approve</button>`);
        actions.push(`<button class="btn btn-sm btn-danger" data-action="reject" data-id="${row.recommendation_id}">Reject</button>`);
      }
      if (row.status === "approved") {
        actions.push(`<button class="btn btn-sm btn-primary" data-action="execute" data-id="${row.recommendation_id}">Execute</button>`);
      }
      if (row.status === "executed") {
        actions.push(`<button class="btn btn-sm btn-primary" data-action="savings" data-id="${row.recommendation_id}">Record savings</button>`);
      }
      return `
        <tr>
          <td class="mono muted">${row.recommendation_id}</td>
          <td>
            <div>${escapeHtml(row.resource_name)}</div>
            <div class="muted small">${escapeHtml(row.rule_name)}</div>
          </td>
          <td>${escapeHtml(row.project_name)}<div class="muted small">${escapeHtml(row.team_name)}</div></td>
          <td>${escapeHtml(sentenceCase(row.recommendation_type))}</td>
          <td class="num">${escapeHtml(money(row.estimated_monthly_saving))}</td>
          <td>${badge(row.risk_level || "unknown")}</td>
          <td>${badge(row.status)}</td>
          <td class="muted small nowrap">${escapeHtml(formatDate(row.detected_at))}</td>
          <td><div class="actions">${actions.join("")}</div></td>
        </tr>`;
    })
    .join("");
}

function handleRecommendationAction(event) {
  const button = event.target.closest("button[data-action]");
  if (!button) return;
  const id = Number(button.dataset.id);
  const action = button.dataset.action;
  if (action === "view") openDetail(id);
  if (action === "request") openDecision(id, "request");
  if (action === "approve") openDecision(id, "approve");
  if (action === "reject") openDecision(id, "reject");
  if (action === "execute") openExecute(id);
  if (action === "savings") openSavings(id);
}

function openDecision(id, kind) {
  const labels = {
    request: {
      title: "Submit for review",
      notice: "This creates one pending approval request. A partial unique index prevents duplicate pending requests.",
      submit: "Submit for review",
    },
    approve: {
      title: "Approve recommendation",
      notice: "The procedure locks the recommendation row with FOR UPDATE, validates the pending request and writes status history plus an audit entry.",
      submit: "Approve",
    },
    reject: {
      title: "Reject recommendation",
      notice: "The pending request will be resolved as rejected and the recommendation moves to the rejected state.",
      submit: "Reject",
    },
  };
  const config = labels[kind];
  $("decision-rec-id").value = id;
  $("decision-kind").value = kind;
  $("decision-title").textContent = config.title;
  $("decision-notice").textContent = config.notice;
  $("decision-comments").value = "";
  $("decision-submit").textContent = config.submit;
  $("decision-submit").className = `btn ${kind === "reject" ? "btn-danger" : "btn-primary"}`;
  openModal("decision-modal");
}

async function submitDecision(event) {
  event.preventDefault();
  const id = $("decision-rec-id").value;
  const kind = $("decision-kind").value;
  const comments = $("decision-comments").value.trim();
  const path = kind === "request" ? "request-review" : kind;
  const button = $("decision-submit");
  button.disabled = true;
  try {
    await api(`/recommendations/${id}/${path}`, {
      method: "POST",
      body: { user_id: 1, reviewer_user_id: 1, comments: comments || null },
    });
    toast(
      kind === "request"
        ? "Recommendation submitted for review"
        : kind === "approve"
        ? "Recommendation approved"
        : "Recommendation rejected"
    );
    closeModal("decision-modal");
    await loadRecommendations();
  } catch (error) {
    toast(error.message, "error");
  } finally {
    button.disabled = false;
  }
}

function openExecute(id) {
  const row = recState.rows.find((item) => item.recommendation_id === id);
  $("execute-rec-id").value = id;
  $("execute-action").value = ACTION_DEFAULTS[row ? row.recommendation_type : ""] || "stop_instance";
  openModal("execute-modal");
}

async function submitExecute(event) {
  event.preventDefault();
  const id = $("execute-rec-id").value;
  const actionType = $("execute-action").value;
  const button = $("execute-submit");
  button.disabled = true;
  try {
    await api(`/recommendations/${id}/execute`, {
      method: "POST",
      body: { user_id: 1, action_type: actionType },
    });
    toast("Remediation executed");
    closeModal("execute-modal");
    await loadRecommendations();
  } catch (error) {
    toast(error.message, "error");
  } finally {
    button.disabled = false;
  }
}

function localIsoDate(date) {
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;
}

function openSavings(id) {
  const row = recState.rows.find((item) => item.recommendation_id === id);
  const now = new Date();
  const start = new Date(now.getFullYear(), now.getMonth(), 1);
  const end = now.getDate() > 1 ? now : new Date(now.getFullYear(), now.getMonth(), 2);
  $("savings-rec-id").value = id;
  $("savings-baseline").value = Number(row ? row.estimated_monthly_saving : 0).toFixed(2);
  $("savings-actual").value = "0.00";
  $("savings-start").value = localIsoDate(start);
  $("savings-end").value = localIsoDate(end);
  openModal("savings-modal");
}

async function submitSavings(event) {
  event.preventDefault();
  const id = $("savings-rec-id").value;
  const button = $("savings-submit");
  button.disabled = true;
  try {
    await api(`/recommendations/${id}/savings`, {
      method: "POST",
      body: {
        baseline_monthly_cost: Number($("savings-baseline").value),
        actual_monthly_cost: Number($("savings-actual").value),
        measurement_start: $("savings-start").value,
        measurement_end: $("savings-end").value,
        user_id: 1,
      },
    });
    toast("Savings measurement recorded");
    closeModal("savings-modal");
    await loadRecommendations();
  } catch (error) {
    toast(error.message, "error");
  } finally {
    button.disabled = false;
  }
}

async function openDetail(id) {
  const body = $("detail-body");
  body.innerHTML = `<div class="empty"><span class="spinner"></span></div>`;
  openModal("detail-modal");
  try {
    const data = await api(`/recommendations/${id}`);
    const rec = data.recommendation;
    const evidence = data.evidence.length
      ? data.evidence
          .map((item) => {
            const measurement = [
              item.observed_value !== null ? `Observed ${num(item.observed_value, 2)}` : "",
              item.threshold_value !== null ? `Threshold ${num(item.threshold_value, 2)}` : "",
            ]
              .filter(Boolean)
              .join("; ");
            return `<div class="list-item">
              <div><div class="title">${escapeHtml(item.metric_name || "Operational evidence")}</div>
              <div class="sub">${escapeHtml(item.narrative || "")}</div></div>
              ${measurement ? `<div class="small num evidence-value">${escapeHtml(measurement)}</div>` : ""}
            </div>`;
          })
          .join("")
      : `<div class="empty">No evidence rows.</div>`;
    const approvals = data.approvals.length
      ? data.approvals
          .map(
            (item) => `<div class="list-item">
              <div><div class="title">${escapeHtml(item.decision || "pending")}</div>
              <div class="sub">Requested by ${escapeHtml(item.requested_by_name || "-")}.${item.reviewed_by_name ? ` Reviewed by ${escapeHtml(item.reviewed_by_name)}.` : ""}</div></div>
              <div class="muted small">${escapeHtml(formatDate(item.requested_at, true))}</div>
            </div>`
          )
          .join("")
      : `<div class="empty">No approval requests.</div>`;
    const history = data.status_history.length
      ? data.status_history
          .map(
            (item) => `<div class="list-item">
              <div><div class="title">${escapeHtml(item.old_status || "new")} to ${escapeHtml(item.new_status)}</div>
              <div class="sub">${escapeHtml(item.reason || "No reason recorded")}, ${escapeHtml(item.changed_by_name || "system")}</div></div>
              <div class="muted small">${escapeHtml(formatDate(item.changed_at, true))}</div>
            </div>`
          )
          .join("")
      : `<div class="empty">No status transitions.</div>`;
    const actions = data.actions.length
      ? data.actions
          .map(
            (item) => `<div class="list-item">
              <div><div class="title">${escapeHtml(sentenceCase(item.action_type))}</div>
              <div class="sub">${escapeHtml(item.executed_by_name || "system")}, ${escapeHtml(formatDate(item.started_at, true))}</div></div>
              <div>${badge(item.execution_status)}</div>
            </div>`
          )
          .join("")
      : `<div class="empty">No execution actions.</div>`;
    const savings = data.savings
      ? `<dl class="kv">
          <dt>Baseline monthly cost</dt><dd>${escapeHtml(money(data.savings.baseline_monthly_cost))}</dd>
          <dt>Actual monthly cost</dt><dd>${escapeHtml(money(data.savings.actual_monthly_cost))}</dd>
          <dt>Estimated saving</dt><dd>${escapeHtml(money(data.savings.estimated_saving))}</dd>
          <dt>Actual saving</dt><dd>${escapeHtml(money(data.savings.actual_saving))}</dd>
          <dt>Realization</dt><dd>${escapeHtml(num(data.savings.realization_percentage, 1))}%</dd>
          <dt>Measurement window</dt><dd>${escapeHtml(formatDate(data.savings.measurement_start))} to ${escapeHtml(formatDate(data.savings.measurement_end))}</dd>
        </dl>`
      : `<div class="empty">No savings measured yet.</div>`;

    body.innerHTML = `
      <div class="grid grid-2">
        <div>
          <h2>${escapeHtml(rec.resource_name)}</h2>
          <p class="muted">${escapeHtml(rec.problem_description)}</p>
          <dl class="kv" style="margin-top:14px">
            <dt>Project</dt><dd>${escapeHtml(rec.project_name)} / ${escapeHtml(rec.team_name)}</dd>
            <dt>Rule</dt><dd>${escapeHtml(rec.rule_name)} (${escapeHtml(rec.severity || "-")})</dd>
            <dt>Suggested action</dt><dd>${escapeHtml(rec.suggested_action)}</dd>
            <dt>Estimated saving</dt><dd>${escapeHtml(money(rec.estimated_monthly_saving))}</dd>
            <dt>Risk</dt><dd>${badge(rec.risk_level || "unknown")}</dd>
            <dt>Status</dt><dd>${badge(rec.status)}</dd>
            <dt>Detected</dt><dd>${escapeHtml(formatDate(rec.detected_at, true))}</dd>
          </dl>
        </div>
        <div>
          <div class="detail-block" style="border-top:none;margin-top:0;padding-top:0">
            <h4>Evidence</h4>
            <div class="list">${evidence}</div>
          </div>
        </div>
      </div>
      <div class="detail-block"><h4>Approval requests</h4><div class="list">${approvals}</div></div>
      <div class="detail-block"><h4>Status history</h4><div class="list">${history}</div></div>
      <div class="detail-block"><h4>Executed actions</h4><div class="list">${actions}</div></div>
      <div class="detail-block"><h4>Savings realization</h4>${savings}</div>`;
  } catch (error) {
    body.innerHTML = `<div class="notice error">${escapeHtml(error.message)}</div>`;
  }
}

document.addEventListener("DOMContentLoaded", initRecommendations);

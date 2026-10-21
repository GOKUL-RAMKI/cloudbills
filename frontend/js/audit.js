const auditState = { rows: [] };

const $ = (id) => document.getElementById(id);

async function initAudit() {
  $("refresh-audit").addEventListener("click", loadAudit);
  const onSearch = debounce(() => renderAudit(), 180);
  $("audit-search").addEventListener("input", onSearch);
  await loadAudit();
}

async function loadAudit() {
  try {
    const data = await api("/audit");
    auditState.rows = data.items;
    renderAudit();
  } catch (error) {
    toast(`Could not load audit trail: ${error.message}`, "error");
  }
}

function renderAudit() {
  const query = $("audit-search").value.trim().toLowerCase();
  const rows = query
    ? auditState.rows.filter((row) =>
        [row.action_type, row.entity_type, row.entity_id, row.username]
          .filter((value) => value !== null && value !== undefined)
          .some((value) => String(value).toLowerCase().includes(query))
      )
    : auditState.rows;
  $("audit-count").textContent = `${num(rows.length)} of ${num(auditState.rows.length)} entries`;
  const body = $("audit-body");
  if (!rows.length) {
    body.innerHTML = `<tr><td colspan="7" class="empty"><strong>No audit entries</strong>Nothing matches the current filter.</td></tr>`;
    return;
  }
  body.innerHTML = rows
    .map(
      (row) => `
      <tr>
        <td class="small nowrap">${escapeHtml(formatDate(row.action_timestamp, true))}</td>
        <td>${badge(row.action_type)}</td>
        <td>${escapeHtml(row.entity_type)} <span class="mono muted">#${row.entity_id}</span></td>
        <td>${escapeHtml(row.username || "system")}</td>
        <td class="state-cell">${renderState(row.old_state)}</td>
        <td class="state-cell">${renderState(row.new_state)}</td>
        <td class="mono small muted">${escapeHtml(row.client_ip || "-")}</td>
      </tr>`
    )
    .join("");
}

function renderState(value) {
  if (value === null || value === undefined) return '<span class="muted">-</span>';
  const entries = typeof value === "object" ? Object.entries(value) : [];
  if (!entries.length) return '<span class="muted">-</span>';
  return `<div class="state-text">${entries
    .map(([key, item]) => `${escapeHtml(key)}=${escapeHtml(typeof item === "object" ? JSON.stringify(item) : item)}`)
    .join(" &middot; ")}</div>`;
}

document.addEventListener("DOMContentLoaded", initAudit);

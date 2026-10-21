const resourceState = { lookups: null, rows: [] };

const $ = (id) => document.getElementById(id);

async function initResources() {
  try {
    resourceState.lookups = await api("/lookups");
    populateLookups();
    bindResourceEvents();
    await Promise.all([loadResources(), runDetectors()]);
  } catch (error) {
    toast(`Could not initialise inventory: ${error.message}`, "error");
  }
}

function populateLookups() {
  const lookups = resourceState.lookups;
  fillSelect(
    $("filter-project"),
    lookups.projects,
    "project_id",
    (project) => `${project.project_name} (${project.environment_code})`,
    "All projects"
  );
  fillSelect(
    $("resource-account"),
    lookups.accounts,
    "account_id",
    (account) => `${account.provider_name} / ${account.account_name}`,
    "Select account"
  );
  fillSelect(
    $("resource-project"),
    lookups.projects,
    "project_id",
    (project) => `${project.project_name} (${project.environment_code})`,
    "Unassigned / shared"
  );
  refreshTypeRegion();
}

function refreshTypeRegion(selectedType = null, selectedRegion = null) {
  const lookups = resourceState.lookups;
  const accountId = Number($("resource-account").value);
  const account = lookups.accounts.find((item) => item.account_id === accountId);
  const types = account
    ? lookups.resource_types.filter((item) => item.provider_id === account.provider_id)
    : lookups.resource_types;
  const regions = account
    ? lookups.regions.filter((item) => item.provider_id === account.provider_id)
    : lookups.regions;

  fillSelect(
    $("resource-type"),
    types,
    "type_id",
    (type) => `${type.provider_name} / ${type.raw_type_name} (${type.generic_category})`,
    "Select type"
  );
  fillSelect(
    $("resource-region"),
    regions,
    "region_id",
    (region) => `${region.provider_name} / ${region.region_code} - ${region.region_name}`,
    "Select region"
  );
  if (selectedType) $("resource-type").value = String(selectedType);
  if (selectedRegion) $("resource-region").value = String(selectedRegion);
}

function bindResourceEvents() {
  $("refresh-resources").addEventListener("click", loadResources);
  $("add-resource").addEventListener("click", () => openResourceModal(null));
  $("rerun-detectors").addEventListener("click", runDetectors);
  $("resource-account").addEventListener("change", () => refreshTypeRegion());
  $("resource-form").addEventListener("submit", saveResource);
  $("resource-search").addEventListener(
    "input",
    debounce(() => loadResources(), 320)
  );
  ["filter-status", "filter-category", "filter-project"].forEach((id) => {
    $(id).addEventListener("change", loadResources);
  });
  $("resources-body").addEventListener("click", handleRowAction);
}

async function loadResources() {
  const params = new URLSearchParams();
  const search = $("resource-search").value.trim();
  const status = $("filter-status").value;
  const category = $("filter-category").value;
  const project = $("filter-project").value;
  if (search) params.set("search", search);
  if (status) params.set("status", status);
  if (category) params.set("category", category);
  if (project) params.set("project_id", project);
  params.set("limit", "300");

  try {
    const data = await api(`/resources?${params.toString()}`);
    resourceState.rows = data.items;
    renderResources(data);
  } catch (error) {
    toast(`Could not load resources: ${error.message}`, "error");
  }
}

function renderResources(data) {
  const body = $("resources-body");
  $("resource-count").textContent = `${num(data.total)} resource(s)`;
  if (!data.items.length) {
    body.innerHTML = `<tr><td colspan="10" class="empty"><strong>No matching resources</strong>Adjust the filters or add a resource.</td></tr>`;
    return;
  }
  body.innerHTML = data.items
    .map((row) => {
      const tags = Object.entries(row.tags || {});
      const tagHtml = tags.length
        ? tags
            .map(
              ([key, value]) =>
                `<span class="chip ${key === "owner" ? "owner" : ""}">${escapeHtml(key)}=${escapeHtml(value)}</span>`
            )
            .join("")
        : `<span class="chip empty">untagged</span>`;
      return `
        <tr>
          <td class="mono muted">${row.resource_id}</td>
          <td>
            <div>${escapeHtml(row.resource_name)}</div>
            <div class="muted small mono">${escapeHtml(row.external_resource_id)}</div>
          </td>
          <td>
            <div>${escapeHtml(row.provider_name)}</div>
            <div class="muted small">${escapeHtml(row.account_name)}</div>
          </td>
          <td>${row.project_name ? escapeHtml(row.project_name) : '<span class="muted">Unassigned</span>'}</td>
          <td>
            <div>${escapeHtml(row.raw_type_name)}</div>
            <div class="muted small">${escapeHtml(row.generic_category)}</div>
          </td>
          <td class="mono">${escapeHtml(row.region_code)}</td>
          <td>${badge(row.status)}</td>
          <td class="num">${money(row.hourly_cost)}</td>
          <td style="max-width:280px">${tagHtml}</td>
          <td>
            <div class="actions">
              <button class="btn btn-sm" data-action="view" data-id="${row.resource_id}">View</button>
              <button class="btn btn-sm" data-action="edit" data-id="${row.resource_id}">Edit</button>
              <button class="btn btn-sm btn-danger" data-action="delete" data-id="${row.resource_id}">Delete</button>
            </div>
          </td>
        </tr>`;
    })
    .join("");
}

function handleRowAction(event) {
  const button = event.target.closest("button[data-action]");
  if (!button) return;
  const id = Number(button.dataset.id);
  const action = button.dataset.action;
  if (action === "view") showResourceDetail(id);
  if (action === "edit") openResourceModal(id);
  if (action === "delete") deleteResource(id);
}

function openResourceModal(id) {
  const form = $("resource-form");
  form.reset();
  $("resource-id").value = "";
  $("resource-status").value = "active";
  $("resource-cost").value = "0";
  $("resource-tags").value = "";

  if (id) {
    const row = resourceState.rows.find((item) => item.resource_id === id);
    if (!row) return;
    $("resource-id").value = row.resource_id;
    $("resource-name").value = row.resource_name;
    $("resource-external").value = row.external_resource_id;
    $("resource-account").value = String(row.account_id);
    refreshTypeRegion(row.type_id, row.region_id);
    $("resource-project").value = row.project_id ? String(row.project_id) : "";
    $("resource-status").value = row.status;
    $("resource-cost").value = row.hourly_cost;
    $("resource-tags").value = Object.entries(row.tags || {})
      .map(([key, value]) => `${key}=${value}`)
      .join("\n");
    $("resource-modal-title").textContent = `Edit ${row.resource_name}`;
  } else {
    $("resource-modal-title").textContent = "Add resource";
    refreshTypeRegion();
  }
  openModal("resource-modal");
}

function parseTags(text) {
  const tags = {};
  text
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean)
    .forEach((line) => {
      const index = line.indexOf("=");
      if (index > 0) {
        const key = line.slice(0, index).trim();
        const value = line.slice(index + 1).trim();
        if (key) tags[key] = value;
      }
    });
  return tags;
}

async function saveResource(event) {
  event.preventDefault();
  const id = $("resource-id").value;
  const accountId = $("resource-account").value;
  if (!accountId) {
    toast("Choose a cloud account", "error");
    return;
  }
  const payload = {
    account_id: Number(accountId),
    project_id: $("resource-project").value || null,
    type_id: Number($("resource-type").value),
    region_id: Number($("resource-region").value),
    external_resource_id: $("resource-external").value.trim(),
    resource_name: $("resource-name").value.trim(),
    status: $("resource-status").value,
    hourly_cost: Number($("resource-cost").value || 0),
    tags: parseTags($("resource-tags").value),
    user_id: 1,
  };
  if (!payload.type_id || !payload.region_id) {
    toast("Choose a resource type and region", "error");
    return;
  }
  const button = $("resource-save");
  button.disabled = true;
  try {
    if (id) {
      await api(`/resources/${id}`, { method: "PUT", body: payload });
      toast("Resource updated");
    } else {
      await api("/resources", { method: "POST", body: payload });
      toast("Resource created");
    }
    closeModal("resource-modal");
    await loadResources();
  } catch (error) {
    toast(error.message, "error");
  } finally {
    button.disabled = false;
  }
}

async function deleteResource(id) {
  const row = resourceState.rows.find((item) => item.resource_id === id);
  const label = row ? row.resource_name : `resource ${id}`;
  if (!window.confirm(`Soft-delete ${label}? The row remains for audit history.`)) return;
  try {
    await api(`/resources/${id}`, { method: "DELETE", body: { user_id: 1 } });
    toast("Resource marked as deleted");
    await loadResources();
  } catch (error) {
    toast(error.message, "error");
  }
}

async function showResourceDetail(id) {
  const body = $("detail-body");
  body.innerHTML = `<div class="empty"><span class="spinner"></span></div>`;
  openModal("detail-modal");
  try {
    const data = await api(`/resources/${id}`);
    const r = data.resource;
    const tags = Object.entries(r.tags || {});
    const deps = data.dependencies.length
      ? data.dependencies
          .map(
            (dep) => `<tr>
              <td>${escapeHtml(dep.direction)}</td>
              <td>${escapeHtml(dep.dependency_type)}</td>
              <td>${escapeHtml(dep.resource_name)}</td>
              <td>${badge(dep.status)}</td>
            </tr>`
          )
          .join("")
      : `<tr><td colspan="4" class="empty">No dependency edges.</td></tr>`;
    const history = data.status_history.length
      ? data.status_history
          .map(
            (item) => `<div class="list-item">
              <div><div class="title">${escapeHtml(item.previous_status || "new")} &rarr; ${escapeHtml(item.new_status)}</div>
              <div class="sub">${escapeHtml(item.reason || "No reason recorded")}</div></div>
              <div class="muted small">${escapeHtml(formatDate(item.transition_time, true))}</div>
            </div>`
          )
          .join("")
      : `<div class="empty">No status transitions.</div>`;
    const recs = data.recommendations.length
      ? data.recommendations
          .map(
            (item) => `<div class="list-item">
              <div><div class="title">${escapeHtml(item.recommendation_type.replace(/_/g, " "))}</div>
              <div class="sub">${escapeHtml(formatDate(item.detected_at))}</div></div>
              <div style="text-align:right">${badge(item.status)}<div class="small num">${escapeHtml(money(item.estimated_monthly_saving))}</div></div>
            </div>`
          )
          .join("")
      : `<div class="empty">No recommendations for this resource.</div>`;

    body.innerHTML = `
      <div class="grid grid-2">
        <div>
          <h2>${escapeHtml(r.resource_name)}</h2>
          <p class="muted mono small">${escapeHtml(r.external_resource_id)}</p>
          <dl class="kv" style="margin-top:14px">
            <dt>Provider</dt><dd>${escapeHtml(r.provider_name)} / ${escapeHtml(r.account_name)}</dd>
            <dt>Project</dt><dd>${escapeHtml(r.project_name || "Unassigned")}</dd>
            <dt>Type</dt><dd>${escapeHtml(r.raw_type_name)} (${escapeHtml(r.generic_category)})</dd>
            <dt>Region</dt><dd>${escapeHtml(r.region_code)} &middot; ${escapeHtml(r.region_name)}</dd>
            <dt>Status</dt><dd>${badge(r.status)}</dd>
            <dt>Hourly cost</dt><dd>${escapeHtml(money(r.hourly_cost))}</dd>
            <dt>Active tags</dt><dd>${tags.length ? tags.map(([k, v]) => `<span class="chip">${escapeHtml(k)}=${escapeHtml(v)}</span>`).join("") : '<span class="chip empty">untagged</span>'}</dd>
          </dl>
        </div>
        <div>
          <div class="detail-block" style="border-top:none;margin-top:0;padding-top:0">
            <h4>Dependencies</h4>
            <div class="table-wrap"><table><tbody>${deps}</tbody></table></div>
          </div>
        </div>
      </div>
      <div class="detail-block">
        <h4>Status history</h4>
        <div class="list">${history}</div>
      </div>
      <div class="detail-block">
        <h4>Recommendations</h4>
        <div class="list">${recs}</div>
      </div>`;
  } catch (error) {
    body.innerHTML = `<div class="notice error">${escapeHtml(error.message)}</div>`;
  }
}

async function runDetectors() {
  try {
    const [idle, orphaned, tags] = await Promise.all([
      api("/detections/idle"),
      api("/detections/orphaned-volumes"),
      api("/detections/missing-tags"),
    ]);
    renderDetectorRows(
      "detect-idle",
      idle.items,
      (row) => `
        <td><div>${escapeHtml(row.resource_name)}</div><div class="muted small">${escapeHtml(row.project_name || "Unassigned")}</div></td>
        <td class="num">${escapeHtml(num(row.average_cpu_percent, 1))}%</td>
        <td class="num">${escapeHtml(money(row.estimated_monthly_waste))}</td>`,
      "No idle compute found"
    );
    renderDetectorRows(
      "detect-orphaned",
      orphaned.items,
      (row) => `
        <td><div>${escapeHtml(row.resource_name)}</div><div class="muted small">${escapeHtml(row.region_code)}</div></td>
        <td>${badge(row.status)}</td>
        <td class="num">${escapeHtml(money(row.estimated_monthly_waste))}</td>`,
      "No orphaned volumes"
    );
    renderDetectorRows(
      "detect-tags",
      tags.items,
      (row) => `
        <td><div>${escapeHtml(row.resource_name)}</div><div class="muted small">${escapeHtml(row.project_name || "Unassigned")}</div></td>
        <td>${(row.missing_keys || []).map((key) => `<span class="chip">${escapeHtml(key)}</span>`).join("")}</td>`,
      "All monitored resources are tagged"
    );
  } catch (error) {
    toast(`Detector failed: ${error.message}`, "error");
  }
}

function renderDetectorRows(id, rows, mapper, emptyText) {
  const body = document.getElementById(id);
  if (!rows.length) {
    body.innerHTML = `<tr><td class="empty">${escapeHtml(emptyText)}</td></tr>`;
    return;
  }
  body.innerHTML = rows
    .slice(0, 8)
    .map((row) => `<tr>${mapper(row)}</tr>`)
    .join("");
}

document.addEventListener("DOMContentLoaded", initResources);

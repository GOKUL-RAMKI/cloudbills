const API_BASE = "/api";

async function api(path, options = {}) {
  const config = { ...options, headers: { ...(options.headers || {}) } };
  if (config.body && !(config.body instanceof FormData) && typeof config.body !== "string") {
    config.headers["Content-Type"] = "application/json";
    config.body = JSON.stringify(config.body);
  }
  const response = await fetch(`${API_BASE}${path}`, config);
  const text = await response.text();
  let data = null;
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      data = { error: text };
    }
  }
  if (!response.ok) {
    const message = (data && (data.error || data.message)) || response.statusText;
    const error = new Error(message);
    error.status = response.status;
    error.data = data;
    throw error;
  }
  return data;
}

function escapeHtml(value) {
  if (value === null || value === undefined) return "";
  return String(value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function money(value) {
  const amount = Number(value || 0);
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(amount);
}

function compactMoney(value) {
  const amount = Number(value || 0);
  if (Math.abs(amount) >= 1000000) return `$${(amount / 1000000).toFixed(2)}M`;
  if (Math.abs(amount) >= 1000) return `$${(amount / 1000).toFixed(1)}k`;
  return money(amount);
}

function num(value, digits = 0) {
  return new Intl.NumberFormat("en-US", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  }).format(Number(value || 0));
}

function sentenceCase(value) {
  let text = String(value || "").replace(/_/g, " ").toLowerCase();
  text = text
    .replace(/\bip\b/g, "IP")
    .replace(/\bid\b/g, "ID")
    .replace(/\bcpu\b/g, "CPU")
    .replace(/\bcsv\b/g, "CSV")
    .replace(/\bapi\b/g, "API");
  return text.charAt(0).toUpperCase() + text.slice(1);
}

function badge(status) {
  const raw = String(status || "unknown");
  return `<span class="badge ${escapeHtml(raw)}">${escapeHtml(sentenceCase(raw))}</span>`;
}

function monthLabel(value) {
  if (!value) return "";
  const text = String(value).slice(0, 7);
  const [year, month] = text.split("-");
  if (!year || !month) return text;
  const date = new Date(Number(year), Number(month) - 1, 1);
  return date.toLocaleDateString("en-US", { month: "short", year: "numeric" });
}

function currentMonth() {
  const now = new Date();
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}`;
}

function formatDate(value, withTime = false) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  const options = withTime
    ? { year: "numeric", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }
    : { year: "numeric", month: "short", day: "numeric" };
  return date.toLocaleDateString("en-US", options);
}

function toast(message, type = "success") {
  let stack = document.querySelector(".toast-stack");
  if (!stack) {
    stack = document.createElement("div");
    stack.className = "toast-stack";
    stack.setAttribute("role", "status");
    stack.setAttribute("aria-live", "polite");
    document.body.appendChild(stack);
  }
  const item = document.createElement("div");
  item.className = `toast ${type}`;
  item.textContent = message;
  stack.appendChild(item);
  setTimeout(() => {
    item.style.opacity = "0";
    item.style.transition = "opacity 180ms ease";
    setTimeout(() => item.remove(), 200);
  }, 4200);
}

let modalReturnFocus = null;

function openModal(id) {
  const modal = document.getElementById(id);
  if (!modal) return;
  modalReturnFocus = document.activeElement;
  modal.classList.remove("hidden");
  const dialog = modal.querySelector(".modal");
  if (!dialog) return;
  const target = dialog.querySelector(
    'input:not([type="hidden"]), select, textarea, button:not([disabled])'
  );
  if (target && window.matchMedia("(pointer: fine)").matches) {
    target.focus();
  }
}

function hideModal(backdrop) {
  if (!backdrop) return;
  backdrop.classList.add("hidden");
  if (modalReturnFocus && document.contains(modalReturnFocus)) {
    modalReturnFocus.focus();
  }
  modalReturnFocus = null;
}

function closeModal(id) {
  hideModal(document.getElementById(id));
}

function initModals() {
  document.querySelectorAll(".modal-backdrop").forEach((backdrop) => {
    const modal = backdrop.querySelector(".modal");
    if (modal) {
      modal.setAttribute("role", "dialog");
      modal.setAttribute("aria-modal", "true");
      const heading = modal.querySelector("h2");
      if (heading) {
        if (!heading.id) heading.id = `${backdrop.id}-title`;
        modal.setAttribute("aria-labelledby", heading.id);
      }
      modal.setAttribute("tabindex", "-1");
    }
    backdrop.addEventListener("click", (event) => {
      if (event.target === backdrop) hideModal(backdrop);
    });
    backdrop.querySelectorAll("[data-close]").forEach((button) => {
      button.addEventListener("click", () => hideModal(backdrop));
    });
  });
  document.addEventListener("keydown", (event) => {
    const open = document.querySelector(".modal-backdrop:not(.hidden)");
    if (!open) return;
    if (event.key === "Escape") {
      hideModal(open);
      return;
    }
    if (event.key !== "Tab") return;
    const focusable = Array.from(
      open.querySelectorAll(
        'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'
      )
    ).filter((element) => element.offsetParent !== null);
    if (!focusable.length) return;
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  });
}

function debounce(fn, delay = 300) {
  let timer;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), delay);
  };
}

function fillSelect(select, items, valueKey, labelFn, firstLabel = null) {
  if (!select) return;
  const current = select.value;
  select.innerHTML = "";
  if (firstLabel !== null) {
    const option = document.createElement("option");
    option.value = "";
    option.textContent = firstLabel;
    select.appendChild(option);
  }
  items.forEach((item) => {
    const option = document.createElement("option");
    option.value = item[valueKey];
    option.textContent = labelFn(item);
    select.appendChild(option);
  });
  if (current) select.value = current;
}

document.addEventListener("DOMContentLoaded", () => {
  initModals();
});

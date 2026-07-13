document.addEventListener("click", (event) => {
  const target = event.target;
  if (!(target instanceof HTMLElement)) {
    return;
  }
  const formId = target.dataset.focusForm;
  if (!formId) {
    return;
  }
  const form = document.getElementById(formId);
  const input = form?.querySelector("input, textarea, button");
  if (input instanceof HTMLElement) {
    input.focus();
    form.scrollIntoView({ behavior: "smooth", block: "center" });
  }
});

const DNS_ACTIVE_ZONE_STORAGE_KEY = "labfoundry:dns:active-zone";
const PUBLIC_ADDRESS_MODE_COOKIE = "labfoundry_public_address_mode";
const LABFOUNDRY_MUTATING_METHODS = new Set(["POST", "PUT", "PATCH", "DELETE"]);
const labFoundryDnsRecordTables = new WeakMap();
let applianceApplySidebarRefreshTimer = 0;

function labFoundryRequestMethod(input, init = {}) {
  return String(init.method || (input instanceof Request ? input.method : "GET")).toUpperCase();
}

function isLabFoundrySameOriginRequest(input) {
  try {
    const rawUrl = input instanceof Request ? input.url : String(input);
    const url = new URL(rawUrl, window.location.href);
    return url.origin === window.location.origin && url.pathname !== "/appliance-apply/status";
  } catch {
    return false;
  }
}

function scheduleApplianceApplySidebarRefresh() {
  window.clearTimeout(applianceApplySidebarRefreshTimer);
  applianceApplySidebarRefreshTimer = window.setTimeout(() => {
    refreshApplianceApplySidebar().catch(() => {});
  }, 50);
}

if (typeof window.fetch === "function" && !window.fetch.labFoundryApplyStatusWrapped) {
  const nativeFetch = window.fetch.bind(window);
  const wrappedFetch = async (input, init = {}) => {
    const method = labFoundryRequestMethod(input, init);
    const shouldRefresh =
      LABFOUNDRY_MUTATING_METHODS.has(method) &&
      isLabFoundrySameOriginRequest(input);
    const response = await nativeFetch(input, init);
    if (shouldRefresh && response.ok) {
      scheduleApplianceApplySidebarRefresh();
    }
    return response;
  };
  wrappedFetch.labFoundryApplyStatusWrapped = true;
  window.fetch = wrappedFetch;
}

function readCookieValue(name) {
  const prefix = `${name}=`;
  return document.cookie
    .split(";")
    .map((part) => part.trim())
    .find((part) => part.startsWith(prefix))
    ?.slice(prefix.length) || "";
}

function writeCookieValue(name, value) {
  document.cookie = `${name}=${encodeURIComponent(value)}; path=/; max-age=31536000; samesite=lax`;
}

function applyPublicAddressMode(mode) {
  const normalized = mode === "ip" ? "ip" : "name";
  document.querySelectorAll("[data-public-address-mode-option]").forEach((button) => {
    if (button instanceof HTMLButtonElement) {
      button.setAttribute("aria-pressed", button.dataset.publicAddressModeOption === normalized ? "true" : "false");
    }
  });
  document.querySelectorAll("[data-public-service-card]").forEach((card) => {
    if (!(card instanceof HTMLAnchorElement)) {
      return;
    }
    const target = normalized === "ip" ? card.dataset.ipHref : card.dataset.nameHref;
    if (target) {
      card.href = target;
    }
  });
}

function initializePublicAddressModeToggle() {
  const toggle = document.querySelector("[data-public-address-mode-toggle]");
  if (!(toggle instanceof HTMLElement)) {
    return;
  }
  const saved = decodeURIComponent(readCookieValue(PUBLIC_ADDRESS_MODE_COOKIE) || "");
  const initialMode = saved === "ip" ? "ip" : "name";
  applyPublicAddressMode(initialMode);
  toggle.querySelectorAll("[data-public-address-mode-option]").forEach((button) => {
    if (!(button instanceof HTMLButtonElement)) {
      return;
    }
    button.addEventListener("click", () => {
      const mode = button.dataset.publicAddressModeOption === "ip" ? "ip" : "name";
      writeCookieValue(PUBLIC_ADDRESS_MODE_COOKIE, mode);
      applyPublicAddressMode(mode);
    });
  });
}

function registerLabFoundryPrismLanguages() {
  if (!window.Prism || !window.Prism.languages) {
    return;
  }
  if (!window.Prism.languages.json) {
    window.Prism.languages.json = {
      property: {
        pattern: /"(?:\\.|[^\\"\r\n])*"(?=\s*:)/,
        greedy: true,
      },
      string: {
        pattern: /"(?:\\.|[^\\"\r\n])*"(?!\s*:)/,
        greedy: true,
      },
      number: /-?\b\d+(?:\.\d+)?(?:e[+-]?\d+)?\b/i,
      boolean: /\b(?:true|false)\b/,
      null: {
        pattern: /\bnull\b/,
        alias: "keyword",
      },
      operator: /:/,
      punctuation: /[{}\[\],]/,
    };
  }
  if (!window.Prism.languages["labfoundry-config"]) {
    window.Prism.languages["labfoundry-config"] = {
      comment: /(^|\n)\s*[#;].*/,
      section: {
        pattern: /(^|\n)\s*\[[^\]\r\n]+\]/,
        alias: "keyword",
      },
      property: /[A-Za-z0-9_.-]+(?=\s*=)/,
      string: {
        pattern: /"(?:\\.|[^"\\])*"/,
        greedy: true,
      },
      boolean: /\b(?:true|false|yes|no|enabled|disabled|accept|drop|reject)\b/i,
      number: /\b\d+(?:\.\d+)?(?:\/\d+)?\b/,
      operator: /=|:|\{|\}/,
      punctuation: /[\[\](),;]/,
    };
  }
  if (!window.Prism.languages["labfoundry-log"]) {
    window.Prism.languages["labfoundry-log"] = {
      error: {
        pattern: /(^|\n)(?:ERROR|FATAL|Caused by:).*/,
        lookbehind: true,
        alias: "important",
      },
      command: {
        pattern: /(^|\n)\$ .*/,
        lookbehind: true,
        alias: "function",
      },
      timestamp: /\b\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?\b/,
      stack: {
        pattern: /^\s+at\s+.+$/m,
        alias: "comment",
      },
      number: /\b\d+(?:\.\d+)*\b/,
      punctuation: /[()[\]{}:]/,
    };
  }
}

function previewLanguageForText(text, element) {
  if (element.classList.contains("language-diff")) {
    return "diff";
  }
  if (element.classList.contains("language-json")) {
    return "json";
  }
  const trimmed = String(text ?? "").trim();
  if ((trimmed.startsWith("{") || trimmed.startsWith("[")) && trimmed.length > 1) {
    try {
      JSON.parse(trimmed);
      return "json";
    } catch {
      // Non-JSON previews still get the compact LabFoundry config grammar.
    }
  }
  return "labfoundry-config";
}

function highlightConfigPreviewElement(element) {
  if (!(element instanceof HTMLElement) || !window.Prism || typeof window.Prism.highlightElement !== "function") {
    return;
  }
  registerLabFoundryPrismLanguages();
  const language = previewLanguageForText(element.textContent || "", element);
  element.classList.remove("language-json", "language-labfoundry-config");
  if (language !== "diff") {
    element.classList.remove("language-diff");
  }
  element.classList.add(`language-${language}`);
  if (element.parentElement instanceof HTMLElement && element.parentElement.tagName === "PRE") {
    element.parentElement.classList.remove("language-json", "language-labfoundry-config");
    if (language !== "diff") {
      element.parentElement.classList.remove("language-diff");
    }
    element.parentElement.classList.add(`language-${language}`);
  }
  window.Prism.highlightElement(element);
}

function highlightConfigPreviews(root = document) {
  if (!(root instanceof Document || root instanceof HTMLElement)) {
    return;
  }
  initializeConfigPreviewActions(root);
  initializeTerminalNoteActions(root);
  root
    .querySelectorAll(
      [
        ".config-preview code",
        ".config-diff code",
        ".terminal-note > code",
        "code.language-json",
        "code.language-labfoundry-config",
        "[data-appliance-settings-preview]",
        "[data-firewall-config-preview]",
        "[data-dns-config-preview]",
        "[data-vcf-config-preview]",
        "[data-vcf-registry-harbor-preview]",
        "[data-vcf-registry-relocation-preview]",
        "[data-vcf-depot-command-preview]",
        "[data-vcf-depot-https-preview]",
        "[data-esxi-pxe-preview]",
      ].join(", "),
    )
    .forEach((element) => highlightConfigPreviewElement(element));
}

function terminalNoteTitle(note) {
  const titleElement = note.querySelector("strong") || note.querySelector("summary");
  return titleElement?.textContent?.trim() || "Preview";
}

function openPreviewModal(title, text, sourceCode) {
  const modal = document.getElementById("preview-modal");
  const titleElement = document.getElementById("preview-modal-title");
  const code = modal?.querySelector("[data-preview-modal-code]");
  if (!(modal instanceof HTMLDialogElement) || !(titleElement instanceof HTMLElement) || !(code instanceof HTMLElement)) {
    return;
  }
  titleElement.textContent = title || "Preview";
  code.textContent = text || "";
  code.className = "";
  if (sourceCode instanceof HTMLElement) {
    sourceCode.classList.forEach((className) => {
      if (className.startsWith("language-")) {
        code.classList.add(className);
      }
    });
  }
  highlightConfigPreviewElement(code);
  if (typeof modal.showModal === "function") {
    modal.showModal();
  } else {
    modal.setAttribute("open", "");
  }
}

function initializePreviewModalControls() {
  const modal = document.getElementById("preview-modal");
  if (!(modal instanceof HTMLDialogElement) || modal.dataset.previewModalInitialized === "1") {
    return;
  }
  modal.dataset.previewModalInitialized = "1";
  const copyButton = modal.querySelector("[data-preview-modal-copy]");
  const closeButton = modal.querySelector("[data-preview-modal-close]");
  const code = modal.querySelector("[data-preview-modal-code]");
  copyButton?.addEventListener("click", async () => {
    try {
      await copyTextToClipboard(code?.textContent || "", "Copied", code);
    } catch {
      showTransientGridStatus("Copy failed");
    }
  });
  closeButton?.addEventListener("click", () => {
    modal.close();
  });
}

function initializeConfigPreviewActions(root = document) {
  if (!(root instanceof Document || root instanceof HTMLElement)) {
    return;
  }
  root.querySelectorAll("[data-config-preview-open]").forEach((button) => {
    if (!(button instanceof HTMLButtonElement) || button.dataset.configPreviewOpenInitialized === "1") {
      return;
    }
    const row = button.closest("[data-config-preview-row]");
    const code = row?.querySelector("[data-config-preview-source]");
    if (!(row instanceof HTMLElement) || !(code instanceof HTMLElement)) {
      return;
    }
    button.dataset.configPreviewOpenInitialized = "1";
    button.addEventListener("click", () => {
      const label = row.querySelector("[data-config-preview-label]")?.textContent?.trim() || "Rendered config";
      openPreviewModal(button.dataset.previewTitle || label, code.textContent || "", code);
    });
  });
}

function appendButtonIcon(button, paths) {
  const icon = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  icon.setAttribute("class", "button-icon");
  icon.setAttribute("viewBox", "0 0 24 24");
  icon.setAttribute("aria-hidden", "true");
  icon.setAttribute("focusable", "false");
  paths.forEach((pathData) => {
    const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
    path.setAttribute("d", pathData);
    icon.append(path);
  });
  button.replaceChildren(icon);
}

function initializeTerminalNoteActions(root = document) {
  if (!(root instanceof Document || root instanceof HTMLElement)) {
    return;
  }
  root.querySelectorAll(".terminal-note").forEach((note) => {
    if (!(note instanceof HTMLElement) || note.dataset.terminalNoteActions === "1") {
      return;
    }
    const code = note.querySelector("code");
    if (!(code instanceof HTMLElement)) {
      return;
    }
    note.dataset.terminalNoteActions = "1";
    note.classList.add("has-actions");
    const actions = document.createElement("div");
    actions.className = "terminal-note-actions";
    const copyButton = document.createElement("button");
    copyButton.className = "button secondary icon-button";
    copyButton.type = "button";
    copyButton.setAttribute("aria-label", "Copy preview");
    copyButton.setAttribute("title", "Copy preview");
    appendButtonIcon(copyButton, [
      "M8 8.75a2 2 0 0 1 2-2h7.25a2 2 0 0 1 2 2V16a2 2 0 0 1-2 2H10a2 2 0 0 1-2-2z",
      "M5.75 15.25H5a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h7.25a2 2 0 0 1 2 2v.75",
    ]);
    const openButton = document.createElement("button");
    openButton.className = "button secondary icon-button";
    openButton.type = "button";
    openButton.setAttribute("aria-label", "Open preview");
    openButton.setAttribute("title", "Open preview");
    appendButtonIcon(openButton, ["M7 17 17 7", "M9 7h8v8"]);
    actions.append(copyButton);
    if (note.dataset.terminalNoteOpen !== "false") {
      actions.append(openButton);
    }
    note.prepend(actions);
    copyButton.addEventListener("click", async () => {
      try {
        await copyTextToClipboard(code.textContent || "");
      } catch {
        showTransientGridStatus("Copy failed");
      }
    });
    if (note.dataset.terminalNoteOpen !== "false") {
      openButton.addEventListener("click", () => openPreviewModal(terminalNoteTitle(note), code.textContent || "", code));
    }
  });
}

function rememberDnsActiveZone(domain) {
  if (!domain) {
    return;
  }
  try {
    window.localStorage.setItem(DNS_ACTIVE_ZONE_STORAGE_KEY, domain);
  } catch {
    // Tab persistence is a convenience only; private browsing can disable it.
  }
}

function storedDnsActiveZone() {
  try {
    return window.localStorage.getItem(DNS_ACTIVE_ZONE_STORAGE_KEY) || "";
  } catch {
    return "";
  }
}

function dnsZoneTabButtonForDomain(domain) {
  if (!domain) {
    return null;
  }
  return Array.from(document.querySelectorAll(".zone-tabs [data-domain]")).find(
    (item) => item instanceof HTMLButtonElement && item.dataset.domain === domain,
  );
}

function showTableMessage(message, type = "error") {
  const element = document.getElementById("dns-record-error");
  if (!element) {
    return;
  }
  element.textContent = message;
  element.classList.toggle("error", type === "error");
  element.classList.toggle("success", type === "success");
  element.classList.remove("hidden");
}

function showTableError(message) {
  showTableMessage(message, "error");
}

function showTableSuccess(message) {
  showTransientGridStatus(message);
}

function clearTableError() {
  const element = document.getElementById("dns-record-error");
  if (!element) {
    return;
  }
  element.textContent = "";
  element.classList.add("hidden");
}

function redrawDnsRecordTables(root = document) {
  if (!(root instanceof Document || root instanceof HTMLElement)) {
    return;
  }
  window.requestAnimationFrame(() => {
    root.querySelectorAll(".dns-records-table").forEach((tableElement) => {
      const table = labFoundryDnsRecordTables.get(tableElement);
      if (table && typeof table.redraw === "function") {
        table.redraw(true);
      }
    });
  });
}

async function postDnsRecordAction(url, data, csrf, options = {}) {
  const reload = options.reload ?? true;
  const body = new FormData();
  body.set("csrf", csrf);
  for (const [key, value] of Object.entries(data)) {
    if (key === "id") {
      continue;
    }
    if (key === "host_label") {
      body.set("hostname", value ?? "");
      continue;
    }
    if (key === "enabled" || key === "masquerade") {
      if (value) {
        body.set(key, "on");
      }
      continue;
    }
    body.set(key, value ?? "");
  }

  const response = await fetch(url, {
    method: "POST",
    body,
    credentials: "same-origin",
  });

  if (!response.ok) {
    const text = await response.text();
    throw new Error(text.match(/DNS .* already exists[^<]*/)?.[0] || "The DNS record could not be saved.");
  }
  if (reload) {
    window.location.reload();
  }
}

function newDnsRecordRow(domain, suggestedAddress = "") {
  return {
    id: "__new__",
    hostname: "",
    host_label: "",
    domain,
    record_type: "A",
    address: suggestedAddress,
    suggested_ipv4: suggestedAddress,
    description: "",
    enabled: true,
    is_new: true,
    ...dnsRecordReverseStatus({
      record_type: "A",
      address: suggestedAddress,
      enabled: true,
    }),
  };
}

function escapeHtml(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function labFoundryBooleanFormatter(cell) {
  const enabled = Boolean(cell.getValue());
  const label = enabled ? "true" : "false";
  const glyph = enabled ? "✓" : "✕";
  const tone = enabled ? "good" : "bad";
  return `<span class="boolean-glyph ${tone}" aria-label="${label}" title="${label}">${glyph}</span>`;
}

function dnsRecordTypeLabel(value) {
  const labels = {
    A: "A (IPv4)",
    AAAA: "AAAA (IPv6)",
    CNAME: "CNAME (alias)",
    TXT: "TXT",
    SRV: "SRV",
    MX: "MX",
    CAA: "CAA",
    PTR: "PTR",
  };
  if (labels[value]) {
    return labels[value];
  }
  return String(value || "A");
}

function dnsRecordTypeOptions() {
  return {
    A: "A (IPv4)",
    AAAA: "AAAA (IPv6)",
    CNAME: "CNAME (alias)",
    TXT: "TXT",
    SRV: "SRV",
    MX: "MX",
    CAA: "CAA",
    PTR: "PTR",
  };
}

function dnsRecordValueHint(recordType) {
  if (recordType === "SRV") {
    return "target port priority weight";
  }
  if (recordType === "MX") {
    return "target preference";
  }
  if (recordType === "CAA") {
    return '0 issue "ca.example"';
  }
  if (recordType === "PTR") {
    return "target hostname";
  }
  if (recordType === "TXT") {
    return "text value";
  }
  if (recordType === "CNAME") {
    return "target hostname";
  }
  return "enter value...";
}

function legacyDnsRecordTypeLabel(value) {
  if (value === "AAAA") {
    return "AAAA (IPv6)";
  }
  if (value === "CNAME") {
    return "CNAME (alias)";
  }
  return "A (IPv4)";
}

function dnsAddRowHintFormatter(cell, emptyText) {
  const data = cell.getRow().getData();
  const value = cell.getValue();
  if (data.is_new && !String(value ?? "").trim()) {
    return `<span class="add-row-hint">${escapeHtml(emptyText)}</span>`;
  }
  return escapeHtml(value);
}

function newRecordRequiredValue(data, field) {
  return String(data?.[field] ?? "").trim();
}

function newRecordRequiredCellEditable(cell, requiredField) {
  const data = cell.getRow().getData();
  if (data.requires_activation && !data.is_activated) {
    return false;
  }
  return !data.is_new || data.is_activated || cell.getField() === requiredField || Boolean(newRecordRequiredValue(data, requiredField));
}

function markNewRecordRow(row, requiredField, actionField = "") {
  const data = row.getData();
  const isNew = Boolean(data.is_new);
  const isPending = isNew && !data.is_activated && !newRecordRequiredValue(data, requiredField);
  const element = row.getElement();
  element.classList.toggle("new-record-row", isNew);
  element.classList.toggle("new-record-row-pending", isPending);
  row.getCells().forEach((cell) => {
    cell.getElement().classList.toggle("new-record-primary-cell", cell.getField() === requiredField);
    cell.getElement().classList.toggle("new-record-action-cell", Boolean(actionField) && cell.getField() === actionField);
  });
}

function lockNewRecordColumns(columns, requiredField) {
  return columns.map((column) => {
    const originalEditable = column.editable;
    return {
      ...column,
      editable: (cell) => {
        const baseEditable = typeof originalEditable === "function" ? originalEditable(cell) : originalEditable !== false;
        return baseEditable && newRecordRequiredCellEditable(cell, requiredField);
      },
    };
  });
}

function reformatPendingNewRecord(cell) {
  const row = cell.getRow();
  if (row.getData().is_new) {
    row.reformat();
  }
}

function pendingNewDnsRecord(data) {
  return Boolean(data?.is_new && !String(data.host_label ?? "").trim());
}

function dnsRecordDomainFormatter(cell) {
  const data = cell.getRow().getData();
  if (pendingNewDnsRecord(data)) {
    return "";
  }
  return escapeHtml(cell.getValue());
}

let dhcpRangeTooltip = null;

function parseIpv4AddressParts(value) {
  const parts = String(value ?? "").trim().split(".");
  if (parts.length !== 4) {
    return null;
  }
  const octets = parts.map((part) => {
    if (!/^\d+$/.test(part)) {
      return null;
    }
    const number = Number(part);
    return number >= 0 && number <= 255 ? number : null;
  });
  return octets.some((part) => part === null) ? null : octets;
}

function parseCompactIpv4RangeEnd(value, startParts) {
  const parts = String(value ?? "").trim().split(".");
  if (!parts.length || parts.length > 4 || parts.some((part) => !/^\d+$/.test(part))) {
    return null;
  }
  const suffix = parts.map((part) => Number(part));
  if (suffix.some((part) => part < 0 || part > 255)) {
    return null;
  }
  return [...startParts.slice(0, 4 - suffix.length), ...suffix];
}

function ipv4PartsToText(parts) {
  return Array.isArray(parts) && parts.length === 4 ? parts.join(".") : "";
}

function dhcpRangeTooltipRows(data) {
  const expression = String(data?.range_expression ?? "").trim();
  if (!expression) {
    return [];
  }
  return expression
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean)
    .map((item) => {
      const [rawStart, rawEnd] = item.split("-", 2).map((part) => part.trim());
      if (!rawStart) {
        return null;
      }
      if (data?.address_family === "ipv6") {
        return { start: rawStart, end: rawEnd || rawStart };
      }
      const startParts = parseIpv4AddressParts(rawStart);
      if (!startParts) {
        return null;
      }
      const endParts = rawEnd ? parseCompactIpv4RangeEnd(rawEnd, startParts) : startParts;
      if (!endParts) {
        return null;
      }
      return { start: ipv4PartsToText(startParts), end: ipv4PartsToText(endParts) };
    })
    .filter(Boolean);
}

function ensureDhcpRangeTooltip() {
  if (!dhcpRangeTooltip) {
    dhcpRangeTooltip = document.createElement("div");
    dhcpRangeTooltip.className = "dhcp-range-tooltip hidden";
    document.body.appendChild(dhcpRangeTooltip);
  }
  return dhcpRangeTooltip;
}

function moveDhcpRangeTooltip(event) {
  if (!dhcpRangeTooltip || dhcpRangeTooltip.classList.contains("hidden")) {
    return;
  }
  const offset = 12;
  const width = dhcpRangeTooltip.offsetWidth || 240;
  const height = dhcpRangeTooltip.offsetHeight || 120;
  const x = Math.min(event.clientX + offset, window.innerWidth - width - offset);
  const y = Math.min(event.clientY + offset, window.innerHeight - height - offset);
  dhcpRangeTooltip.style.left = `${Math.max(offset, x)}px`;
  dhcpRangeTooltip.style.top = `${Math.max(offset, y)}px`;
}

function showDhcpRangeTooltip(event, data) {
  const rows = dhcpRangeTooltipRows(data);
  if (!rows.length) {
    return;
  }
  const tooltip = ensureDhcpRangeTooltip();
  tooltip.innerHTML = `
    <table>
      <thead><tr><th>Start</th><th>End</th></tr></thead>
      <tbody>
        ${rows.map((row) => `<tr><td>${escapeHtml(row.start)}</td><td>${escapeHtml(row.end)}</td></tr>`).join("")}
      </tbody>
    </table>
  `;
  tooltip.classList.remove("hidden");
  moveDhcpRangeTooltip(event);
}

function hideDhcpRangeTooltip() {
  if (dhcpRangeTooltip) {
    dhcpRangeTooltip.classList.add("hidden");
  }
}

function dhcpRangeFormatter(cell) {
  const data = cell.getRow().getData();
  const value = String(cell.getValue() ?? "").trim();
  if (data.is_new && !value) {
    if (!String(data.name ?? "").trim()) {
      return "";
    }
    if (data.address_family === "ipv6") {
      return "";
    }
    return dnsAddRowHintFormatter(cell, "192.168.87.100-192.168.87.200, 192.168.87.222");
  }
  const element = document.createElement("span");
  element.className = "dhcp-range-value";
  element.textContent = value;
  if (dhcpRangeTooltipRows(data).length) {
    element.addEventListener("mouseenter", (event) => showDhcpRangeTooltip(event, data));
    element.addEventListener("mousemove", moveDhcpRangeTooltip);
    element.addEventListener("mouseleave", hideDhcpRangeTooltip);
  }
  return element;
}

function dnsRecordCellEditable(cell) {
  const data = cell.getRow().getData();
  if (!data.is_new) {
    return true;
  }
  if (cell.getField() === "host_label") {
    return true;
  }
  return Boolean(String(data.host_label ?? "").trim());
}

function ipv4ReversePointer(value) {
  const parts = String(value ?? "").trim().split(".");
  if (parts.length !== 4) {
    return "";
  }
  const octets = parts.map((part) => {
    if (!/^\d+$/.test(part)) {
      return null;
    }
    const number = Number(part);
    return number >= 0 && number <= 255 ? String(number) : null;
  });
  if (octets.some((part) => part === null)) {
    return "";
  }
  return `${octets.reverse().join(".")}.in-addr.arpa`;
}

function expandIpv6(value) {
  const address = String(value ?? "").trim().toLowerCase();
  if (!address || address.includes(".")) {
    return null;
  }
  const doubleColonParts = address.split("::");
  if (doubleColonParts.length > 2) {
    return null;
  }
  const head = doubleColonParts[0] ? doubleColonParts[0].split(":") : [];
  const tail = doubleColonParts.length === 2 && doubleColonParts[1] ? doubleColonParts[1].split(":") : [];
  const explicitGroups = [...head, ...tail];
  if (explicitGroups.some((group) => !/^[0-9a-f]{1,4}$/.test(group))) {
    return null;
  }
  const missingGroups = 8 - explicitGroups.length;
  if ((doubleColonParts.length === 1 && missingGroups !== 0) || missingGroups < 0) {
    return null;
  }
  const groups = doubleColonParts.length === 2 ? [...head, ...Array(missingGroups).fill("0"), ...tail] : explicitGroups;
  return groups.length === 8 ? groups.map((group) => group.padStart(4, "0")).join("") : null;
}

function ipv6ReversePointer(value) {
  const expanded = expandIpv6(value);
  return expanded ? `${expanded.split("").reverse().join(".")}.ip6.arpa` : "";
}

function dnsRecordReverseStatus(data) {
  const type = String(data.record_type || "A").toUpperCase();
  if (type !== "A" && type !== "AAAA") {
    return {
      reverse_status: "not-applicable",
      reverse_label: "not applicable",
      reverse_ptr: "",
      reverse_zone: "",
    };
  }
  if (!data.enabled) {
    return {
      reverse_status: "disabled",
      reverse_label: "disabled",
      reverse_ptr: "",
      reverse_zone: "",
    };
  }
  const ptrName = type === "AAAA" ? ipv6ReversePointer(data.address) : ipv4ReversePointer(data.address);
  if (!ptrName) {
    return {
      reverse_status: data.address ? "invalid" : "pending",
      reverse_label: data.address ? "invalid address" : "",
      reverse_ptr: "",
      reverse_zone: "",
    };
  }
  return {
    reverse_status: "generated",
    reverse_label: ptrName,
    reverse_ptr: ptrName,
    reverse_zone: type === "AAAA" ? ptrName.split(".").slice(16).join(".") : ptrName.split(".").slice(1).join("."),
  };
}

function reverseStatusFormatter(cell) {
  const data = cell.getRow().getData();
  const status = data.reverse_status || "pending";
  const label = data.reverse_label || "";
  if (status === "generated") {
    return `<span class="reverse-status good" title="${escapeHtml(label)}">${escapeHtml(label)}</span>`;
  }
  if (status === "invalid") {
    return '<span class="reverse-status warn">invalid address</span>';
  }
  if (status === "disabled") {
    return '<span class="reverse-status muted">disabled</span>';
  }
  if (status === "not-applicable") {
    return '<span class="reverse-status muted">not applicable</span>';
  }
  return '<span class="reverse-status muted">waiting for value</span>';
}

function hasRequiredDnsRecordFields(data) {
  return Boolean((data.host_label || "").trim() && (data.address || "").trim());
}

async function autoSaveDnsRecord(cell, csrf) {
  clearTableError();
  const row = cell.getRow();
  const data = row.getData();
  if (data.is_new) {
    const field = cell.getField();
    if (field === "host_label") {
      row.reformat();
    }
    if (field === "record_type" && data.suggested_ipv4) {
      if (data.record_type !== "A" && data.address === data.suggested_ipv4) {
        await row.update({ address: "", ...dnsRecordReverseStatus({ ...data, address: "" }) });
        return;
      }
      if (data.record_type === "A" && !data.address) {
        await row.update({ address: data.suggested_ipv4, ...dnsRecordReverseStatus({ ...data, address: data.suggested_ipv4 }) });
        return;
      }
    }
    if (["record_type", "address", "enabled"].includes(field)) {
      await row.update(dnsRecordReverseStatus(row.getData()));
    }
    if (!hasRequiredDnsRecordFields(data)) {
      return;
    }
    try {
      await postDnsRecordAction("/dns/records", data, csrf, { reload: false });
      rememberDnsActiveZone(data.domain);
      showTableSuccess("Added");
      window.location.reload();
    } catch (error) {
      showTableError(error instanceof Error ? error.message : "The DNS record could not be added.");
      if (typeof cell.restoreOldValue === "function") {
        cell.restoreOldValue();
      }
    }
    return;
  }
  try {
    await postDnsRecordAction(`/dns/records/${data.id}/edit`, data, csrf, { reload: false });
    row.update(dnsRecordReverseStatus(row.getData()));
    showTableSuccess("Saved");
  } catch (error) {
    showTableError(error instanceof Error ? error.message : "The DNS record could not be saved.");
    if (typeof cell.restoreOldValue === "function") {
      cell.restoreOldValue();
    }
  }
}

async function deleteDnsRecordFromMenu(row, csrf) {
  clearTableError();
  const data = row.getData();
  try {
    rememberDnsActiveZone(data.domain);
    await postDnsRecordAction(`/dns/records/${data.id}/delete`, {}, csrf);
  } catch (error) {
    showTableError(error instanceof Error ? error.message : "The DNS record could not be deleted.");
  }
}

function showDhcpReservationMessage(message, type = "error") {
  const element = document.getElementById("dhcp-reservation-error");
  if (!element) {
    return;
  }
  element.textContent = message;
  element.classList.toggle("error", type === "error");
  element.classList.toggle("success", type === "success");
  element.classList.remove("hidden");
}

function showTransientGridStatus(message) {
  let toast = document.getElementById("grid-status-toast");
  if (!toast) {
    toast = document.createElement("div");
    toast.id = "grid-status-toast";
    toast.className = "grid-status-toast";
    toast.setAttribute("role", "status");
    toast.setAttribute("aria-live", "polite");
    document.body.appendChild(toast);
  }
  toast.textContent = message;
  toast.classList.add("visible");
  window.clearTimeout(showTransientGridStatus.timeoutId);
  showTransientGridStatus.timeoutId = window.setTimeout(() => {
    toast.classList.remove("visible");
  }, 1400);
}

function selectElementText(element) {
  const selection = window.getSelection();
  if (!selection || !(element instanceof HTMLElement)) {
    return false;
  }
  const range = document.createRange();
  range.selectNodeContents(element);
  selection.removeAllRanges();
  selection.addRange(range);
  return !selection.isCollapsed;
}

function copyTextWithTextareaFallback(value) {
  const textarea = document.createElement("textarea");
  textarea.value = value;
  textarea.setAttribute("readonly", "");
  textarea.style.position = "fixed";
  textarea.style.left = "-9999px";
  textarea.style.top = "0";
  document.body.appendChild(textarea);
  textarea.focus({ preventScroll: true });
  textarea.select();
  textarea.setSelectionRange(0, value.length);
  const copied = document.execCommand("copy");
  textarea.remove();
  return copied;
}

async function copyTextToClipboard(text, successMessage = "Copied", fallbackSelectionElement = null) {
  const value = String(text || "");
  if (!value) {
    return;
  }
  let copied = false;
  if (window.isSecureContext && window.navigator?.clipboard && typeof window.navigator.clipboard.writeText === "function") {
    try {
      await window.navigator.clipboard.writeText(value);
      copied = true;
    } catch {
      copied = copyTextWithTextareaFallback(value);
    }
  } else {
    copied = copyTextWithTextareaFallback(value);
  }
  if (copied) {
    showTransientGridStatus(successMessage);
    return;
  }
  if (selectElementText(fallbackSelectionElement)) {
    showTransientGridStatus("Selected");
    return;
  }
  throw new Error("Copy command failed.");
}

function selectCopyButtonFallbackText(button) {
  const valueElement = button.closest(".ca-fingerprint-value")?.querySelector("strong") || button;
  const selection = window.getSelection();
  if (!selection || !valueElement) {
    return false;
  }
  const range = document.createRange();
  range.selectNodeContents(valueElement);
  selection.removeAllRanges();
  selection.addRange(range);
  return !selection.isCollapsed;
}

async function copyValueButtonText(button) {
  try {
    await copyTextToClipboard(button.dataset.copyValue || button.textContent || "");
  } catch {
    showTransientGridStatus(selectCopyButtonFallbackText(button) ? "Selected" : "Copy failed");
  }
}

function initializeCopyValueButtons(root = document) {
  if (!(root instanceof Document || root instanceof HTMLElement)) {
    return;
  }
  root.querySelectorAll("[data-copy-value]").forEach((button) => {
    if (!(button instanceof HTMLButtonElement) || button.dataset.copyInitialized === "1") {
      return;
    }
    button.dataset.copyInitialized = "1";
    button.addEventListener("click", () => copyValueButtonText(button));
  });
}

document.addEventListener("click", (event) => {
  const target = event.target;
  if (!(target instanceof Element)) {
    return;
  }
  const button = target.closest("[data-copy-value]");
  if (!(button instanceof HTMLButtonElement) || button.dataset.copyInitialized === "1") {
    return;
  }
  event.preventDefault();
  copyValueButtonText(button);
});

function showDhcpReservationError(message) {
  showDhcpReservationMessage(message, "error");
}

function showDhcpReservationSuccess(message) {
  showTransientGridStatus(message);
}

function clearDhcpReservationError() {
  const element = document.getElementById("dhcp-reservation-error");
  if (!element) {
    return;
  }
  element.textContent = "";
  element.classList.add("hidden");
}

function showDhcpScopeMessage(message, type = "error") {
  const element = document.getElementById("dhcp-scope-error");
  if (!element) {
    return;
  }
  element.textContent = message;
  element.classList.toggle("error", type === "error");
  element.classList.toggle("success", type === "success");
  element.classList.remove("hidden");
}

function showDhcpScopeError(message) {
  showDhcpScopeMessage(message, "error");
}

function showDhcpScopeSuccess(message) {
  showTransientGridStatus(message);
}

function clearDhcpScopeError() {
  const element = document.getElementById("dhcp-scope-error");
  if (!element) {
    return;
  }
  element.textContent = "";
  element.classList.add("hidden");
}

function showDhcpOptionMessage(message, type = "error") {
  const element = document.getElementById("dhcp-option-error");
  if (!element) {
    return;
  }
  element.textContent = message;
  element.classList.toggle("error", type === "error");
  element.classList.toggle("success", type === "success");
  element.classList.remove("hidden");
}

function showDhcpOptionError(message) {
  showDhcpOptionMessage(message, "error");
}

function showDhcpOptionSuccess(message) {
  showTransientGridStatus(message);
}

function clearDhcpOptionError() {
  const element = document.getElementById("dhcp-option-error");
  if (!element) {
    return;
  }
  element.textContent = "";
  element.classList.add("hidden");
}

async function postDhcpScopeAction(url, data, csrf, options = {}) {
  const reload = options.reload ?? true;
  const body = new FormData();
  body.set("csrf", csrf);
  for (const [key, value] of Object.entries(data)) {
    if (key === "id" || key === "is_new") {
      continue;
    }
    if (key === "enabled") {
      if (value) {
        body.set("enabled", "on");
      }
      continue;
    }
    body.set(key, value ?? "");
  }

  const response = await fetch(url, {
    method: "POST",
    body,
    credentials: "same-origin",
  });

  if (!response.ok) {
    const text = await response.text();
    throw new Error(text.match(/DHCP IP zone .*?(?:already exists|family cannot be changed after it is created)[^<]*/)?.[0] || "The DHCP IP zone could not be saved.");
  }
  if (reload) {
    window.location.reload();
  }
}

async function postDhcpOptionAction(url, data, csrf, options = {}) {
  const reload = options.reload ?? true;
  const body = new FormData();
  body.set("csrf", csrf);
  for (const [key, value] of Object.entries(data)) {
    if (key === "id" || key === "is_new") {
      continue;
    }
    if (key === "enabled") {
      if (value) {
        body.set("enabled", "on");
      }
      continue;
    }
    body.set(key, value ?? "");
  }

  const response = await fetch(url, {
    method: "POST",
    body,
    credentials: "same-origin",
  });

  if (!response.ok) {
    throw new Error("The DHCP option could not be saved.");
  }
  if (reload) {
    window.location.reload();
  }
}

function normalizeDhcpZoneName(value) {
  return String(value || "").trim().toLowerCase();
}

function dhcpInterfaceDefaults(defaults, interfaceName) {
  const entries = Array.isArray(defaults.interfaces) ? defaults.interfaces : [];
  return entries.find((item) => item.name === interfaceName) || entries[0] || {};
}

function dhcpDefaultFamilyForInterface(defaults, interfaceName) {
  const interfaceDefaults = dhcpInterfaceDefaults(defaults, interfaceName);
  if (interfaceDefaults.ipv4_address) {
    return "ipv4";
  }
  if (interfaceDefaults.ipv6_address) {
    return "ipv6";
  }
  return "ipv4";
}

function applyDhcpScopeInterfaceDefaults(rowData, defaults, options = {}) {
  const overwrite = options.overwrite ?? false;
  const interfaceDefaults = dhcpInterfaceDefaults(defaults, rowData.interface_name);
  const family = rowData.address_family === "ipv6" ? "ipv6" : "ipv4";
  const gateway = family === "ipv6" ? interfaceDefaults.ipv6_address : interfaceDefaults.ipv4_address;
  const prefix = family === "ipv6" ? interfaceDefaults.ipv6_prefix : interfaceDefaults.ipv4_prefix;
  const dnsDefault = family === "ipv6" ? interfaceDefaults.ipv6_dns_default : interfaceDefaults.ipv4_dns_default;
  const ntpDefault = family === "ipv6" ? interfaceDefaults.ipv6_ntp_default : interfaceDefaults.ipv4_ntp_default;
  if (overwrite) {
    rowData.site_address = gateway || "";
  } else if (!rowData.site_address && gateway) {
    rowData.site_address = gateway;
  }
  if (overwrite) {
    rowData.prefix_length = Number.isInteger(prefix) ? prefix : "";
  } else if (!rowData.prefix_length && Number.isInteger(prefix)) {
    rowData.prefix_length = prefix;
  }
  if (overwrite) {
    rowData.dns_server = dnsDefault || "";
  } else if (!rowData.dns_server && dnsDefault) {
    rowData.dns_server = dnsDefault;
  }
  if (overwrite) {
    rowData.ntp_server = ntpDefault || "";
  } else if (!rowData.ntp_server && ntpDefault) {
    rowData.ntp_server = ntpDefault;
  }
  if ((overwrite || !rowData.domain_name) && defaults.default_domain) {
    rowData.domain_name = defaults.default_domain;
  }
  return rowData;
}

function isUniqueNewDhcpScopeName(data, existingNames) {
  const name = normalizeDhcpZoneName(data.name);
  return Boolean(name) && !existingNames.has(name);
}

function dhcpScopeCellEditable(cell, existingNames) {
  const data = cell.getRow().getData();
  if (!data.is_new) {
    return true;
  }
  if (cell.getField() === "name") {
    return true;
  }
  return isUniqueNewDhcpScopeName(data, existingNames);
}

function dhcpScopeFamilyEditable(cell, existingNames) {
  const data = cell.getRow().getData();
  if (!data.is_new) {
    return false;
  }
  return dhcpScopeCellEditable(cell, existingNames);
}

function newDhcpScopeRow(defaultInterface = "eth2", defaults = {}) {
  return {
    id: "__new__",
    name: "",
    address_family: "",
    interface_name: "",
    site_address: "",
    prefix_length: "",
    range_expression: "",
    lease_time: "",
    domain_name: "",
    dns_server: "",
    ntp_server: "",
    enabled: true,
    description: "",
    is_new: true,
  };
}

function newDhcpOptionRow() {
  return {
    id: "__new__",
    scope_id: "__global__",
    option_code: "",
    value: "",
    description: "",
    enabled: true,
    is_new: true,
  };
}

function hasRequiredDhcpOptionFields(data) {
  return Boolean((data.option_code || "").trim() && (data.value || "").trim());
}

async function autoSaveDhcpOption(cell, csrf) {
  clearDhcpOptionError();
  const row = cell.getRow();
  const data = row.getData();
  if (data.is_new) {
    if (!hasRequiredDhcpOptionFields(data)) {
      return;
    }
    try {
      await postDhcpOptionAction("/dhcp/options", data, csrf, { reload: false });
      showDhcpOptionSuccess("Added");
      window.location.reload();
    } catch (error) {
      showDhcpOptionError(error instanceof Error ? error.message : "The DHCP option could not be added.");
      if (typeof cell.restoreOldValue === "function") {
        cell.restoreOldValue();
      }
    }
    return;
  }
  try {
    await postDhcpOptionAction(`/dhcp/options/${data.id}/edit`, data, csrf, { reload: false });
    showDhcpOptionSuccess("Saved");
  } catch (error) {
    showDhcpOptionError(error instanceof Error ? error.message : "The DHCP option could not be saved.");
    if (typeof cell.restoreOldValue === "function") {
      cell.restoreOldValue();
    }
  }
}

async function deleteDhcpOptionFromMenu(row, csrf) {
  clearDhcpOptionError();
  const data = row.getData();
  if (data.is_new) {
    return;
  }
  const confirmed = await requestConfirmation({
    title: `Delete DHCP option ${data.option_code}?`,
    message: "This removes the DHCP option from LabFoundry desired state. It will not touch the appliance until global appliance apply runs.",
    label: "Delete option",
  });
  if (!confirmed) {
    return;
  }
  try {
    await postDhcpOptionAction(`/dhcp/options/${data.id}/delete`, {}, csrf);
  } catch (error) {
    showDhcpOptionError(error instanceof Error ? error.message : "The DHCP option could not be deleted.");
  }
}

function hasRequiredDhcpScopeFields(data) {
  return Boolean(
    (data.name || "").trim() &&
      (data.interface_name || "").trim() &&
      (data.site_address || "").trim() &&
      (data.range_expression || "").trim() &&
      (data.dns_server || "").trim(),
  );
}

async function autoSaveDhcpScope(cell, csrf) {
  clearDhcpScopeError();
  const row = cell.getRow();
  const data = row.getData();
  if (data.is_new) {
    if (!hasRequiredDhcpScopeFields(data)) {
      return;
    }
    try {
      await postDhcpScopeAction("/dhcp/scopes", data, csrf, { reload: false });
      showDhcpScopeSuccess("Added");
      window.location.reload();
    } catch (error) {
      showDhcpScopeError(error instanceof Error ? error.message : "The DHCP IP zone could not be added.");
      if (typeof cell.restoreOldValue === "function") {
        cell.restoreOldValue();
      }
    }
    return;
  }
  try {
    await postDhcpScopeAction(`/dhcp/scopes/${data.id}/edit`, data, csrf, { reload: false });
    showDhcpScopeSuccess("Saved");
  } catch (error) {
    showDhcpScopeError(error instanceof Error ? error.message : "The DHCP IP zone could not be saved.");
    if (typeof cell.restoreOldValue === "function") {
      cell.restoreOldValue();
    }
  }
}

async function deleteDhcpScopeFromMenu(row, csrf) {
  clearDhcpScopeError();
  const data = row.getData();
  if (data.is_new) {
    return;
  }
  const confirmed = await requestConfirmation({
    title: `Delete ${data.name} IP zone?`,
    message: `This removes DHCP IP zone ${data.name} from LabFoundry desired state. It will not touch the appliance until global appliance apply runs.`,
    label: "Delete IP zone",
  });
  if (!confirmed) {
    return;
  }
  try {
    await postDhcpScopeAction(`/dhcp/scopes/${data.id}/delete`, {}, csrf);
  } catch (error) {
    showDhcpScopeError(error instanceof Error ? error.message : "The DHCP IP zone could not be deleted.");
  }
}

async function postDhcpReservationAction(url, data, csrf, options = {}) {
  const reload = options.reload ?? true;
  const body = new FormData();
  body.set("csrf", csrf);
  for (const [key, value] of Object.entries(data)) {
    if (key === "id" || key === "is_new") {
      continue;
    }
    if (key === "enabled") {
      if (value) {
        body.set("enabled", "on");
      }
      continue;
    }
    body.set(key, value ?? "");
  }

  const response = await fetch(url, {
    method: "POST",
    body,
    credentials: "same-origin",
  });

  if (!response.ok) {
    const text = await response.text();
    throw new Error(text.match(/DHCP reservation already exists[^<]*/)?.[0] || "The DHCP reservation could not be saved.");
  }
  if (reload) {
    window.location.reload();
  }
}

function newDhcpReservationRow() {
  return {
    id: "__new__",
    hostname: "",
    mac_address: "",
    ip_address: "",
    description: "",
    enabled: true,
    is_new: true,
  };
}

function hasRequiredDhcpReservationFields(data) {
  return Boolean((data.hostname || "").trim() && (data.mac_address || "").trim() && (data.ip_address || "").trim());
}

function dhcpReservationHasHostname(data) {
  return Boolean(String(data.hostname ?? "").trim());
}

function dhcpReservationCellEditable(cell) {
  const data = cell.getRow().getData();
  if (!data.is_new) {
    return true;
  }
  if (cell.getField() === "hostname") {
    return true;
  }
  return dhcpReservationHasHostname(data);
}

function dhcpReservationAddRowHintFormatter(cell, hint) {
  const data = cell.getRow().getData();
  if (data.is_new && !dhcpReservationHasHostname(data) && !String(cell.getValue() ?? "").trim()) {
    return "";
  }
  return dnsAddRowHintFormatter(cell, hint);
}

async function autoSaveDhcpReservation(cell, csrf) {
  clearDhcpReservationError();
  const row = cell.getRow();
  const data = row.getData();
  if (data.is_new) {
    if (!hasRequiredDhcpReservationFields(data)) {
      return;
    }
    try {
      await postDhcpReservationAction("/dhcp/reservations", data, csrf, { reload: false });
      showDhcpReservationSuccess("Added");
      window.location.reload();
    } catch (error) {
      showDhcpReservationError(error instanceof Error ? error.message : "The DHCP reservation could not be added.");
      if (typeof cell.restoreOldValue === "function") {
        cell.restoreOldValue();
      }
    }
    return;
  }
  try {
    await postDhcpReservationAction(`/dhcp/reservations/${data.id}/edit`, data, csrf, { reload: false });
    showDhcpReservationSuccess("Saved");
  } catch (error) {
    showDhcpReservationError(error instanceof Error ? error.message : "The DHCP reservation could not be saved.");
    if (typeof cell.restoreOldValue === "function") {
      cell.restoreOldValue();
    }
  }
}

async function deleteDhcpReservationFromMenu(row, csrf) {
  clearDhcpReservationError();
  const data = row.getData();
  if (data.is_new) {
    return;
  }
  const confirmed = await requestConfirmation({
    title: `Delete ${data.hostname || data.mac_address} reservation?`,
    message: `This removes the DHCP reservation for ${data.mac_address} from LabFoundry desired state. It will not touch the appliance until global appliance apply runs.`,
    label: "Delete reservation",
  });
  if (!confirmed) {
    return;
  }
  try {
    await postDhcpReservationAction(`/dhcp/reservations/${data.id}/delete`, {}, csrf);
  } catch (error) {
    showDhcpReservationError(error instanceof Error ? error.message : "The DHCP reservation could not be deleted.");
  }
}

function closeDhcpLeaseMenus(exceptMenu = null) {
  document.querySelectorAll("[data-lease-menu]").forEach((menu) => {
    if (menu !== exceptMenu) {
      menu.setAttribute("hidden", "");
    }
  });
}

function openDhcpLeaseReservationModal(source) {
  const modal = document.getElementById("dhcp-lease-reservation-modal");
  if (!(modal instanceof HTMLDialogElement)) {
    return;
  }
  const hostnameInput = modal.querySelector("[data-dhcp-lease-modal-hostname]");
  const macInput = modal.querySelector("[data-dhcp-lease-modal-mac-input]");
  const ipInput = modal.querySelector("[data-dhcp-lease-modal-ip-input]");
  const descriptionInput = modal.querySelector("[data-dhcp-lease-modal-description-input]");
  const macText = modal.querySelector("[data-dhcp-lease-modal-mac]");
  const ipText = modal.querySelector("[data-dhcp-lease-modal-ip]");
  const hostname = source instanceof HTMLElement ? source.dataset.hostname || "" : source?.hostname || "";
  const macAddress = source instanceof HTMLElement ? source.dataset.macAddress || "" : source?.mac_address || "";
  const ipAddress = source instanceof HTMLElement ? source.dataset.ipAddress || "" : source?.ip_address || "";
  if (hostnameInput instanceof HTMLInputElement) {
    hostnameInput.value = hostname;
  }
  if (macInput instanceof HTMLInputElement) {
    macInput.value = macAddress;
  }
  if (ipInput instanceof HTMLInputElement) {
    ipInput.value = ipAddress;
  }
  if (descriptionInput instanceof HTMLInputElement) {
    descriptionInput.value = `Created from live DHCP lease ${ipAddress}.`;
  }
  if (macText instanceof HTMLElement) {
    macText.textContent = macAddress;
  }
  if (ipText instanceof HTMLElement) {
    ipText.textContent = ipAddress;
  }
  closeDhcpLeaseMenus();
  if (typeof modal.showModal === "function") {
    modal.showModal();
  } else {
    modal.setAttribute("open", "");
  }
  if (hostnameInput instanceof HTMLInputElement) {
    hostnameInput.focus();
    hostnameInput.select();
  }
}

function dhcpLeaseHostname(source) {
  const hostname = source instanceof HTMLElement ? source.dataset.hostname || "" : source?.hostname || "";
  return String(hostname || "").trim().replace(/\.$/, "").toLowerCase();
}

function dhcpLeaseMacAddress(source) {
  return source instanceof HTMLElement ? source.dataset.macAddress || "" : source?.mac_address || "";
}

function dhcpLeaseIpAddress(source) {
  return source instanceof HTMLElement ? source.dataset.ipAddress || "" : source?.ip_address || "";
}

function defaultDhcpLeasePxeHostname(source) {
  const hostname = dhcpLeaseHostname(source);
  if (hostname && hostname !== "-") {
    return hostname;
  }
  const macSuffix = dhcpLeaseMacAddress(source).toLowerCase().replace(/[^0-9a-f]/g, "").slice(-6) || "host";
  return `esxi-${macSuffix}.labfoundry.internal`;
}

function openDhcpLeasePxeModal(source) {
  const modal = document.getElementById("dhcp-lease-pxe-modal");
  if (!(modal instanceof HTMLDialogElement)) {
    return;
  }
  const hostnameInput = modal.querySelector("[data-dhcp-lease-pxe-hostname]");
  const macInput = modal.querySelector("[data-dhcp-lease-pxe-mac-input]");
  const ipInput = modal.querySelector("[data-dhcp-lease-pxe-ip-input]");
  const macText = modal.querySelector("[data-dhcp-lease-pxe-mac]");
  const ipText = modal.querySelector("[data-dhcp-lease-pxe-ip]");
  const macAddress = dhcpLeaseMacAddress(source);
  const ipAddress = dhcpLeaseIpAddress(source);
  if (hostnameInput instanceof HTMLInputElement) {
    hostnameInput.value = defaultDhcpLeasePxeHostname(source);
  }
  if (macInput instanceof HTMLInputElement) {
    macInput.value = macAddress;
  }
  if (ipInput instanceof HTMLInputElement) {
    ipInput.value = ipAddress;
  }
  if (macText instanceof HTMLElement) {
    macText.textContent = macAddress;
  }
  if (ipText instanceof HTMLElement) {
    ipText.textContent = ipAddress;
  }
  closeDhcpLeaseMenus();
  if (typeof modal.showModal === "function") {
    modal.showModal();
  } else {
    modal.setAttribute("open", "");
  }
  if (hostnameInput instanceof HTMLInputElement) {
    hostnameInput.focus();
    hostnameInput.select();
  }
}

function dhcpLeaseStatusFormatter(cell) {
  const status = String(cell.getValue() || "");
  return `<span class="status-pill ${status === "active" ? "good" : "muted"}">${escapeHtml(status || "unknown")}</span>`;
}

function submitDhcpLeaseAction(path, data, csrf) {
  const form = document.createElement("form");
  form.method = "post";
  form.action = path;
  const values = {
    csrf,
    hostname: data.hostname || "",
    mac_address: data.mac_address || "",
    ip_address: data.ip_address || "",
  };
  Object.entries(values).forEach(([name, value]) => {
    const input = document.createElement("input");
    input.type = "hidden";
    input.name = name;
    input.value = value;
    form.append(input);
  });
  document.body.append(form);
  form.requestSubmit();
}

function initializeDhcpLeasesTable() {
  const tableElement = document.getElementById("dhcp-leases-table");
  if (!(tableElement instanceof HTMLElement)) {
    return;
  }
  const fallback = document.getElementById(tableElement.dataset.fallbackId || "");
  if (typeof Tabulator === "undefined") {
    showDhcpReservationError("Tabulator did not load. Showing the fallback table.");
    return;
  }
  const csrf = tableElement.dataset.csrf || "";
  const rows = JSON.parse(tableElement.dataset.leases || "[]");
  try {
    new Tabulator(tableElement, {
      data: rows,
      layout: "fitColumns",
      height: "300px",
      rowHeight: 28,
      placeholder: "No DHCP leases reported.",
      reactiveData: false,
      rowContextMenu: [
        {
          label: "Create reservation",
          action: (event, row) => openDhcpLeaseReservationModal(row.getData()),
        },
        {
          label: "Create PXE entry",
          action: (event, row) => openDhcpLeasePxeModal(row.getData()),
        },
        {
          label: "Deny DHCP for MAC",
          action: async (event, row) => {
            const data = row.getData();
            const confirmed = await requestConfirmation({
              title: `Deny DHCP for ${data.mac_address}?`,
              message: "This adds a LabFoundry desired-state dnsmasq ignore rule for this MAC. It will not affect the appliance until global appliance apply runs.",
              label: "Deny DHCP",
            });
            if (confirmed) {
              submitDhcpLeaseAction("/dhcp/leases/deny", data, csrf);
            }
          },
        },
      ],
      columns: [
        { title: "Status", field: "status", formatter: dhcpLeaseStatusFormatter, width: 100 },
        { title: "DNS name / FQDN", field: "hostname", formatter: (cell) => escapeHtml(cell.getValue() || "-"), minWidth: 190 },
        { title: "IP", field: "ip_address", minWidth: 140 },
        { title: "Zone", field: "zone_name", formatter: (cell) => escapeHtml(cell.getValue() || "-"), minWidth: 120 },
        { title: "MAC", field: "mac_address", minWidth: 170 },
        { title: "Expires", field: "expires_at", minWidth: 210 },
        { title: "Client ID", field: "client_id", formatter: (cell) => escapeHtml(cell.getValue() || "-"), minWidth: 210 },
      ],
    });
    if (fallback) {
      fallback.classList.add("hidden");
    }
  } catch (error) {
    showDhcpReservationError(error instanceof Error ? error.message : "Tabulator could not render. Showing the fallback table.");
  }
}

function initializeDhcpLeaseReservationActions() {
  const reservationModal = document.getElementById("dhcp-lease-reservation-modal");
  if (reservationModal instanceof HTMLDialogElement && reservationModal.dataset.leaseReservationInitialized !== "1") {
    reservationModal.dataset.leaseReservationInitialized = "1";
    reservationModal.querySelectorAll("[data-dhcp-lease-modal-cancel]").forEach((button) => {
      if (button instanceof HTMLButtonElement) {
        button.addEventListener("click", () => reservationModal.close("cancel"));
      }
    });
    reservationModal.addEventListener("click", (event) => {
      if (event.target === reservationModal) {
        reservationModal.close("cancel");
      }
    });
  }
  const pxeModal = document.getElementById("dhcp-lease-pxe-modal");
  if (pxeModal instanceof HTMLDialogElement && pxeModal.dataset.leasePxeInitialized !== "1") {
    pxeModal.dataset.leasePxeInitialized = "1";
    pxeModal.querySelectorAll("[data-dhcp-lease-pxe-cancel]").forEach((button) => {
      if (button instanceof HTMLButtonElement) {
        button.addEventListener("click", () => pxeModal.close("cancel"));
      }
    });
    pxeModal.addEventListener("click", (event) => {
      if (event.target === pxeModal) {
        pxeModal.close("cancel");
      }
    });
  }
  document.querySelectorAll("[data-lease-menu-toggle]").forEach((button) => {
    if (!(button instanceof HTMLButtonElement) || button.dataset.leaseMenuInitialized === "1") {
      return;
    }
    button.dataset.leaseMenuInitialized = "1";
    button.addEventListener("click", (event) => {
      event.stopPropagation();
      const menu = button.closest("[data-lease-action-menu]")?.querySelector("[data-lease-menu]");
      if (!(menu instanceof HTMLElement)) {
        return;
      }
      const isOpening = menu.hasAttribute("hidden");
      closeDhcpLeaseMenus(menu);
      menu.toggleAttribute("hidden", !isOpening);
    });
  });
  document.querySelectorAll("[data-dhcp-lease-reservation]").forEach((button) => {
    if (!(button instanceof HTMLButtonElement) || button.dataset.leaseReservationButtonInitialized === "1") {
      return;
    }
    button.dataset.leaseReservationButtonInitialized = "1";
    button.addEventListener("click", () => openDhcpLeaseReservationModal(button));
  });
  document.querySelectorAll("[data-dhcp-lease-pxe-host]").forEach((button) => {
    if (!(button instanceof HTMLButtonElement) || button.dataset.leasePxeButtonInitialized === "1") {
      return;
    }
    button.dataset.leasePxeButtonInitialized = "1";
    button.addEventListener("click", () => openDhcpLeasePxeModal(button));
  });
  document.addEventListener("click", (event) => {
    if (!(event.target instanceof HTMLElement) || !event.target.closest("[data-lease-action-menu]")) {
      closeDhcpLeaseMenus();
    }
  });
}

function showEsxiHostMessage(message, type = "error") {
  const element = document.getElementById("esxi-pxe-host-error");
  if (!element) {
    return;
  }
  element.textContent = message;
  element.classList.toggle("error", type === "error");
  element.classList.toggle("success", type === "success");
  element.classList.remove("hidden");
}

function showEsxiHostError(message) {
  showEsxiHostMessage(message, "error");
}

function showEsxiHostSuccess(message) {
  showTransientGridStatus(message);
}

function clearEsxiHostError() {
  const element = document.getElementById("esxi-pxe-host-error");
  if (!element) {
    return;
  }
  element.textContent = "";
  element.classList.add("hidden");
}

async function postEsxiHostAction(url, data, csrf, options = {}) {
  const reload = options.reload ?? true;
  const body = new FormData();
  body.set("csrf", csrf);
  for (const [key, value] of Object.entries(data)) {
    if (key === "id" || key === "is_new" || key === "is_default" || key === "variables" || key.endsWith("_name")) {
      continue;
    }
    if (key === "variables_json") {
      body.set("variables", value ?? "{}");
      continue;
    }
    if (key === "enabled") {
      if (value) {
        body.set("enabled", "on");
      }
      continue;
    }
    body.set(key, value ?? "");
  }
  const response = await fetch(url, {
    method: "POST",
    body,
    credentials: "same-origin",
  });
  if (!response.ok) {
    const text = await response.text();
    throw new Error(text.match(/ESXi PXE host[^<]*/)?.[0] || text.match(/Default ESXi PXE[^<]*/)?.[0] || "The ESXi PXE host reference could not be saved.");
  }
  if (reload) {
    window.location.reload();
  }
}

function newEsxiHostRow(defaultIsoPath = "") {
  return {
    id: "new",
    hostname: "",
    mac_address: "",
    ip_address: "",
    kickstart_id: "",
    kickstart_name: "",
    installer_iso_path: defaultIsoPath,
    installer_iso_name: "",
    variables_json: "{}",
    enabled: true,
    is_new: true,
    is_default: false,
  };
}

function hasRequiredEsxiHostFields(data) {
  return Boolean((data.hostname || "").trim() && (data.mac_address || "").trim());
}

async function autoSaveEsxiHost(cell, csrf) {
  clearEsxiHostError();
  const row = cell.getRow();
  const data = row.getData();
  if (data.is_default) {
    try {
      await postEsxiHostAction("/esxi-pxe/default-host", data, csrf, { reload: false });
      showEsxiHostSuccess("Saved");
    } catch (error) {
      showEsxiHostError(error instanceof Error ? error.message : "The default ESXi PXE host profile could not be saved.");
      if (typeof cell.restoreOldValue === "function") {
        cell.restoreOldValue();
      }
    }
    return;
  }
  if (data.is_new) {
    if (!hasRequiredEsxiHostFields(data)) {
      reformatPendingNewRecord(cell);
      return;
    }
    try {
      await postEsxiHostAction("/esxi-pxe/hosts", data, csrf);
      showEsxiHostSuccess("Added");
    } catch (error) {
      showEsxiHostError(error instanceof Error ? error.message : "The ESXi PXE host reference could not be added.");
      if (typeof cell.restoreOldValue === "function") {
        cell.restoreOldValue();
      }
    }
    return;
  }
  try {
    await postEsxiHostAction(`/esxi-pxe/hosts/${data.id}`, data, csrf, { reload: false });
    showEsxiHostSuccess("Saved");
  } catch (error) {
    showEsxiHostError(error instanceof Error ? error.message : "The ESXi PXE host reference could not be saved.");
    if (typeof cell.restoreOldValue === "function") {
      cell.restoreOldValue();
    }
  }
}

async function deleteEsxiHost(row, csrf) {
  clearEsxiHostError();
  const data = row.getData();
  if (data.is_new || data.is_default) {
    return;
  }
  const confirmed = await requestConfirmation({
    title: `Delete ${data.hostname} host reference?`,
    message: `This removes the ESXi PXE host reference for ${data.mac_address} from desired state. It will not touch generated PXE files until global appliance apply runs.`,
    label: "Delete host reference",
  });
  if (!confirmed) {
    return;
  }
  try {
    await postEsxiHostAction(`/esxi-pxe/hosts/${data.id}/delete`, {}, csrf);
  } catch (error) {
    showEsxiHostError(error instanceof Error ? error.message : "The ESXi PXE host reference could not be deleted.");
  }
}

function showCaMessage(elementId, message, type = "error") {
  const element = document.getElementById(elementId);
  if (!element) {
    return;
  }
  element.textContent = message;
  element.classList.toggle("error", type === "error");
  element.classList.toggle("success", type === "success");
  element.classList.remove("hidden");
}

function clearCaMessage(elementId) {
  const element = document.getElementById(elementId);
  if (!element) {
    return;
  }
  element.textContent = "";
  element.classList.add("hidden");
}

async function postCaAction(url, data, csrf, options = {}) {
  const reload = options.reload ?? true;
  const body = new FormData();
  body.set("csrf", csrf);
  for (const [key, value] of Object.entries(data)) {
    if (["id", "is_new", "profile_name", "managed_owner", "fingerprint", "cert_path", "has_certificate", "has_private_key"].includes(key)) {
      continue;
    }
    if (key === "enabled" || key === "san_required") {
      if (value) {
        body.set(key, "on");
      }
      continue;
    }
    body.set(key, value ?? "");
  }

  const response = await fetch(url, {
    method: "POST",
    body,
    credentials: "same-origin",
  });

  if (!response.ok) {
    const text = await response.text();
    throw new Error(text.match(/CA .* already exists[^<]*/)?.[0] || "The CA desired state could not be saved.");
  }
  if (reload) {
    window.location.reload();
  }
}

function newCaProfileRow() {
  return {
    id: "__new__",
    name: "",
    certificate_type: "server",
    validity_days: 825,
    key_algorithm: "RSA",
    key_size: 2048,
    key_usage: "digitalSignature,keyEncipherment",
    extended_key_usage: "serverAuth",
    san_required: true,
    enabled: true,
    description: "",
    is_new: true,
  };
}

function newCaCertificateRow(defaultProfileId = "") {
  return {
    id: "__new__",
    common_name: "",
    profile_id: defaultProfileId,
    profile_name: "",
    subject_alt_names: "",
    ip_addresses: "",
    status: "planned",
    serial_number: "",
    fingerprint: "",
    managed_owner: "manual",
    cert_path: "",
    has_certificate: false,
    has_private_key: false,
    enabled: true,
    description: "",
    is_new: true,
  };
}

function hasRequiredCaProfileFields(data) {
  return Boolean((data.name || "").trim() && (data.certificate_type || "").trim());
}

function hasRequiredCaCertificateFields(data) {
  return Boolean((data.common_name || "").trim());
}

async function autoSaveCaProfile(cell, csrf) {
  clearCaMessage("ca-profile-error");
  const row = cell.getRow();
  const data = row.getData();
  if (data.is_new) {
    if (!hasRequiredCaProfileFields(data)) {
      reformatPendingNewRecord(cell);
      return;
    }
    try {
      await postCaAction("/certificate-authority/profiles", data, csrf, { reload: false });
      showTransientGridStatus("Added");
      window.location.reload();
    } catch (error) {
      showCaMessage("ca-profile-error", error instanceof Error ? error.message : "The CA profile could not be added.");
      if (typeof cell.restoreOldValue === "function") {
        cell.restoreOldValue();
      }
    }
    return;
  }
  try {
    await postCaAction(`/certificate-authority/profiles/${data.id}/edit`, data, csrf, { reload: false });
    showTransientGridStatus("Saved");
  } catch (error) {
    showCaMessage("ca-profile-error", error instanceof Error ? error.message : "The CA profile could not be saved.");
    if (typeof cell.restoreOldValue === "function") {
      cell.restoreOldValue();
    }
  }
}

async function deleteCaProfileFromMenu(row, csrf) {
  clearCaMessage("ca-profile-error");
  const data = row.getData();
  if (data.is_new) {
    return;
  }
  const confirmed = await requestConfirmation({
    title: `Delete ${data.name} profile?`,
    message: "This removes the CA profile from LabFoundry desired state and unassigns requests using it. It will not touch the appliance until global appliance apply runs.",
    label: "Delete profile",
  });
  if (!confirmed) {
    return;
  }
  try {
    await postCaAction(`/certificate-authority/profiles/${data.id}/delete`, {}, csrf);
  } catch (error) {
    showCaMessage("ca-profile-error", error instanceof Error ? error.message : "The CA profile could not be deleted.");
  }
}

async function autoSaveCaCertificate(cell, csrf) {
  clearCaMessage("ca-certificate-error");
  const row = cell.getRow();
  const data = row.getData();
  if (data.is_new) {
    if (!hasRequiredCaCertificateFields(data)) {
      reformatPendingNewRecord(cell);
      return;
    }
    try {
      await postCaAction("/certificate-authority/certificates", data, csrf, { reload: false });
      showTransientGridStatus("Added");
      window.location.reload();
    } catch (error) {
      showCaMessage("ca-certificate-error", error instanceof Error ? error.message : "The certificate request could not be added.");
      if (typeof cell.restoreOldValue === "function") {
        cell.restoreOldValue();
      }
    }
    return;
  }
  try {
    await postCaAction(`/certificate-authority/certificates/${data.id}/edit`, data, csrf, { reload: false });
    showTransientGridStatus("Saved");
  } catch (error) {
    showCaMessage("ca-certificate-error", error instanceof Error ? error.message : "The certificate request could not be saved.");
    if (typeof cell.restoreOldValue === "function") {
      cell.restoreOldValue();
    }
  }
}

async function deleteCaCertificateFromMenu(row, csrf) {
  clearCaMessage("ca-certificate-error");
  const data = row.getData();
  if (data.is_new) {
    return;
  }
  const confirmed = await requestConfirmation({
    title: `Delete ${data.common_name} certificate request?`,
    message: "This removes the certificate request from LabFoundry desired state. It will not touch the appliance until global appliance apply runs.",
    label: "Delete request",
  });
  if (!confirmed) {
    return;
  }
  try {
    await postCaAction(`/certificate-authority/certificates/${data.id}/delete`, {}, csrf);
  } catch (error) {
    showCaMessage("ca-certificate-error", error instanceof Error ? error.message : "The certificate request could not be deleted.");
  }
}

function initializeCaProfilesTable() {
  const tableElement = document.getElementById("ca-profiles-table");
  if (!(tableElement instanceof HTMLElement)) {
    return;
  }
  const fallback = document.getElementById(tableElement.dataset.fallbackId || "");
  if (typeof Tabulator === "undefined") {
    showCaMessage("ca-profile-error", "Tabulator did not load. Showing the fallback table.");
    return;
  }
  const csrf = tableElement.dataset.csrf || "";
  const rows = [...JSON.parse(tableElement.dataset.profiles || "[]"), newCaProfileRow()];
  try {
    new Tabulator(tableElement, {
      data: rows,
      index: "id",
      layout: "fitColumns",
      height: "360px",
      rowHeight: 28,
      placeholder: "No CA profiles configured.",
      reactiveData: false,
      rowContextMenu: [
        {
          label: "Delete profile",
          action: (event, row) => deleteCaProfileFromMenu(row, csrf),
        },
      ],
      columns: lockNewRecordColumns([
        {
          title: "Name",
          field: "name",
          editor: "input",
          formatter: (cell) => dnsAddRowHintFormatter(cell, "+ Add profile here"),
          minWidth: 170,
          cellEdited: (cell) => autoSaveCaProfile(cell, csrf),
        },
        {
          title: "Type",
          field: "certificate_type",
          editor: "list",
          editorParams: { values: { server: "server", client: "client", user: "user", intermediate: "intermediate" } },
          width: 130,
          cellEdited: (cell) => autoSaveCaProfile(cell, csrf),
        },
        {
          title: "Validity",
          field: "validity_days",
          editor: "number",
          width: 100,
          cellEdited: (cell) => autoSaveCaProfile(cell, csrf),
        },
        {
          title: "Key",
          field: "key_algorithm",
          editor: "list",
          editorParams: { values: { RSA: "RSA", ECDSA: "ECDSA" } },
          width: 90,
          cellEdited: (cell) => autoSaveCaProfile(cell, csrf),
        },
        {
          title: "Size",
          field: "key_size",
          editor: "number",
          width: 90,
          cellEdited: (cell) => autoSaveCaProfile(cell, csrf),
        },
        {
          title: "EKU",
          field: "extended_key_usage",
          editor: "input",
          minWidth: 160,
          cellEdited: (cell) => autoSaveCaProfile(cell, csrf),
        },
        {
          title: "SAN",
          field: "san_required",
          formatter: labFoundryBooleanFormatter,
          editor: "tickCross",
          hozAlign: "center",
          width: 80,
          headerSort: false,
          cellEdited: (cell) => autoSaveCaProfile(cell, csrf),
        },
        {
          title: "Enabled",
          field: "enabled",
          formatter: labFoundryBooleanFormatter,
          editor: "tickCross",
          hozAlign: "center",
          width: 100,
          headerSort: false,
          cellEdited: (cell) => autoSaveCaProfile(cell, csrf),
        },
        {
          title: "Description",
          field: "description",
          editor: "input",
          formatter: (cell) => dnsAddRowHintFormatter(cell, "optional note..."),
          minWidth: 220,
          cellEdited: (cell) => autoSaveCaProfile(cell, csrf),
        },
      ], "name"),
      rowFormatter: (row) => {
        markNewRecordRow(row, "name");
      },
    });
    if (fallback) {
      fallback.classList.add("hidden");
    }
  } catch (error) {
    showCaMessage("ca-profile-error", error instanceof Error ? error.message : "Tabulator could not render. Showing the fallback table.");
  }
}

function initializeCaCertificatesTable() {
  const tableElement = document.getElementById("ca-certificates-table");
  if (!(tableElement instanceof HTMLElement)) {
    return;
  }
  const fallback = document.getElementById(tableElement.dataset.fallbackId || "");
  if (typeof Tabulator === "undefined") {
    showCaMessage("ca-certificate-error", "Tabulator did not load. Showing the fallback table.");
    return;
  }
  const csrf = tableElement.dataset.csrf || "";
  const profileOptions = JSON.parse(tableElement.dataset.profileOptions || "[]");
  const profileValues = Object.fromEntries(profileOptions.map((item) => [item.id, item.label]));
  const defaultProfileId = profileOptions[0]?.id || "";
  const rows = [...JSON.parse(tableElement.dataset.certificates || "[]"), newCaCertificateRow(defaultProfileId)];
  try {
    new Tabulator(tableElement, {
      data: rows,
      index: "id",
      layout: "fitColumns",
      height: "420px",
      rowHeight: 28,
      placeholder: "No certificate requests configured.",
      reactiveData: false,
      rowContextMenu: [
        {
          label: "Delete request",
          action: (event, row) => deleteCaCertificateFromMenu(row, csrf),
        },
      ],
      columns: lockNewRecordColumns([
        {
          title: "Common name",
          field: "common_name",
          editor: "input",
          formatter: (cell) => dnsAddRowHintFormatter(cell, "+ Add certificate here"),
          minWidth: 210,
          cellEdited: (cell) => autoSaveCaCertificate(cell, csrf),
        },
        {
          title: "Owner",
          field: "managed_owner",
          formatter: (cell) => cell.getValue() || "manual",
          minWidth: 150,
          headerSort: true,
        },
        {
          title: "Profile",
          field: "profile_id",
          editor: "list",
          editorParams: { values: profileValues },
          formatter: (cell) => profileValues[cell.getValue()] || "Unassigned",
          minWidth: 160,
          cellEdited: (cell) => autoSaveCaCertificate(cell, csrf),
        },
        {
          title: "DNS SANs",
          field: "subject_alt_names",
          editor: "textarea",
          formatter: (cell) => dnsAddRowHintFormatter(cell, "DNS names..."),
          minWidth: 220,
          cellEdited: (cell) => autoSaveCaCertificate(cell, csrf),
        },
        {
          title: "IP SANs",
          field: "ip_addresses",
          editor: "textarea",
          formatter: (cell) => dnsAddRowHintFormatter(cell, "IP addresses..."),
          minWidth: 170,
          cellEdited: (cell) => autoSaveCaCertificate(cell, csrf),
        },
        {
          title: "Status",
          field: "status",
          editor: "list",
          editorParams: { values: { planned: "planned", "csr-staged": "csr-staged", issued: "issued", revoked: "revoked" } },
          width: 120,
          cellEdited: (cell) => autoSaveCaCertificate(cell, csrf),
        },
        {
          title: "Enabled",
          field: "enabled",
          formatter: labFoundryBooleanFormatter,
          editor: "tickCross",
          hozAlign: "center",
          width: 100,
          headerSort: false,
          cellEdited: (cell) => autoSaveCaCertificate(cell, csrf),
        },
        {
          title: "Fingerprint",
          field: "fingerprint",
          formatter: (cell) => {
            const value = cell.getValue() || "";
            return value ? `${value.slice(0, 12)}...` : "";
          },
          width: 120,
          headerSort: false,
        },
        {
          title: "Exports",
          field: "has_certificate",
          formatter: (cell) => {
            const data = cell.getRow().getData();
            if (data.is_new || !data.has_certificate) {
              return '<span class="muted">pending</span>';
            }
            const base = `/certificate-authority/certificates/${data.id}/downloads`;
            const privateLink = data.has_private_key ? ` <a class="button tiny ghost" href="${base}/private-key.pem">Key</a>` : "";
            return `<a class="button tiny secondary" href="${base}/certificate.pem">Cert</a> <a class="button tiny secondary" href="${base}/chain.pem">Chain</a>${privateLink}`;
          },
          minWidth: 190,
          headerSort: false,
        },
        {
          title: "Description",
          field: "description",
          editor: "input",
          formatter: (cell) => dnsAddRowHintFormatter(cell, "optional note..."),
          minWidth: 220,
          cellEdited: (cell) => autoSaveCaCertificate(cell, csrf),
        },
      ], "common_name"),
      rowFormatter: (row) => {
        markNewRecordRow(row, "common_name");
      },
    });
    if (fallback) {
      fallback.classList.add("hidden");
    }
  } catch (error) {
    showCaMessage("ca-certificate-error", error instanceof Error ? error.message : "Tabulator could not render. Showing the fallback table.");
  }
}

async function postKmsAction(url, data, csrf, options = {}) {
  const reload = options.reload ?? true;
  const body = new FormData();
  body.set("csrf", csrf);
  for (const [key, value] of Object.entries(data)) {
    if (key === "id" || key === "is_new" || key === "owner_client_name") {
      continue;
    }
    if (key === "enabled" || key === "exportable") {
      if (value) {
        body.set(key, "on");
      }
      continue;
    }
    body.set(key, value ?? "");
  }

  const response = await fetch(url, {
    method: "POST",
    body,
    credentials: "same-origin",
  });

  if (!response.ok) {
    const text = await response.text();
    throw new Error(text.match(/KMS .* already exists[^<]*/)?.[0] || "The KMS desired state could not be saved.");
  }
  if (reload) {
    window.location.reload();
  }
}

function newKmsClientRow() {
  return {
    id: "__new__",
    name: "",
    certificate_subject: "",
    role: "service",
    allowed_operations: "locate,get,register,create",
    enabled: true,
    description: "",
    is_new: true,
  };
}

function newKmsKeyRow(defaultClientId = "") {
  return {
    id: "__new__",
    name: "",
    algorithm: "AES",
    length: 256,
    usage: "encrypt,decrypt",
    state: "active",
    owner_client_id: defaultClientId,
    owner_client_name: "",
    exportable: false,
    enabled: true,
    description: "",
    is_new: true,
  };
}

function hasRequiredKmsClientFields(data) {
  return Boolean((data.name || "").trim() && (data.certificate_subject || "").trim());
}

function hasRequiredKmsKeyFields(data) {
  return Boolean((data.name || "").trim());
}

async function postFirewallRuleAction(url, data, csrf, options = {}) {
  const reload = options.reload ?? true;
  const body = new FormData();
  body.set("csrf", csrf);
  for (const [key, value] of Object.entries(data)) {
    if (["id", "is_new", "created_at", "updated_at"].includes(key)) {
      continue;
    }
    if (key === "enabled") {
      if (value) {
        body.set("enabled", "on");
      }
      continue;
    }
    body.set(key, value ?? "");
  }

  const response = await fetch(url, {
    method: "POST",
    body,
    credentials: "same-origin",
  });
  if (!response.ok) {
    const text = await response.text();
    const plainText = text.trim().replace(/<[^>]+>/g, " ").replace(/\s+/g, " ");
    throw new Error(plainText || "The firewall rule could not be saved.");
  }
  if (reload) {
    window.location.reload();
  }
}

function newFirewallRuleRow(defaultInterface = "") {
  return {
    id: "__new__",
    name: "",
    direction: "input",
    action: "accept",
    protocol: "tcp",
    source: "any",
    destination: "any",
    destination_port: "",
    interface_name: defaultInterface,
    priority: 100,
    enabled: true,
    description: "",
    is_new: true,
  };
}

function firewallGroupOptions(groups = []) {
  const options = { any: "Any" };
  groups.forEach((group) => {
    if (!group || !group.id || group.id === "any") {
      return;
    }
    options[`group:${group.id}`] = group.name || group.id;
  });
  return options;
}

function firewallGroupFormatter(groupOptions) {
  return (cell) => {
    const value = String(cell.getValue() || "any");
    return escapeHtml(groupOptions[value] || value);
  };
}

function hasRequiredFirewallRuleFields(data) {
  return Boolean((data.name || "").trim());
}

async function autoSaveFirewallRule(cell, csrf) {
  clearCaMessage("firewall-rule-error");
  const row = cell.getRow();
  const data = row.getData();
  if (data.is_new) {
    if (!hasRequiredFirewallRuleFields(data)) {
      reformatPendingNewRecord(cell);
      return;
    }
    try {
      await postFirewallRuleAction("/firewall/rules", data, csrf, { reload: false });
      showTransientGridStatus("Added");
      window.location.reload();
    } catch (error) {
      showCaMessage("firewall-rule-error", error instanceof Error ? error.message : "The firewall rule could not be added.");
      if (typeof cell.restoreOldValue === "function") {
        cell.restoreOldValue();
      }
    }
    return;
  }
  try {
    await postFirewallRuleAction(`/firewall/rules/${data.id}/edit`, data, csrf, { reload: false });
    showTransientGridStatus("Saved");
    await refreshNetworkSideStack();
  } catch (error) {
    showCaMessage("firewall-rule-error", error instanceof Error ? error.message : "The firewall rule could not be saved.");
    if (typeof cell.restoreOldValue === "function") {
      cell.restoreOldValue();
    }
  }
}

async function deleteFirewallRuleFromMenu(row, csrf) {
  clearCaMessage("firewall-rule-error");
  const data = row.getData();
  if (data.is_new) {
    return;
  }
  const confirmed = await requestConfirmation({
    title: `Delete ${data.name}?`,
    message: "This removes the firewall rule from LabFoundry desired state. It will not touch the appliance until global appliance apply runs.",
    label: "Delete rule",
  });
  if (!confirmed) {
    return;
  }
  try {
    await postFirewallRuleAction(`/firewall/rules/${data.id}/delete`, {}, csrf);
  } catch (error) {
    showCaMessage("firewall-rule-error", error instanceof Error ? error.message : "The firewall rule could not be deleted.");
  }
}

function initializeFirewallRulesTable() {
  const tableElement = document.getElementById("firewall-rules-table");
  if (!(tableElement instanceof HTMLElement)) {
    return;
  }
  const fallback = document.getElementById(tableElement.dataset.fallbackId || "");
  if (typeof Tabulator === "undefined") {
    showCaMessage("firewall-rule-error", "Tabulator did not load. Showing the fallback table.");
    return;
  }
  const csrf = tableElement.dataset.csrf || "";
  const directions = roleValues(JSON.parse(tableElement.dataset.directions || "[]"));
  const actions = roleValues(JSON.parse(tableElement.dataset.actions || "[]"));
  const protocols = roleValues(JSON.parse(tableElement.dataset.protocols || "[]"));
  const interfaces = JSON.parse(tableElement.dataset.interfaces || "[]");
  const groups = JSON.parse(tableElement.dataset.groups || "[]");
  const interfaceOptions = Object.fromEntries(["", ...interfaces].map((item) => [item, item || "any"]));
  const groupOptions = firewallGroupOptions(groups);
  const groupValueFormatter = firewallGroupFormatter(groupOptions);
  const rows = [...JSON.parse(tableElement.dataset.rules || "[]"), newFirewallRuleRow(interfaces[0] || "")];
  const tableHeight = `${Math.min(Math.max(rows.length * 28 + 34, 90), 240)}px`;
  try {
    new Tabulator(tableElement, {
      data: rows,
      index: "id",
      layout: "fitColumns",
      height: tableHeight,
      rowHeight: 28,
      placeholder: "No firewall rules configured.",
      reactiveData: false,
      rowContextMenu: [
        {
          label: "Delete rule",
          action: (_event, row) => deleteFirewallRuleFromMenu(row, csrf),
          disabled: (_component) => _component.getData().is_new,
        },
      ],
      columns: lockNewRecordColumns([
        {
          title: "Name",
          field: "name",
          editor: "input",
          formatter: (cell) => dnsAddRowHintFormatter(cell, "+ Add rule here"),
          cellEdited: (cell) => autoSaveFirewallRule(cell, csrf),
        },
        {
          title: "Direction",
          field: "direction",
          editor: "list",
          editorParams: { values: directions },
          width: 120,
          cellEdited: (cell) => autoSaveFirewallRule(cell, csrf),
        },
        {
          title: "Action",
          field: "action",
          editor: "list",
          editorParams: { values: actions },
          width: 105,
          cellEdited: (cell) => autoSaveFirewallRule(cell, csrf),
        },
        {
          title: "Protocol",
          field: "protocol",
          editor: "list",
          editorParams: { values: protocols },
          width: 110,
          cellEdited: (cell) => autoSaveFirewallRule(cell, csrf),
        },
        {
          title: "Source",
          field: "source",
          editor: "list",
          editorParams: { values: groupOptions },
          formatter: groupValueFormatter,
          cellEdited: (cell) => autoSaveFirewallRule(cell, csrf),
        },
        {
          title: "Destination",
          field: "destination",
          editor: "list",
          editorParams: { values: groupOptions },
          formatter: groupValueFormatter,
          cellEdited: (cell) => autoSaveFirewallRule(cell, csrf),
        },
        { title: "Ports", field: "destination_port", editor: "input", width: 120, cellEdited: (cell) => autoSaveFirewallRule(cell, csrf) },
        {
          title: "Interface",
          field: "interface_name",
          editor: "list",
          editorParams: { values: interfaceOptions },
          width: 120,
          cellEdited: (cell) => autoSaveFirewallRule(cell, csrf),
        },
        { title: "Priority", field: "priority", editor: "number", width: 100, cellEdited: (cell) => autoSaveFirewallRule(cell, csrf) },
        {
          title: "Enabled",
          field: "enabled",
          formatter: labFoundryBooleanFormatter,
          editor: "tickCross",
          hozAlign: "center",
          width: 95,
          cellEdited: (cell) => autoSaveFirewallRule(cell, csrf),
        },
        { title: "Description", field: "description", editor: "input", cellEdited: (cell) => autoSaveFirewallRule(cell, csrf) },
      ], "name"),
      rowFormatter: (row) => {
        markNewRecordRow(row, "name");
      },
    });
    if (fallback) {
      fallback.classList.add("hidden");
    }
  } catch (error) {
    showCaMessage("firewall-rule-error", error instanceof Error ? error.message : "Tabulator could not render. Showing the fallback table.");
  }
}

function managedFirewallStatusFormatter(cell) {
  const value = String(cell.getValue() || "managed");
  const style = value === "generated" ? "good" : value === "replaced" ? "warn" : "muted";
  return `<span class="status-pill ${style}">${escapeHtml(value)}</span>`;
}

async function updateManagedFirewallSourceGroup(cell, csrf) {
  const data = cell.getRow().getData();
  if (data.managed_state !== "generated") {
    if (typeof cell.restoreOldValue === "function") {
      cell.restoreOldValue();
    }
    return;
  }
  const body = new FormData();
  body.set("csrf", csrf);
  body.set("rule_name", data.name || "");
  body.set("source_group_id", data.source_group_id || "");
  const response = await fetch("/firewall/managed-rules/source-group", {
    method: "POST",
    body,
    credentials: "same-origin",
  });
  if (!response.ok) {
    if (typeof cell.restoreOldValue === "function") {
      cell.restoreOldValue();
    }
    const text = await response.text();
    showCaMessage("firewall-rule-error", text.trim().replace(/<[^>]+>/g, " ").replace(/\s+/g, " ") || "The managed firewall group could not be saved.");
    return;
  }
  showTransientGridStatus("Saved");
  window.location.reload();
}

function initializeManagedFirewallRulesTable() {
  const tableElement = document.getElementById("managed-firewall-rules-table");
  if (!(tableElement instanceof HTMLElement)) {
    return;
  }
  const fallback = document.getElementById(tableElement.dataset.fallbackId || "");
  if (typeof Tabulator === "undefined") {
    return;
  }
  const rows = JSON.parse(tableElement.dataset.rules || "[]");
  const csrf = tableElement.dataset.csrf || "";
  const sourceGroups = JSON.parse(tableElement.dataset.sourceGroups || "[]");
  const sourceGroupOptions = Object.fromEntries(sourceGroups.map((group) => [group.id, group.name]));
  try {
    new Tabulator(tableElement, {
      data: rows,
      index: "id",
      layout: "fitColumns",
      height: "100%",
      rowHeight: 28,
      placeholder: "No managed service rules.",
      reactiveData: false,
      columns: [
        { title: "Status", field: "managed_status", formatter: managedFirewallStatusFormatter, width: 120 },
        { title: "Name", field: "name" },
        {
          title: "Source",
          field: "source_group_id",
          editor: "list",
          editorParams: { values: sourceGroupOptions },
          formatter: (cell) => escapeHtml(cell.getRow().getData().source_group_name || cell.getValue() || ""),
          width: 170,
          cellEdited: (cell) => updateManagedFirewallSourceGroup(cell, csrf),
          editable: (cell) => cell.getRow().getData().managed_state === "generated" && Boolean(cell.getRow().getData().source_group_id),
        },
        { title: "Direction", field: "direction", width: 120 },
        { title: "Action", field: "action", width: 105 },
        { title: "Protocol", field: "protocol", width: 110 },
        { title: "Ports", field: "destination_port", width: 120 },
        { title: "Interface", field: "interface_name", width: 120 },
        { title: "Priority", field: "priority", width: 100 },
        { title: "Enabled", field: "enabled", formatter: labFoundryBooleanFormatter, hozAlign: "center", width: 95 },
        { title: "Description", field: "description" },
      ],
      rowFormatter: (row) => {
        const data = row.getData();
        row.getElement().classList.toggle("managed-rule-generated", data.managed_state === "generated");
        row.getElement().classList.toggle("managed-rule-replaced", data.managed_state === "replaced");
      },
    });
    if (fallback) {
      fallback.classList.add("hidden");
    }
  } catch (_error) {
    if (fallback) {
      fallback.classList.remove("hidden");
    }
  }
}

function serviceRuntimeFormatter(cell) {
  const running = Boolean(cell.getValue());
  const data = cell.getRow().getData() || {};
  if (!running && data.enabled === false) {
    return '<span class="service-state muted">disabled</span>';
  }
  return `<span class="service-state ${running ? "good" : "muted"}">${running ? "running" : "stopped"}</span>`;
}

function serviceNameFormatter(cell) {
  const data = cell.getRow().getData();
  return `<span class="service-name-cell"><strong>${escapeHtml(data.display_name)}</strong><small>${escapeHtml(data.service)}</small></span>`;
}

function submitServiceAction(service, action, csrf) {
  const form = document.createElement("form");
  form.method = "post";
  form.action = `/services/${encodeURIComponent(service)}/${encodeURIComponent(action)}`;
  const input = document.createElement("input");
  input.type = "hidden";
  input.name = "csrf";
  input.value = csrf;
  form.append(input);
  document.body.append(form);
  form.requestSubmit();
}

function autoToggleServiceEnabled(cell, csrf) {
  const data = cell.getRow().getData();
  submitServiceAction(data.service, data.enabled ? "enable" : "disable", csrf);
}

function initializeServicesTable() {
  const tableElement = document.getElementById("services-table");
  if (!(tableElement instanceof HTMLElement)) {
    return;
  }
  const fallback = document.getElementById(tableElement.dataset.fallbackId || "");
  if (typeof Tabulator === "undefined") {
    showCaMessage("services-error", "Tabulator did not load. Showing the fallback table.");
    return;
  }
  const csrf = tableElement.dataset.csrf || "";
  const rows = JSON.parse(tableElement.dataset.services || "[]");
  try {
    new Tabulator(tableElement, {
      data: rows,
      index: "id",
      layout: "fitColumns",
      height: "100%",
      rowHeight: 42,
      placeholder: "No allowlisted services configured.",
      reactiveData: false,
      rowContextMenu: [
        {
          label: "Start",
          action: (_event, row) => submitServiceAction(row.getData().service, "start", csrf),
        },
        {
          label: "Stop",
          action: (_event, row) => submitServiceAction(row.getData().service, "stop", csrf),
        },
        {
          label: "Restart",
          action: (_event, row) => submitServiceAction(row.getData().service, "restart", csrf),
        },
        {
          label: "Enable",
          action: (_event, row) => submitServiceAction(row.getData().service, "enable", csrf),
          disabled: (component) => component.getData().enabled,
        },
        {
          label: "Disable",
          action: (_event, row) => submitServiceAction(row.getData().service, "disable", csrf),
          disabled: (component) => !component.getData().enabled,
        },
        {
          label: "Open logs",
          action: (_event, row) => {
            window.location.href = `/services/${encodeURIComponent(row.getData().service)}/logs`;
          },
        },
        {
          label: "Check Chrony source health",
          action: () => openChronySourceHealthModal(),
          disabled: (component) => component.getData().service !== "chronyd",
        },
      ],
      columns: [
        {
          title: "Service",
          field: "display_name",
          formatter: serviceNameFormatter,
          minWidth: 310,
        },
        {
          title: "Runtime",
          field: "running",
          formatter: serviceRuntimeFormatter,
          width: 125,
          hozAlign: "center",
        },
        {
          title: "Startup",
          field: "enabled",
          formatter: labFoundryBooleanFormatter,
          editor: "tickCross",
          width: 125,
          hozAlign: "center",
          cellEdited: (cell) => autoToggleServiceEnabled(cell, csrf),
        },
        {
          title: "Boundary",
          field: "detail",
          formatter: (cell) => escapeHtml(cell.getValue() || "native host service"),
          minWidth: 260,
        },
      ],
    });
    if (fallback) {
      fallback.classList.add("hidden");
    }
  } catch (error) {
    showCaMessage("services-error", error instanceof Error ? error.message : "Tabulator could not render. Showing the fallback table.");
  }
}

async function postUserAction(url, data, csrf, options = {}) {
  const reload = options.reload ?? true;
  const body = new FormData();
  body.set("csrf", csrf);
  for (const [key, value] of Object.entries(data)) {
    if (
      [
        "id",
        "is_new",
        "is_current",
        "created_at",
        "enabled",
        "os_sync_status",
        "os_password_pending",
        "os_account_state",
        "os_account_detail",
        "os_unlock_available",
        "unlock_requested",
      ].includes(key)
    ) {
      continue;
    }
    if (Array.isArray(value)) {
      value.forEach((item) => body.append(key, item ?? ""));
    } else {
      body.set(key, value ?? "");
    }
  }

  const response = await fetch(url, {
    method: "POST",
    body,
    credentials: "same-origin",
  });

  if (!response.ok) {
    const text = await response.text();
    const plainText = text.trim().replace(/<[^>]+>/g, " ").replace(/\s+/g, " ");
    throw new Error(plainText || "The user could not be saved.");
  }
  if (reload) {
    window.location.reload();
  }
}

function openUserPasswordModal(data) {
  const modal = document.getElementById("user-password-modal");
  const form = document.getElementById("user-password-form");
  const title = document.getElementById("user-password-modal-title");
  const message = document.getElementById("user-password-modal-message");
  if (!(modal instanceof HTMLDialogElement) || !(form instanceof HTMLFormElement)) {
    return;
  }
  form.action = `/users/${data.id}/password`;
  form.reset();
  form.querySelectorAll('input[type="text"][data-password-visible]').forEach((input) => {
    if (input instanceof HTMLInputElement) {
      input.type = "password";
      input.removeAttribute("data-password-visible");
    }
  });
  form.querySelectorAll("[data-password-toggle]").forEach((button) => {
    if (button instanceof HTMLButtonElement) {
      button.setAttribute("aria-pressed", "false");
      button.setAttribute("aria-label", button.getAttribute("aria-label")?.replace("Hide", "Show") || "Show password");
    }
  });
  form.querySelectorAll("input").forEach((input) => {
    if (input instanceof HTMLInputElement) {
      input.setCustomValidity("");
    }
  });
  if (title instanceof HTMLElement) {
    title.textContent = `Reset ${data.username} password`;
  }
  if (message instanceof HTMLElement) {
    message.textContent = "Set/reset the Photon OS password. The password is held only until global Local Users apply.";
  }
  modal.showModal();
  const passwordInput = form.querySelector('input[name="password"]');
  if (passwordInput instanceof HTMLInputElement) {
    passwordInput.focus();
  }
}

async function deleteUserFromMenu(row, csrf) {
  clearCaMessage("users-error");
  const data = row.getData();
  if (data.is_new) {
    return;
  }
  const confirmed = await requestConfirmation({
    title: `Remove ${data.username}?`,
    message: "This removes the local LabFoundry account, revokes its API tokens, and removes the managed Photon OS account on the next global appliance apply.",
    label: "Remove user",
  });
  if (!confirmed) {
    return;
  }
  try {
    await postUserAction(`/users/${data.id}/delete`, {}, csrf);
  } catch (error) {
    showCaMessage("users-error", error instanceof Error ? error.message : "The user could not be removed.");
  }
}

async function unlockUserFromMenu(row, csrf) {
  clearCaMessage("users-error");
  const data = row.getData();
  if (data.is_new) {
    return;
  }
  try {
    await postUserAction(`/users/${data.id}/unlock`, {}, csrf, { reload: false });
    showTransientGridStatus("Unlock pending");
    window.location.reload();
  } catch (error) {
    showCaMessage("users-error", error instanceof Error ? error.message : "The unlock request could not be staged.");
  }
}

async function disableUserFromMenu(row, csrf) {
  clearCaMessage("users-error");
  const data = row.getData();
  if (data.is_new || !data.enabled) {
    return;
  }
  const confirmed = await requestConfirmation({
    title: `Disable ${data.username}?`,
    message: "This marks the LabFoundry user disabled, revokes its API tokens, and removes the managed Photon OS account on the next global appliance apply.",
    label: "Disable user",
  });
  if (!confirmed) {
    return;
  }
  try {
    await postUserAction(`/users/${data.id}/disable`, {}, csrf, { reload: false });
    showTransientGridStatus("Disabled");
    window.location.reload();
  } catch (error) {
    showCaMessage("users-error", error instanceof Error ? error.message : "The user could not be disabled.");
  }
}

function newUserRow() {
  return {
    id: "__new__",
    username: "",
    role: "viewer",
    roles: ["viewer"],
    roles_label: "viewer",
    roles_text: "viewer",
    shell: "/sbin/nologin",
    enabled: false,
    created_at: "",
    os_sync_status: "password not staged; reset to sync",
    os_password_pending: false,
    os_account_state: "absent",
    os_account_detail: "",
    os_unlock_available: false,
    unlock_requested: false,
    is_current: false,
    is_new: true,
  };
}

function normalizeUserRoleSelection(value, allowedRoles) {
  const rawValues = Array.isArray(value) ? value : String(value || "").split(",");
  const selected = rawValues
    .map((item) => String(item || "").trim())
    .filter((item, index, values) => allowedRoles.includes(item) && values.indexOf(item) === index);
  return selected.length ? selected : ["viewer"];
}

function userRolesFormatter(cell) {
  const data = cell.getRow().getData();
  const roles = Array.isArray(data.roles) && data.roles.length ? data.roles : String(data.roles_text || data.role || "viewer").split(",");
  return escapeHtml(roles.map((role) => String(role).trim()).filter(Boolean).join(", ") || "viewer");
}

function syncUserRoleFields(row, roles) {
  const selectedRoles = Array.isArray(roles) && roles.length ? roles : ["viewer"];
  const roleText = selectedRoles.join(", ");
  const data = row.getData();
  data.role = selectedRoles[0] || "viewer";
  data.roles = selectedRoles;
  data.roles_label = roleText;
  data.roles_text = roleText;
  row.update({
    role: data.role,
    roles: data.roles,
    roles_label: roleText,
    roles_text: roleText,
  });
}

function hasRequiredUserFields(data) {
  return Boolean((data.username || "").trim());
}

async function autoSaveUser(cell, csrf) {
  clearCaMessage("users-error");
  const row = cell.getRow();
  const data = row.getData();
  if (data.is_new) {
    if (!hasRequiredUserFields(data)) {
      return;
    }
    try {
      await postUserAction("/users", data, csrf, { reload: false });
      showTransientGridStatus("Added");
      window.location.reload();
    } catch (error) {
      showCaMessage("users-error", error instanceof Error ? error.message : "The user could not be added.");
      if (typeof cell.restoreOldValue === "function") {
        cell.restoreOldValue();
      }
    }
    return;
  }
  try {
    await postUserAction(`/users/${data.id}/edit`, data, csrf, { reload: false });
    showTransientGridStatus("Saved");
  } catch (error) {
    showCaMessage("users-error", error instanceof Error ? error.message : "The user could not be saved.");
    if (typeof cell.restoreOldValue === "function") {
      cell.restoreOldValue();
    }
  }
}

function initializeUsersTable() {
  const tableElement = document.getElementById("users-table");
  if (!(tableElement instanceof HTMLElement)) {
    return;
  }
  const fallback = document.getElementById(tableElement.dataset.fallbackId || "");
  if (typeof Tabulator === "undefined") {
    showCaMessage("users-error", "Tabulator did not load. Showing the fallback table.");
    return;
  }
  const csrf = tableElement.dataset.csrf || "";
  const shells = JSON.parse(tableElement.dataset.shells || '["/sbin/nologin","/bin/bash","/bin/sh"]');
  const roles = JSON.parse(tableElement.dataset.roles || '["viewer"]');
  const roleOptions = roleValues(roles);
  const rows = [...JSON.parse(tableElement.dataset.users || "[]"), newUserRow()];
  try {
    new Tabulator(tableElement, {
      data: rows,
      index: "id",
      layout: "fitColumns",
      height: "420px",
      rowHeight: 28,
      placeholder: "No local users configured.",
      reactiveData: false,
      rowContextMenu: [
        {
          label: "Set/reset Photon OS password",
          action: (_event, row) => openUserPasswordModal(row.getData()),
          disabled: (component) => component.getData().is_new,
        },
        {
          label: "Unlock OS account",
          action: (_event, row) => unlockUserFromMenu(row, csrf),
          disabled: (component) => {
            const data = component.getData();
            return data.is_new || !data.enabled || !data.os_unlock_available || data.unlock_requested;
          },
        },
        {
          label: "Disable user",
          action: (_event, row) => disableUserFromMenu(row, csrf),
          disabled: (component) => {
            const data = component.getData();
            return data.is_new || data.is_current || !data.enabled;
          },
        },
        {
          label: "Remove user",
          action: (_event, row) => deleteUserFromMenu(row, csrf),
          disabled: (component) => component.getData().is_new || component.getData().is_current,
        },
      ],
      columns: lockNewRecordColumns([
        {
          title: "Username",
          field: "username",
          editor: "input",
          formatter: (cell) => dnsAddRowHintFormatter(cell, "+ Add user here"),
          cellEdited: (cell) => autoSaveUser(cell, csrf),
        },
        {
          title: "Roles",
          field: "roles",
          editor: "list",
          editorParams: { values: roleOptions, multiselect: true },
          formatter: userRolesFormatter,
          cellEdited: (cell) => {
            const selectedRoles = normalizeUserRoleSelection(cell.getValue(), roles);
            syncUserRoleFields(cell.getRow(), selectedRoles);
            autoSaveUser(cell, csrf);
          },
          minWidth: 190,
        },
        {
          title: "Shell",
          field: "shell",
          editor: "list",
          editorParams: { values: shells },
          minWidth: 145,
          cellEdited: (cell) => autoSaveUser(cell, csrf),
        },
        {
          title: "Enabled",
          field: "enabled",
          formatter: labFoundryBooleanFormatter,
          hozAlign: "center",
          width: 110,
        },
        {
          title: "OS account",
          field: "os_account_state",
          formatter: (cell) => {
            const value = String(cell.getValue() || "");
            const data = cell.getRow().getData();
            const pill = value === "present" ? "good" : ["locked", "faillock blocked", "password not set"].includes(value) ? "warn" : "muted";
            const pending = data.unlock_requested ? ' <span class="status-pill warn">unlock pending</span>' : "";
            const title = data.os_account_detail ? ` title="${escapeHtml(data.os_account_detail)}"` : "";
            return `<span class="status-pill ${pill}"${title}>${escapeHtml(value)}</span>${pending}`;
          },
          minWidth: 190,
        },
        { title: "Created", field: "created_at", width: 120 },
        {
          title: "Session",
          field: "is_current",
          formatter: (cell) => (cell.getValue() ? '<span class="status-pill good">current</span>' : ""),
          width: 110,
        },
      ], "username"),
      rowFormatter: (row) => {
        markNewRecordRow(row, "username");
      },
    });
    if (fallback) {
      fallback.classList.add("hidden");
    }
  } catch (error) {
    showCaMessage("users-error", error instanceof Error ? error.message : "Tabulator could not render. Showing the fallback table.");
  }
}

function initializeUserPasswordForm() {
  const modal = document.getElementById("user-password-modal");
  const form = document.getElementById("user-password-form");
  const cancel = document.querySelector("[data-user-password-cancel]");
  document.querySelectorAll("[data-reset-user-button]").forEach((button) => {
    if (!(button instanceof HTMLButtonElement)) {
      return;
    }
    button.addEventListener("click", () => {
      openUserPasswordModal({ id: button.dataset.userId, username: button.dataset.username || "user" });
    });
  });
  if (cancel instanceof HTMLButtonElement && modal instanceof HTMLDialogElement) {
    cancel.addEventListener("click", () => modal.close("cancel"));
  }
  if (!(form instanceof HTMLFormElement)) {
    return;
  }
  const password = form.querySelector('input[name="password"]');
  const confirmation = form.querySelector('input[name="confirm_password"]');
  const validatePasswordMatch = (report = false) => {
    if (!(password instanceof HTMLInputElement) || !(confirmation instanceof HTMLInputElement)) {
      return true;
    }
    if (!password.value || !confirmation.value || password.value === confirmation.value) {
      confirmation.setCustomValidity("");
      return true;
    }
    confirmation.setCustomValidity("Password confirmation does not match.");
    if (report) {
      confirmation.reportValidity();
    }
    return false;
  };
  [password, confirmation].forEach((input) => {
    if (!(input instanceof HTMLInputElement)) {
      return;
    }
    input.addEventListener("input", () => validatePasswordMatch(false));
  });
  form.querySelectorAll("[data-password-toggle]").forEach((button) => {
    if (!(button instanceof HTMLButtonElement)) {
      return;
    }
    const input = button.closest(".password-input-wrap")?.querySelector("input");
    if (!(input instanceof HTMLInputElement)) {
      return;
    }
    button.addEventListener("click", () => {
      const nextVisible = input.type === "password";
      input.type = nextVisible ? "text" : "password";
      input.toggleAttribute("data-password-visible", nextVisible);
      button.setAttribute("aria-pressed", nextVisible ? "true" : "false");
      button.setAttribute("aria-label", `${nextVisible ? "Hide" : "Show"} ${input.name === "confirm_password" ? "confirmation password" : "new password"}`);
      input.focus();
    });
  });
  form.addEventListener("submit", (event) => {
    if (!validatePasswordMatch(true)) {
      event.preventDefault();
    }
  });
}

async function autoSaveKmsClient(cell, csrf) {
  clearCaMessage("kms-client-error");
  const row = cell.getRow();
  const data = row.getData();
  if (data.is_new) {
    if (!hasRequiredKmsClientFields(data)) {
      return;
    }
    try {
      await postKmsAction("/kms/clients", data, csrf, { reload: false });
      showTransientGridStatus("Added");
      window.location.reload();
    } catch (error) {
      showCaMessage("kms-client-error", error instanceof Error ? error.message : "The KMS client could not be added.");
      if (typeof cell.restoreOldValue === "function") {
        cell.restoreOldValue();
      }
    }
    return;
  }
  try {
    await postKmsAction(`/kms/clients/${data.id}/edit`, data, csrf, { reload: false });
    showTransientGridStatus("Saved");
  } catch (error) {
    showCaMessage("kms-client-error", error instanceof Error ? error.message : "The KMS client could not be saved.");
    if (typeof cell.restoreOldValue === "function") {
      cell.restoreOldValue();
    }
  }
}

async function autoSaveKmsKey(cell, csrf) {
  clearCaMessage("kms-key-error");
  const row = cell.getRow();
  const data = row.getData();
  if (data.is_new) {
    if (!hasRequiredKmsKeyFields(data)) {
      reformatPendingNewRecord(cell);
      return;
    }
    try {
      await postKmsAction("/kms/keys", data, csrf, { reload: false });
      showTransientGridStatus("Added");
      window.location.reload();
    } catch (error) {
      showCaMessage("kms-key-error", error instanceof Error ? error.message : "The KMS key could not be added.");
      if (typeof cell.restoreOldValue === "function") {
        cell.restoreOldValue();
      }
    }
    return;
  }
  try {
    await postKmsAction(`/kms/keys/${data.id}/edit`, data, csrf, { reload: false });
    showTransientGridStatus("Saved");
  } catch (error) {
    showCaMessage("kms-key-error", error instanceof Error ? error.message : "The KMS key could not be saved.");
    if (typeof cell.restoreOldValue === "function") {
      cell.restoreOldValue();
    }
  }
}

async function deleteKmsClientFromMenu(row, csrf) {
  clearCaMessage("kms-client-error");
  const data = row.getData();
  if (data.is_new) {
    return;
  }
  const confirmed = await requestConfirmation({
    title: `Delete ${data.name} client?`,
    message: "This removes the KMS client from LabFoundry desired state and unassigns any keys owned by it. It will not touch the appliance until global appliance apply runs.",
    label: "Delete client",
  });
  if (!confirmed) {
    return;
  }
  try {
    await postKmsAction(`/kms/clients/${data.id}/delete`, {}, csrf);
  } catch (error) {
    showCaMessage("kms-client-error", error instanceof Error ? error.message : "The KMS client could not be deleted.");
  }
}

async function deleteKmsKeyFromMenu(row, csrf) {
  clearCaMessage("kms-key-error");
  const data = row.getData();
  if (data.is_new) {
    return;
  }
  const confirmed = await requestConfirmation({
    title: `Delete ${data.name} key?`,
    message: "This removes the KMS key from LabFoundry desired state. It will not touch the appliance until global appliance apply runs.",
    label: "Delete key",
  });
  if (!confirmed) {
    return;
  }
  try {
    await postKmsAction(`/kms/keys/${data.id}/delete`, {}, csrf);
  } catch (error) {
    showCaMessage("kms-key-error", error instanceof Error ? error.message : "The KMS key could not be deleted.");
  }
}

function initializeKmsClientsTable() {
  const tableElement = document.getElementById("kms-clients-table");
  if (!(tableElement instanceof HTMLElement)) {
    return;
  }
  const fallback = document.getElementById(tableElement.dataset.fallbackId || "");
  if (typeof Tabulator === "undefined") {
    showCaMessage("kms-client-error", "Tabulator did not load. Showing the fallback table.");
    return;
  }
  const csrf = tableElement.dataset.csrf || "";
  const roleValuesMap = roleValues(JSON.parse(tableElement.dataset.roleOptions || "[]"));
  const rows = [...JSON.parse(tableElement.dataset.clients || "[]"), newKmsClientRow()];
  try {
    new Tabulator(tableElement, {
      data: rows,
      index: "id",
      layout: "fitColumns",
      height: "420px",
      rowHeight: 28,
      placeholder: "No KMIP clients configured.",
      reactiveData: false,
      rowContextMenu: [
        {
          label: "Delete client",
          action: (event, row) => deleteKmsClientFromMenu(row, csrf),
        },
      ],
      columns: lockNewRecordColumns([
        {
          title: "Name",
          field: "name",
          editor: "input",
          formatter: (cell) => dnsAddRowHintFormatter(cell, "+ Add client here"),
          minWidth: 170,
          cellEdited: (cell) => autoSaveKmsClient(cell, csrf),
        },
        {
          title: "Certificate subject",
          field: "certificate_subject",
          editor: "input",
          formatter: (cell) => dnsAddRowHintFormatter(cell, "CN=client,O=LabFoundry"),
          minWidth: 300,
          cellEdited: (cell) => autoSaveKmsClient(cell, csrf),
        },
        {
          title: "Role",
          field: "role",
          editor: "list",
          editorParams: { values: roleValuesMap },
          width: 120,
          cellEdited: (cell) => autoSaveKmsClient(cell, csrf),
        },
        {
          title: "Operations",
          field: "allowed_operations",
          editor: "input",
          formatter: (cell) => dnsAddRowHintFormatter(cell, "locate,get,register,create"),
          minWidth: 220,
          cellEdited: (cell) => autoSaveKmsClient(cell, csrf),
        },
        {
          title: "Enabled",
          field: "enabled",
          formatter: labFoundryBooleanFormatter,
          editor: "tickCross",
          hozAlign: "center",
          width: 100,
          headerSort: false,
          cellEdited: (cell) => autoSaveKmsClient(cell, csrf),
        },
        {
          title: "Description",
          field: "description",
          editor: "input",
          formatter: (cell) => dnsAddRowHintFormatter(cell, "optional note..."),
          minWidth: 220,
          cellEdited: (cell) => autoSaveKmsClient(cell, csrf),
        },
      ], "name"),
      rowFormatter: (row) => {
        markNewRecordRow(row, "name");
      },
    });
    if (fallback) {
      fallback.classList.add("hidden");
    }
  } catch (error) {
    showCaMessage("kms-client-error", error instanceof Error ? error.message : "Tabulator could not render. Showing the fallback table.");
  }
}

function initializeKmsKeysTable() {
  const tableElement = document.getElementById("kms-keys-table");
  if (!(tableElement instanceof HTMLElement)) {
    return;
  }
  const fallback = document.getElementById(tableElement.dataset.fallbackId || "");
  if (typeof Tabulator === "undefined") {
    showCaMessage("kms-key-error", "Tabulator did not load. Showing the fallback table.");
    return;
  }
  const csrf = tableElement.dataset.csrf || "";
  const algorithmValues = roleValues(JSON.parse(tableElement.dataset.algorithmOptions || "[]"));
  const stateValues = roleValues(JSON.parse(tableElement.dataset.stateOptions || "[]"));
  const clientOptions = JSON.parse(tableElement.dataset.clientOptions || "[]");
  const clientValues = { "": "Unassigned", ...Object.fromEntries(clientOptions.map((item) => [item.id, item.label])) };
  const defaultClientId = clientOptions[0]?.id || "";
  const rows = [...JSON.parse(tableElement.dataset.keys || "[]"), newKmsKeyRow(defaultClientId)];
  try {
    new Tabulator(tableElement, {
      data: rows,
      index: "id",
      layout: "fitColumns",
      height: "420px",
      rowHeight: 28,
      placeholder: "No KMS keys configured.",
      reactiveData: false,
      rowContextMenu: [
        {
          label: "Delete key",
          action: (event, row) => deleteKmsKeyFromMenu(row, csrf),
        },
      ],
      columns: lockNewRecordColumns([
        {
          title: "Name",
          field: "name",
          editor: "input",
          formatter: (cell) => dnsAddRowHintFormatter(cell, "+ Add key here"),
          minWidth: 190,
          cellEdited: (cell) => autoSaveKmsKey(cell, csrf),
        },
        {
          title: "Algorithm",
          field: "algorithm",
          editor: "list",
          editorParams: { values: algorithmValues },
          width: 120,
          cellEdited: (cell) => autoSaveKmsKey(cell, csrf),
        },
        {
          title: "Length",
          field: "length",
          editor: "number",
          width: 95,
          cellEdited: (cell) => autoSaveKmsKey(cell, csrf),
        },
        {
          title: "Usage",
          field: "usage",
          editor: "input",
          formatter: (cell) => dnsAddRowHintFormatter(cell, "encrypt,decrypt"),
          minWidth: 150,
          cellEdited: (cell) => autoSaveKmsKey(cell, csrf),
        },
        {
          title: "State",
          field: "state",
          editor: "list",
          editorParams: { values: stateValues },
          minWidth: 140,
          cellEdited: (cell) => autoSaveKmsKey(cell, csrf),
        },
        {
          title: "Owner client",
          field: "owner_client_id",
          editor: "list",
          editorParams: { values: clientValues },
          formatter: (cell) => clientValues[cell.getValue()] || "Unassigned",
          minWidth: 170,
          cellEdited: (cell) => autoSaveKmsKey(cell, csrf),
        },
        {
          title: "Exportable",
          field: "exportable",
          formatter: labFoundryBooleanFormatter,
          editor: "tickCross",
          hozAlign: "center",
          width: 110,
          headerSort: false,
          cellEdited: (cell) => autoSaveKmsKey(cell, csrf),
        },
        {
          title: "Enabled",
          field: "enabled",
          formatter: labFoundryBooleanFormatter,
          editor: "tickCross",
          hozAlign: "center",
          width: 100,
          headerSort: false,
          cellEdited: (cell) => autoSaveKmsKey(cell, csrf),
        },
        {
          title: "Description",
          field: "description",
          editor: "input",
          formatter: (cell) => dnsAddRowHintFormatter(cell, "optional note..."),
          minWidth: 220,
          cellEdited: (cell) => autoSaveKmsKey(cell, csrf),
        },
      ], "name"),
      rowFormatter: (row) => {
        markNewRecordRow(row, "name");
      },
    });
    if (fallback) {
      fallback.classList.add("hidden");
    }
  } catch (error) {
    showCaMessage("kms-key-error", error instanceof Error ? error.message : "Tabulator could not render. Showing the fallback table.");
  }
}

function updateCaSettingsPreview(payload = {}) {
  const configPreview = document.querySelector("[data-ca-config-preview]");
  if (configPreview instanceof HTMLElement && payload.config_preview) {
    configPreview.textContent = payload.config_preview;
    highlightConfigPreviewElement(configPreview);
  }
}

function initializeCaSettings() {
  document.querySelectorAll("[data-ca-settings]").forEach((form) => {
    if (!(form instanceof HTMLFormElement)) {
      return;
    }
    form.addEventListener("labfoundry:autosave-success", (event) => {
      updateCaSettingsPreview(event.detail || {});
    });
  });
}

function serviceBindSelection(form, payload = {}) {
  const interfaceEditor = form.querySelector(".tag-editor[data-service-bind-interface]");
  const addressEditor = form.querySelector(".tag-editor[data-service-bind-address]");
  const interfaces = Array.isArray(payload.listen_interfaces) ? payload.listen_interfaces : tagEditorValues(interfaceEditor);
  let addresses = Array.isArray(payload.listen_addresses) ? payload.listen_addresses : tagEditorValues(addressEditor);
  if (!addresses.length && interfaceEditor instanceof HTMLElement) {
    const options = Array.from(interfaceEditor.querySelectorAll("[data-tag-option]"));
    interfaces.forEach((interfaceName) => {
      const match = options.find((option) => option.getAttribute("data-tag-option") === interfaceName);
      const rawAddresses = match?.getAttribute("data-service-bind-addresses") || match?.getAttribute("data-service-bind-address") || "";
      rawAddresses
        .split(",")
        .map((value) => value.trim())
        .filter(Boolean)
        .forEach((address) => {
          if (!addresses.includes(address)) {
            addresses.push(address);
          }
        });
    });
  }
  const interfaceName = payload.listen_interface || interfaces[0] || "";
  const address = payload.listen_address || addresses[0] || "";
  return {
    interfaceName,
    address,
    interfaces,
    addresses,
    interfaceLabel: interfaces.length ? interfaces.join(", ") : interfaceName,
    addressLabel: addresses.length ? addresses.join(", ") : address,
  };
}

function updateDerivedListenAddressSummary(form, payload = {}) {
  if (!(form instanceof HTMLElement) || !Array.isArray(payload.listen_addresses)) {
    return;
  }
  const label = payload.listen_addresses.length ? payload.listen_addresses.join(", ") : "No interface address selected";
  form.querySelectorAll("[data-derived-listen-addresses]").forEach((element) => {
    if (element instanceof HTMLElement) {
      element.textContent = label;
    }
  });
}

function updateKmsDerivedAddress(form, payload = {}) {
  const portInput = form.querySelector('input[name="port"]');
  const port = payload.port || portInput?.value || "5696";
  const configPath = document.querySelector("[data-kms-config-path]");
  if (configPath instanceof HTMLElement && payload.config_path) {
    configPath.textContent = payload.config_path;
  }
  const configPreview = document.querySelector("[data-kms-config-preview]");
  if (configPreview instanceof HTMLElement && payload.config_preview) {
    configPreview.textContent = payload.config_preview;
    highlightConfigPreviewElement(configPreview);
  }
  const hostInput = form.querySelector('input[name="hostname"]');
  if (hostInput instanceof HTMLInputElement && payload.hostname) {
    hostInput.value = payload.hostname;
  }
  const certInput = form.querySelector('input[name="server_certificate"]');
  if (certInput instanceof HTMLInputElement && payload.server_certificate) {
    certInput.value = payload.server_certificate;
  }
  const portField = form.querySelector('input[name="port"]');
  if (portField instanceof HTMLInputElement && payload.port) {
    portField.value = String(port);
  }
}

function updateKmsValidation(payload = {}) {
  const status = document.querySelector("[data-kms-validation-status]");
  const validationPanel = status?.closest(".panel");
  if (!(status instanceof HTMLElement) || !(validationPanel instanceof HTMLElement)) {
    return;
  }
  const errors = Array.isArray(payload.validation_errors) ? payload.validation_errors : [];
  status.textContent = errors.length ? "needs attention" : "valid";
  status.classList.toggle("good", errors.length === 0);
  status.classList.toggle("warn", errors.length > 0);
  const previewAnchor = validationPanel.querySelector("[data-config-preview-row]");
  let errorBox = validationPanel.querySelector("[data-kms-validation-errors]");
  const validMessage = validationPanel.querySelector("[data-kms-validation-message]");
  if (errors.length === 0) {
    errorBox?.remove();
    if (!(validMessage instanceof HTMLElement)) {
      const message = document.createElement("p");
      message.className = "muted";
      message.setAttribute("data-kms-validation-message", "");
      message.textContent = "The desired KMS state passes LabFoundry validation. Appliance validation still runs through the allowlisted KMS helper before PyKMIP changes are applied.";
      validationPanel.insertBefore(message, previewAnchor);
    }
    return;
  }
  validMessage?.remove();
  if (!(errorBox instanceof HTMLElement)) {
    errorBox = document.createElement("div");
    errorBox.className = "alert error";
    errorBox.setAttribute("data-kms-validation-errors", "");
    validationPanel.insertBefore(errorBox, previewAnchor);
  }
  if (errorBox instanceof HTMLElement) {
    errorBox.innerHTML = "";
    errors.forEach((error) => {
      const row = document.createElement("div");
      row.textContent = error;
      errorBox.appendChild(row);
    });
  }
}

function initializeKmsSettings() {
  document.querySelectorAll("[data-kms-settings]").forEach((form) => {
    if (!(form instanceof HTMLFormElement)) {
      return;
    }
    const portInput = form.querySelector('input[name="port"]');
    const refresh = () => updateKmsDerivedAddress(form);
    if (portInput instanceof HTMLInputElement) {
      portInput.addEventListener("input", refresh);
    }
    form.addEventListener("labfoundry:autosave-success", (event) => {
      const payload = event.detail || {};
      updateKmsDerivedAddress(form, payload);
      updateKmsValidation(payload);
    });
    refresh();
  });
}

function chronyBlankUpstreamRow() {
  return {
    id: `new-${Date.now()}-${Math.random().toString(16).slice(2)}`,
    source: "",
    enabled: false,
    use_nts: false,
    maxdelay: "",
    description: "",
    is_new: true,
  };
}

function chronyUpstreamRowHasSource(cell) {
  return Boolean(String(cell.getRow().getData().source || "").trim());
}

function chronyGuardedTickFormatter(cell) {
  if (!chronyUpstreamRowHasSource(cell)) {
    return "";
  }
  return labFoundryBooleanFormatter(cell);
}

function chronyGuardedTextFormatter(cell) {
  if (!chronyUpstreamRowHasSource(cell)) {
    return "";
  }
  return escapeHtml(cell.getValue() || "");
}

function normalizeChronyUpstreamRows(rows = []) {
  return rows
    .map((row, index) => ({
      id: row.id || `source-${index + 1}`,
      source: String(row.source || "").trim(),
      enabled: row.enabled !== false,
      use_nts: Boolean(row.use_nts),
      maxdelay: String(row.maxdelay || "").trim(),
      description: String(row.description || "").trim(),
    }))
    .filter((row) => row.source);
}

function syncChronyUpstreamsHiddenInput(table) {
  const hiddenInput = document.querySelector("[data-chrony-upstreams-json]");
  if (!(hiddenInput instanceof HTMLInputElement)) {
    return;
  }
  hiddenInput.value = JSON.stringify(normalizeChronyUpstreamRows(table.getData()));
}

function ensureChronyUpstreamAddRow(table) {
  const rows = table.getData();
  const hasBlankRow = rows.some((row) => row.is_new && !String(row.source || "").trim());
  if (!hasBlankRow) {
    table.addRow(chronyBlankUpstreamRow(), false);
  }
}

function initializeChronyUpstreamsTable() {
  const tableElement = document.getElementById("chrony-upstreams-table");
  if (!(tableElement instanceof HTMLElement)) {
    return;
  }
  const fallback = document.getElementById(tableElement.dataset.fallbackId || "");
  const hiddenInput = document.querySelector("[data-chrony-upstreams-json]");
  if (typeof Tabulator === "undefined") {
    if (fallback instanceof HTMLElement) {
      fallback.classList.remove("hidden");
    }
    return;
  }
  try {
    const parsedRows = JSON.parse(tableElement.dataset.chronyUpstreams || "[]");
    const rows = normalizeChronyUpstreamRows(parsedRows);
    rows.push(chronyBlankUpstreamRow());
    const table = new Tabulator(tableElement, {
      data: rows,
      index: "id",
      layout: "fitColumns",
      height: "260px",
      rowHeight: 34,
      placeholder: "Add an upstream source.",
      reactiveData: false,
      columns: lockNewRecordColumns([
        {
          title: "Source",
          field: "source",
          editor: "input",
          minWidth: 180,
          formatter: (cell) => {
            const value = String(cell.getValue() || "");
            return dnsAddRowHintFormatter(cell, value || "+ Add source here");
          },
        },
        { title: "NTS", field: "use_nts", formatter: chronyGuardedTickFormatter, editor: "tickCross", editable: chronyUpstreamRowHasSource, width: 70, hozAlign: "center" },
        { title: "Max delay", field: "maxdelay", editor: "input", editable: chronyUpstreamRowHasSource, width: 105, formatter: chronyGuardedTextFormatter },
        { title: "Enabled", field: "enabled", formatter: chronyGuardedTickFormatter, editor: "tickCross", editable: chronyUpstreamRowHasSource, width: 92, hozAlign: "center" },
        { title: "Description", field: "description", editor: "input", editable: chronyUpstreamRowHasSource, minWidth: 170, formatter: chronyGuardedTextFormatter },
      ], "source"),
      rowFormatter: (row) => {
        markNewRecordRow(row, "source");
      },
    });
    table.on("cellEdited", (cell) => {
      const row = cell.getRow();
      const data = row.getData();
      if (data.is_new && String(data.source || "").trim()) {
        row.update({ is_new: false, id: data.id || `source-${Date.now()}`, enabled: true });
        ensureChronyUpstreamAddRow(table);
      }
      syncChronyUpstreamsHiddenInput(table);
      if (hiddenInput instanceof HTMLInputElement) {
        hiddenInput.dispatchEvent(new Event("change", { bubbles: true }));
      }
    });
    syncChronyUpstreamsHiddenInput(table);
    if (fallback instanceof HTMLElement) {
      fallback.classList.add("hidden");
    }
  } catch (error) {
    if (fallback instanceof HTMLElement) {
      fallback.classList.remove("hidden");
    }
  }
}

function updateChronySettingsPreview(form, payload = {}) {
  updateDerivedListenAddressSummary(form, payload);
  const configPath = document.querySelector("[data-ntp-config-path]");
  if (configPath instanceof HTMLElement && payload.config_path) {
    configPath.textContent = payload.config_path;
  }
  const configPreview = document.querySelector("[data-ntp-config-preview]");
  if (configPreview instanceof HTMLElement && payload.config_preview !== undefined) {
    configPreview.textContent = payload.config_preview;
    highlightConfigPreviewElement(configPreview);
  }
  const hostname = form.querySelector('input[name="hostname"]');
  if (hostname instanceof HTMLInputElement && payload.hostname) {
    hostname.value = payload.hostname;
  }
  const port = form.querySelector('input[name="port"]');
  if (port instanceof HTMLInputElement && payload.port) {
    port.value = String(payload.port);
  }
}

function updateNtpValidation(payload = {}) {
  const status = document.querySelector("[data-ntp-validation-status]");
  const validationPanel = status?.closest(".panel");
  if (!(status instanceof HTMLElement) || !(validationPanel instanceof HTMLElement)) {
    return;
  }
  const errors = Array.isArray(payload.validation_errors) ? payload.validation_errors : [];
  status.textContent = errors.length ? "needs attention" : "valid";
  status.classList.toggle("good", errors.length === 0);
  status.classList.toggle("warn", errors.length > 0);
  const previewAnchor = validationPanel.querySelector("[data-config-preview-row]");
  let errorBox = validationPanel.querySelector("[data-ntp-validation-errors]");
  const validMessage = validationPanel.querySelector("[data-ntp-validation-message]");
  if (errors.length === 0) {
    errorBox?.remove();
    if (!(validMessage instanceof HTMLElement)) {
      const message = document.createElement("p");
      message.className = "muted";
      message.setAttribute("data-ntp-validation-message", "");
      message.textContent = "The desired Chrony state passes LabFoundry validation. Appliance validation still runs through the allowlisted Chrony helper before apply.";
      validationPanel.insertBefore(message, previewAnchor);
    }
    return;
  }
  validMessage?.remove();
  if (!(errorBox instanceof HTMLElement)) {
    errorBox = document.createElement("ul");
    errorBox.className = "error-list";
    errorBox.setAttribute("data-ntp-validation-errors", "");
    validationPanel.insertBefore(errorBox, previewAnchor);
  }
  if (errorBox instanceof HTMLElement) {
    errorBox.innerHTML = "";
    errors.forEach((error) => {
      const row = document.createElement("li");
      row.textContent = error;
      errorBox.appendChild(row);
    });
  }
}

function initializeChronySettings() {
  document.querySelectorAll("[data-ntp-settings]").forEach((form) => {
    if (!(form instanceof HTMLFormElement)) {
      return;
    }
    form.addEventListener("labfoundry:autosave-success", (event) => {
      const payload = event.detail || {};
      updateChronySettingsPreview(form, payload);
      updateNtpValidation(payload);
    });
  });
}

function formatChronySourceHealthSection(name, section = {}) {
  const title = name.charAt(0).toUpperCase() + name.slice(1);
  const returnCode = Number(section.returncode ?? 0);
  const stdout = String(section.stdout || "").trimEnd();
  const stderr = String(section.stderr || "").trimEnd();
  const lines = [`[${title}] returncode=${returnCode}`];
  if (stdout) {
    lines.push(stdout);
  }
  if (stderr) {
    lines.push(`stderr: ${stderr}`);
  }
  if (!stdout && !stderr) {
    lines.push("(no output)");
  }
  return lines.join("\n");
}

function formatChronySourceHealthPayload(payload = {}) {
  const sections = payload.status && typeof payload.status === "object" ? payload.status : {};
  const names = ["tracking", "sources", "authdata"];
  if (names.some((name) => sections[name])) {
    return names.map((name) => formatChronySourceHealthSection(name, sections[name] || {})).join("\n\n");
  }
  const stdout = String(payload.stdout || "").trimEnd();
  const stderr = String(payload.stderr || "").trimEnd();
  return [stdout, stderr ? `stderr: ${stderr}` : ""].filter(Boolean).join("\n\n") || "No Chrony source health output was returned.";
}

function setChronySourceHealthStatus(statusElement, text, state) {
  if (!(statusElement instanceof HTMLElement)) {
    return;
  }
  statusElement.textContent = text;
  statusElement.classList.toggle("good", state === "good");
  statusElement.classList.toggle("warn", state === "warn");
  statusElement.classList.toggle("muted", state === "muted");
}

let chronySourceHealthLoader = null;

function openChronySourceHealthModal() {
  const modal = document.getElementById("chrony-source-health-modal");
  if (!(modal instanceof HTMLDialogElement)) {
    return;
  }
  if (typeof modal.showModal === "function") {
    modal.showModal();
  } else {
    modal.setAttribute("open", "");
  }
  if (typeof chronySourceHealthLoader === "function") {
    chronySourceHealthLoader();
  }
}

function initializeChronySourceHealthModal() {
  const modal = document.getElementById("chrony-source-health-modal");
  const output = modal?.querySelector("[data-chrony-source-health-output]");
  const status = modal?.querySelector("[data-chrony-source-health-status]");
  const refreshButton = modal?.querySelector("[data-chrony-source-health-refresh]");
  const closeButton = modal?.querySelector("[data-chrony-source-health-close]");
  if (!(modal instanceof HTMLDialogElement) || !(output instanceof HTMLElement)) {
    return;
  }

  const loadHealth = async () => {
    setChronySourceHealthStatus(status, "checking", "muted");
    output.textContent = "Checking chronyc tracking, sources, and authdata...";
    if (refreshButton instanceof HTMLButtonElement) {
      refreshButton.disabled = true;
    }
    try {
      const response = await fetch("/chrony/source-health", {
        method: "GET",
        credentials: "same-origin",
        headers: { Accept: "application/json" },
      });
      const payload = await response.json();
      output.textContent = formatChronySourceHealthPayload(payload);
      const failedSection = Object.values(payload.status || {}).some((section) => Number(section?.returncode ?? 0) !== 0);
      if (!response.ok || !payload.ok || failedSection) {
        setChronySourceHealthStatus(status, "needs attention", "warn");
      } else if (payload.dry_run) {
        setChronySourceHealthStatus(status, "dry-run", "muted");
      } else {
        setChronySourceHealthStatus(status, "healthy", "good");
      }
    } catch (error) {
      output.textContent = error instanceof Error ? error.message : "Unable to check Chrony source health.";
      setChronySourceHealthStatus(status, "failed", "warn");
    } finally {
      if (refreshButton instanceof HTMLButtonElement) {
        refreshButton.disabled = false;
      }
    }
  };
  chronySourceHealthLoader = loadHealth;

  document.querySelectorAll("[data-chrony-source-health-open]").forEach((button) => {
    if (!(button instanceof HTMLButtonElement)) {
      return;
    }
    button.addEventListener("click", openChronySourceHealthModal);
  });
  if (refreshButton instanceof HTMLButtonElement) {
    refreshButton.addEventListener("click", loadHealth);
  }
  if (closeButton instanceof HTMLButtonElement) {
    closeButton.addEventListener("click", () => modal.close());
  }
}

function showWanMessage(elementId, message) {
  showCaMessage(elementId, message, "error");
}

async function postWanAction(url, data, csrf, options = {}) {
  const reload = options.reload ?? true;
  const body = new FormData();
  body.set("csrf", csrf);
  for (const [key, value] of Object.entries(data)) {
    if (["id", "is_new", "wan_policy_name"].includes(key)) {
      continue;
    }
    if (key === "enabled") {
      if (value) {
        body.set("enabled", "on");
      }
      continue;
    }
    body.set(key, value ?? "");
  }
  const response = await fetch(url, {
    method: "POST",
    body,
    credentials: "same-origin",
  });
  if (!response.ok) {
    const text = await response.text();
    const plainText = text.trim().replace(/<[^>]+>/g, " ").replace(/\s+/g, " ");
    throw new Error(plainText || "The route and WAN desired state could not be saved.");
  }
  if (reload) {
    window.location.reload();
  }
}

function newWanRouteRow(defaultTarget = "") {
  return {
    id: "__new__",
    destination_cidr: "",
    gateway: "",
    interface_name: defaultTarget,
    metric: 100,
    enabled: true,
    wan_policy_id: "",
    wan_policy_name: "",
    wan_mode: "interface",
    is_new: true,
  };
}

function newWanPolicyRow() {
  return {
    id: "__new__",
    name: "",
    description: "",
    enabled: true,
    latency_ms: 0,
    jitter_ms: 0,
    packet_loss_percent: 0,
    bandwidth_mbit: "",
    corrupt_percent: 0,
    duplicate_percent: 0,
    reorder_percent: 0,
    is_new: true,
  };
}

function newWanNatRuleRow(defaultTarget = "") {
  return {
    id: "__new__",
    name: "",
    enabled: true,
    source: "any",
    outbound_interface: defaultTarget,
    masquerade: true,
    priority: 100,
    description: "",
    is_new: true,
  };
}

function newWanRoutingRuleRow(defaultSource = "", defaultDestination = "") {
  return {
    id: "__new__",
    name: "",
    kind: "add explicit rule",
    enabled: true,
    source_interface: defaultSource,
    destination_interface: defaultDestination,
    priority: 100,
    description: "",
    generated: false,
    is_new: true,
  };
}

function hasRequiredWanRouteFields(data) {
  return Boolean((data.destination_cidr || "").trim() && (data.interface_name || "").trim());
}

function hasRequiredWanNatFields(data) {
  return Boolean((data.name || "").trim() && (data.outbound_interface || "").trim());
}

function hasRequiredWanPolicyFields(data) {
  return Boolean((data.name || "").trim());
}

function hasRequiredWanRoutingFields(data) {
  return Boolean((data.name || "").trim() && (data.source_interface || "").trim() && (data.destination_interface || "").trim());
}

function wanPolicyValues(policyOptions) {
  const values = { "": "none" };
  policyOptions.forEach((policy) => {
    values[String(policy.id)] = policy.label;
  });
  return values;
}

function wanPolicyFormatter(cell, policyLabels) {
  const value = cell.getValue();
  if (!value) {
    return '<span class="muted">none</span>';
  }
  return escapeHtml(policyLabels[String(value)] || value);
}

async function autoSaveWanRoute(cell, csrf) {
  clearCaMessage("routes-wan-route-error");
  const row = cell.getRow();
  const data = row.getData();
  if (data.is_new) {
    if (!hasRequiredWanRouteFields(data)) {
      reformatPendingNewRecord(cell);
      return;
    }
    try {
      await postWanAction("/routes-wan/routes", data, csrf, { reload: false });
      showTransientGridStatus("Added");
      window.location.reload();
    } catch (error) {
      showWanMessage("routes-wan-route-error", error instanceof Error ? error.message : "The route could not be added.");
      if (typeof cell.restoreOldValue === "function") {
        cell.restoreOldValue();
      }
    }
    return;
  }
  try {
    await postWanAction(`/routes-wan/routes/${data.id}/edit`, data, csrf, { reload: false });
    showTransientGridStatus("Saved");
    await refreshNetworkSideStack();
  } catch (error) {
    showWanMessage("routes-wan-route-error", error instanceof Error ? error.message : "The route could not be saved.");
    if (typeof cell.restoreOldValue === "function") {
      cell.restoreOldValue();
    }
  }
}

async function autoSaveWanPolicy(cell, csrf) {
  clearCaMessage("routes-wan-policy-error");
  const row = cell.getRow();
  const data = row.getData();
  if (data.is_new) {
    if (!hasRequiredWanPolicyFields(data)) {
      reformatPendingNewRecord(cell);
      return;
    }
    try {
      await postWanAction("/routes-wan/policies", data, csrf, { reload: false });
      showTransientGridStatus("Added");
      window.location.reload();
    } catch (error) {
      showWanMessage("routes-wan-policy-error", error instanceof Error ? error.message : "The WAN policy could not be added.");
      if (typeof cell.restoreOldValue === "function") {
        cell.restoreOldValue();
      }
    }
    return;
  }
  try {
    await postWanAction(`/routes-wan/policies/${data.id}/edit`, data, csrf, { reload: false });
    showTransientGridStatus("Saved");
    await refreshNetworkSideStack();
  } catch (error) {
    showWanMessage("routes-wan-policy-error", error instanceof Error ? error.message : "The WAN policy could not be saved.");
    if (typeof cell.restoreOldValue === "function") {
      cell.restoreOldValue();
    }
  }
}

async function autoSaveWanNatRule(cell, csrf) {
  clearCaMessage("routes-wan-nat-error");
  const row = cell.getRow();
  const data = row.getData();
  if (data.is_new) {
    if (!hasRequiredWanNatFields(data)) {
      reformatPendingNewRecord(cell);
      return;
    }
    try {
      await postWanAction("/routes-wan/nat-rules", data, csrf, { reload: false });
      showTransientGridStatus("Added");
      window.location.reload();
    } catch (error) {
      showWanMessage("routes-wan-nat-error", error instanceof Error ? error.message : "The NAT rule could not be added.");
      if (typeof cell.restoreOldValue === "function") {
        cell.restoreOldValue();
      }
    }
    return;
  }
  try {
    await postWanAction(`/routes-wan/nat-rules/${data.id}/edit`, data, csrf, { reload: false });
    showTransientGridStatus("Saved");
    await refreshNetworkSideStack();
  } catch (error) {
    showWanMessage("routes-wan-nat-error", error instanceof Error ? error.message : "The NAT rule could not be saved.");
    if (typeof cell.restoreOldValue === "function") {
      cell.restoreOldValue();
    }
  }
}

async function autoSaveWanRoutingRule(cell, csrf) {
  clearCaMessage("routes-wan-routing-error");
  const row = cell.getRow();
  const data = row.getData();
  if (data.generated) {
    return;
  }
  if (data.is_new) {
    if (!hasRequiredWanRoutingFields(data)) {
      reformatPendingNewRecord(cell);
      return;
    }
    try {
      await postWanAction("/routes-wan/routing-rules", data, csrf, { reload: false });
      showTransientGridStatus("Added");
      window.location.reload();
    } catch (error) {
      showWanMessage("routes-wan-routing-error", error instanceof Error ? error.message : "The routing rule could not be added.");
      if (typeof cell.restoreOldValue === "function") {
        cell.restoreOldValue();
      }
    }
    return;
  }
  try {
    await postWanAction(`/routes-wan/routing-rules/${data.id}/edit`, data, csrf, { reload: false });
    showTransientGridStatus("Saved");
    await refreshNetworkSideStack();
  } catch (error) {
    showWanMessage("routes-wan-routing-error", error instanceof Error ? error.message : "The routing rule could not be saved.");
    if (typeof cell.restoreOldValue === "function") {
      cell.restoreOldValue();
    }
  }
}

async function deleteWanRouteFromMenu(row, csrf) {
  clearCaMessage("routes-wan-route-error");
  const data = row.getData();
  if (data.is_new) {
    return;
  }
  const confirmed = await requestConfirmation({
    title: `Delete route ${data.destination_cidr}?`,
    message: "This removes the route from LabFoundry desired state. It will not touch the appliance until global appliance apply runs.",
    label: "Delete route",
  });
  if (!confirmed) {
    return;
  }
  try {
    await postWanAction(`/routes-wan/routes/${data.id}/delete`, {}, csrf);
  } catch (error) {
    showWanMessage("routes-wan-route-error", error instanceof Error ? error.message : "The route could not be deleted.");
  }
}

async function deleteWanNatRuleFromMenu(row, csrf) {
  clearCaMessage("routes-wan-nat-error");
  const data = row.getData();
  if (data.is_new) {
    return;
  }
  const confirmed = await requestConfirmation({
    title: `Delete NAT rule ${data.name}?`,
    message: "This removes the NAT rule from LabFoundry desired state. It will not touch the appliance until global appliance apply runs.",
    label: "Delete NAT rule",
  });
  if (!confirmed) {
    return;
  }
  try {
    await postWanAction(`/routes-wan/nat-rules/${data.id}/delete`, {}, csrf);
  } catch (error) {
    showWanMessage("routes-wan-nat-error", error instanceof Error ? error.message : "The NAT rule could not be deleted.");
  }
}

async function deleteWanPolicyFromMenu(row, csrf) {
  clearCaMessage("routes-wan-policy-error");
  const data = row.getData();
  if (data.is_new) {
    return;
  }
  const confirmed = await requestConfirmation({
    title: `Delete ${data.name}?`,
    message: "This removes the WAN policy from LabFoundry desired state and clears it from assigned routes. It will not touch the appliance until global appliance apply runs.",
    label: "Delete policy",
  });
  if (!confirmed) {
    return;
  }
  try {
    await postWanAction(`/routes-wan/policies/${data.id}/delete`, {}, csrf);
  } catch (error) {
    showWanMessage("routes-wan-policy-error", error instanceof Error ? error.message : "The WAN policy could not be deleted.");
  }
}

async function deleteWanRoutingRuleFromMenu(row, csrf) {
  clearCaMessage("routes-wan-routing-error");
  const data = row.getData();
  if (data.is_new || data.generated) {
    return;
  }
  const confirmed = await requestConfirmation({
    title: `Delete routing rule ${data.name}?`,
    message: "This removes the routing permission from LabFoundry desired state. It will not touch the appliance until global appliance apply runs.",
    label: "Delete routing rule",
  });
  if (!confirmed) {
    return;
  }
  try {
    await postWanAction(`/routes-wan/routing-rules/${data.id}/delete`, {}, csrf);
  } catch (error) {
    showWanMessage("routes-wan-routing-error", error instanceof Error ? error.message : "The routing rule could not be deleted.");
  }
}

function initializeRoutesWanRoutingTable() {
  const tableElement = document.getElementById("routes-wan-routing-table");
  if (!(tableElement instanceof HTMLElement)) {
    return;
  }
  const fallback = document.getElementById(tableElement.dataset.fallbackId || "");
  if (typeof Tabulator === "undefined") {
    showWanMessage("routes-wan-routing-error", "Tabulator did not load. Showing the fallback table.");
    return;
  }
  const csrf = tableElement.dataset.csrf || "";
  const targets = JSON.parse(tableElement.dataset.targetOptions || "[]");
  const targetValues = Object.fromEntries(targets.map((target) => [target.name, target.label]));
  const defaultSource = targets[0]?.name || "";
  const defaultDestination = targets.find((target) => target.name !== defaultSource)?.name || "";
  const generatedRows = JSON.parse(tableElement.dataset.generatedRules || "[]");
  const explicitRows = JSON.parse(tableElement.dataset.rules || "[]").map((row) => ({ ...row, kind: "explicit access rule" }));
  const generatedWithKind = generatedRows.map((row) => ({ ...row, kind: "auto route-role rule" }));
  const rows = [...generatedWithKind, ...explicitRows, newWanRoutingRuleRow(defaultSource, defaultDestination)];
  try {
    new Tabulator(tableElement, {
      data: rows,
      index: "id",
      layout: "fitColumns",
      height: "420px",
      rowHeight: 28,
      placeholder: "No routing permissions configured.",
      reactiveData: false,
      rowContextMenu: [
        {
          label: "Delete routing rule",
          action: (_event, row) => deleteWanRoutingRuleFromMenu(row, csrf),
          disabled: (component) => component.getData().is_new || component.getData().generated,
        },
      ],
      columns: lockNewRecordColumns([
        {
          title: "Name",
          field: "name",
          editor: "input",
          editable: (cell) => !cell.getRow().getData().generated,
          formatter: (cell) => dnsAddRowHintFormatter(cell, "+ Add explicit access rule"),
          minWidth: 170,
          cellEdited: (cell) => autoSaveWanRoutingRule(cell, csrf),
        },
        {
          title: "Type",
          field: "kind",
          formatter: (cell) => {
            const data = cell.getRow().getData();
            if (data.generated) {
              return '<span class="status-pill good">auto route-role</span>';
            }
            if (data.is_new) {
              return '<span class="status-pill warn">new explicit</span>';
            }
            return '<span class="status-pill muted">explicit access</span>';
          },
          width: 140,
          headerSort: false,
        },
        {
          title: "Source",
          field: "source_interface",
          editor: "list",
          editable: (cell) => !cell.getRow().getData().generated,
          editorParams: { values: targetValues },
          formatter: (cell) => escapeHtml(targetValues[cell.getValue()] || cell.getValue() || "choose source..."),
          minWidth: 220,
          cellEdited: (cell) => autoSaveWanRoutingRule(cell, csrf),
        },
        {
          title: "Destination",
          field: "destination_interface",
          editor: "list",
          editable: (cell) => !cell.getRow().getData().generated,
          editorParams: { values: targetValues },
          formatter: (cell) => escapeHtml(targetValues[cell.getValue()] || cell.getValue() || "choose destination..."),
          minWidth: 220,
          cellEdited: (cell) => autoSaveWanRoutingRule(cell, csrf),
        },
        {
          title: "Priority",
          field: "priority",
          editor: "number",
          editable: (cell) => !cell.getRow().getData().generated,
          width: 100,
          cellEdited: (cell) => autoSaveWanRoutingRule(cell, csrf),
        },
        {
          title: "Enabled",
          field: "enabled",
          formatter: labFoundryBooleanFormatter,
          editor: "tickCross",
          editable: (cell) => !cell.getRow().getData().generated,
          hozAlign: "center",
          width: 100,
          headerSort: false,
          cellEdited: (cell) => autoSaveWanRoutingRule(cell, csrf),
        },
        {
          title: "Description",
          field: "description",
          editor: "input",
          editable: (cell) => !cell.getRow().getData().generated,
          minWidth: 190,
          cellEdited: (cell) => autoSaveWanRoutingRule(cell, csrf),
        },
      ], "name"),
      rowFormatter: (row) => {
        const data = row.getData();
        markNewRecordRow(row, "name");
        row.getElement().classList.toggle("readonly-row", Boolean(data.generated));
      },
    });
    if (fallback) {
      fallback.classList.add("hidden");
    }
  } catch (error) {
    showWanMessage("routes-wan-routing-error", error instanceof Error ? error.message : "Tabulator could not render. Showing the fallback table.");
  }
}

function initializeRoutesWanNatTable() {
  const tableElement = document.getElementById("routes-wan-nat-table");
  if (!(tableElement instanceof HTMLElement)) {
    return;
  }
  const fallback = document.getElementById(tableElement.dataset.fallbackId || "");
  if (typeof Tabulator === "undefined") {
    showWanMessage("routes-wan-nat-error", "Tabulator did not load. Showing the fallback table.");
    return;
  }
  const csrf = tableElement.dataset.csrf || "";
  const targets = JSON.parse(tableElement.dataset.natTargetOptions || "[]");
  const targetValues = Object.fromEntries(targets.map((target) => [target.name, target.label]));
  const defaultTarget = targets[0]?.name || "";
  const rows = [...JSON.parse(tableElement.dataset.natRules || "[]"), newWanNatRuleRow(defaultTarget)];
  try {
    new Tabulator(tableElement, {
      data: rows,
      index: "id",
      layout: "fitColumns",
      height: "420px",
      rowHeight: 28,
      placeholder: "No NAT rules configured.",
      reactiveData: false,
      rowContextMenu: [
        {
          label: "Delete NAT rule",
          action: (_event, row) => deleteWanNatRuleFromMenu(row, csrf),
          disabled: (component) => component.getData().is_new,
        },
      ],
      columns: lockNewRecordColumns([
        {
          title: "Name",
          field: "name",
          editor: "input",
          formatter: (cell) => dnsAddRowHintFormatter(cell, "+ Add NAT rule here"),
          minWidth: 160,
          cellEdited: (cell) => autoSaveWanNatRule(cell, csrf),
        },
        {
          title: "Source",
          field: "source",
          editor: "input",
          formatter: (cell) => dnsAddRowHintFormatter(cell, "any or CIDR"),
          minWidth: 170,
          cellEdited: (cell) => autoSaveWanNatRule(cell, csrf),
        },
        {
          title: "Outbound",
          field: "outbound_interface",
          editor: "list",
          editorParams: { values: targetValues },
          formatter: (cell) => escapeHtml(targetValues[cell.getValue()] || cell.getValue() || "choose interface..."),
          minWidth: 230,
          cellEdited: (cell) => autoSaveWanNatRule(cell, csrf),
        },
        {
          title: "Masq",
          field: "masquerade",
          formatter: labFoundryBooleanFormatter,
          editor: "tickCross",
          hozAlign: "center",
          width: 90,
          headerSort: false,
          cellEdited: (cell) => autoSaveWanNatRule(cell, csrf),
        },
        {
          title: "Priority",
          field: "priority",
          editor: "number",
          width: 100,
          cellEdited: (cell) => autoSaveWanNatRule(cell, csrf),
        },
        {
          title: "Enabled",
          field: "enabled",
          formatter: labFoundryBooleanFormatter,
          editor: "tickCross",
          hozAlign: "center",
          width: 100,
          headerSort: false,
          cellEdited: (cell) => autoSaveWanNatRule(cell, csrf),
        },
        { title: "Description", field: "description", editor: "input", minWidth: 180, cellEdited: (cell) => autoSaveWanNatRule(cell, csrf) },
      ], "name"),
      rowFormatter: (row) => {
        markNewRecordRow(row, "name");
      },
    });
    if (fallback) {
      fallback.classList.add("hidden");
    }
  } catch (error) {
    showWanMessage("routes-wan-nat-error", error instanceof Error ? error.message : "Tabulator could not render. Showing the fallback table.");
  }
}

function initializeRoutesWanRoutesTable() {
  const tableElement = document.getElementById("routes-wan-routes-table");
  if (!(tableElement instanceof HTMLElement)) {
    return;
  }
  const fallback = document.getElementById(tableElement.dataset.fallbackId || "");
  if (typeof Tabulator === "undefined") {
    showWanMessage("routes-wan-route-error", "Tabulator did not load. Showing the fallback table.");
    return;
  }
  const csrf = tableElement.dataset.csrf || "";
  const targets = JSON.parse(tableElement.dataset.targetOptions || "[]");
  const targetValues = Object.fromEntries(targets.map((target) => [target.name, target.label]));
  const policyOptions = JSON.parse(tableElement.dataset.policyOptions || "[]");
  const policyValues = wanPolicyValues(policyOptions);
  const defaultTarget = targets[0]?.name || "";
  const rows = [...JSON.parse(tableElement.dataset.routes || "[]"), newWanRouteRow(defaultTarget)];
  try {
    new Tabulator(tableElement, {
      data: rows,
      index: "id",
      layout: "fitColumns",
      height: "420px",
      rowHeight: 28,
      placeholder: "No routes configured.",
      reactiveData: false,
      rowContextMenu: [
        {
          label: "Delete route",
          action: (_event, row) => deleteWanRouteFromMenu(row, csrf),
          disabled: (component) => component.getData().is_new,
        },
      ],
      columns: lockNewRecordColumns([
        {
          title: "Destination",
          field: "destination_cidr",
          editor: "input",
          formatter: (cell) => dnsAddRowHintFormatter(cell, "+ Add route here"),
          minWidth: 160,
          cellEdited: (cell) => autoSaveWanRoute(cell, csrf),
        },
        {
          title: "Gateway",
          field: "gateway",
          editor: "input",
          formatter: (cell) => dnsAddRowHintFormatter(cell, "direct"),
          minWidth: 135,
          cellEdited: (cell) => autoSaveWanRoute(cell, csrf),
        },
        {
          title: "Interface",
          field: "interface_name",
          editor: "list",
          editorParams: { values: targetValues },
          formatter: (cell) => escapeHtml(targetValues[cell.getValue()] || cell.getValue() || "choose target..."),
          minWidth: 230,
          cellEdited: (cell) => autoSaveWanRoute(cell, csrf),
        },
        {
          title: "WAN Policy",
          field: "wan_policy_id",
          editor: "list",
          editorParams: { values: policyValues },
          formatter: (cell) => wanPolicyFormatter(cell, policyValues),
          minWidth: 150,
          cellEdited: (cell) => autoSaveWanRoute(cell, csrf),
        },
        {
          title: "Metric",
          field: "metric",
          editor: "number",
          width: 90,
          cellEdited: (cell) => autoSaveWanRoute(cell, csrf),
        },
        {
          title: "Enabled",
          field: "enabled",
          formatter: labFoundryBooleanFormatter,
          editor: "tickCross",
          hozAlign: "center",
          width: 100,
          headerSort: false,
          cellEdited: (cell) => autoSaveWanRoute(cell, csrf),
        },
      ], "destination_cidr"),
      rowFormatter: (row) => {
        markNewRecordRow(row, "destination_cidr");
      },
    });
    if (fallback) {
      fallback.classList.add("hidden");
    }
  } catch (error) {
    showWanMessage("routes-wan-route-error", error instanceof Error ? error.message : "Tabulator could not render. Showing the fallback table.");
  }
}

function initializeRoutesWanPoliciesTable() {
  const tableElement = document.getElementById("routes-wan-policies-table");
  if (!(tableElement instanceof HTMLElement)) {
    return;
  }
  const fallback = document.getElementById(tableElement.dataset.fallbackId || "");
  if (typeof Tabulator === "undefined") {
    showWanMessage("routes-wan-policy-error", "Tabulator did not load. Showing the fallback table.");
    return;
  }
  const csrf = tableElement.dataset.csrf || "";
  const rows = [...JSON.parse(tableElement.dataset.policies || "[]"), newWanPolicyRow()];
  try {
    new Tabulator(tableElement, {
      data: rows,
      index: "id",
      layout: "fitColumns",
      height: "420px",
      rowHeight: 28,
      placeholder: "No WAN policies configured.",
      reactiveData: false,
      rowContextMenu: [
        {
          label: "Delete policy",
          action: (_event, row) => deleteWanPolicyFromMenu(row, csrf),
          disabled: (component) => component.getData().is_new,
        },
      ],
      columns: lockNewRecordColumns([
        {
          title: "Name",
          field: "name",
          editor: "input",
          formatter: (cell) => dnsAddRowHintFormatter(cell, "+ Add policy here"),
          minWidth: 145,
          cellEdited: (cell) => autoSaveWanPolicy(cell, csrf),
        },
        { title: "Latency ms", field: "latency_ms", editor: "number", width: 115, cellEdited: (cell) => autoSaveWanPolicy(cell, csrf) },
        { title: "Jitter ms", field: "jitter_ms", editor: "number", width: 105, cellEdited: (cell) => autoSaveWanPolicy(cell, csrf) },
        { title: "Loss %", field: "packet_loss_percent", editor: "number", width: 95, cellEdited: (cell) => autoSaveWanPolicy(cell, csrf) },
        { title: "Bandwidth Mbps", field: "bandwidth_mbit", editor: "number", width: 145, cellEdited: (cell) => autoSaveWanPolicy(cell, csrf) },
        { title: "Corrupt %", field: "corrupt_percent", editor: "number", width: 105, cellEdited: (cell) => autoSaveWanPolicy(cell, csrf) },
        { title: "Duplicate %", field: "duplicate_percent", editor: "number", width: 120, cellEdited: (cell) => autoSaveWanPolicy(cell, csrf) },
        { title: "Reorder %", field: "reorder_percent", editor: "number", width: 110, cellEdited: (cell) => autoSaveWanPolicy(cell, csrf) },
        {
          title: "Enabled",
          field: "enabled",
          formatter: labFoundryBooleanFormatter,
          editor: "tickCross",
          hozAlign: "center",
          width: 100,
          headerSort: false,
          cellEdited: (cell) => autoSaveWanPolicy(cell, csrf),
        },
        { title: "Description", field: "description", editor: "input", minWidth: 180, cellEdited: (cell) => autoSaveWanPolicy(cell, csrf) },
      ], "name"),
      rowFormatter: (row) => {
        markNewRecordRow(row, "name");
      },
    });
    if (fallback) {
      fallback.classList.add("hidden");
    }
  } catch (error) {
    showWanMessage("routes-wan-policy-error", error instanceof Error ? error.message : "Tabulator could not render. Showing the fallback table.");
  }
}

function showNetworkMessage(elementId, message) {
  showCaMessage(elementId, message, "error");
}

async function postNetworkAction(url, data, csrf, options = {}) {
  const reload = options.reload ?? true;
  const body = new FormData();
  body.set("csrf", csrf);
  for (const [key, value] of Object.entries(data)) {
    if (
      key === "id" ||
      key === "is_new" ||
      key === "name" ||
      key === "mac_address" ||
      key === "driver" ||
      key === "speed" ||
      key === "host_ip_cidr" ||
      key === "host_ipv6_cidr" ||
      key === "host_mtu" ||
      key === "host_admin_state" ||
      key === "oper_state" ||
      key === "vlan_count" ||
      key === "parent_missing" ||
      key === "admin_up"
    ) {
      continue;
    }
    if (key === "enabled") {
      if (value) {
        body.set("enabled", "on");
      }
      continue;
    }
    body.set(key, value ?? "");
  }
  const response = await fetch(url, {
    method: "POST",
    body,
    credentials: "same-origin",
  });
  if (!response.ok) {
    const text = await response.text();
    const plainText = text.trim().replace(/<[^>]+>/g, " ").replace(/\s+/g, " ");
    throw new Error(text.match(/VLAN .* already exists[^<]*/)?.[0] || plainText || "The network desired state could not be saved.");
  }
  if (reload) {
    window.location.reload();
  }
}

function newVlanInterfaceRow(defaultParent = "eth1", defaultMtu = 1500) {
  return {
    id: "__new__",
    name: "",
    parent_interface: defaultParent,
    vlan_id: "",
    ip_cidr: "",
    ipv6_cidr: "",
    mtu: defaultMtu,
    role: "access",
    enabled: true,
    is_new: true,
    is_activated: false,
    requires_activation: true,
  };
}

function physicalLinkTypeFormatter(cell, modeOptions) {
  const data = cell.getRow().getData();
  const value = cell.getValue();
  const label = modeOptions[value] || value;
  const vlanCount = Number(data.vlan_count || 0);
  if (value === "trunk" && vlanCount > 0) {
    return `${escapeHtml(label)} <span class="cell-note">locked by ${vlanCount} VLAN${vlanCount === 1 ? "" : "s"}</span>`;
  }
  return escapeHtml(label);
}

function networkStateIcon(state, label) {
  const normalized = String(state || "").toLowerCase();
  const displayLabel = label || normalized || "unknown";
  let className = "unknown";
  let symbol = "?";
  if (normalized === "up" || normalized === "enabled" || normalized === "true") {
    className = "up";
    symbol = "&#8593;";
  } else if (normalized === "down" || normalized === "disabled" || normalized === "false") {
    className = "down";
    symbol = "&#8595;";
  } else if (normalized === "missing") {
    className = "missing";
    symbol = "!";
  }
  const title = displayLabel === "missing" ? "missing from host inventory" : displayLabel;
  return `<span class="network-state-icon ${className}" title="${escapeHtml(title)}"><span class="state-symbol" aria-hidden="true">${symbol}</span><span class="state-label">${escapeHtml(displayLabel)}</span></span>`;
}

function isValidIpv4Address(value) {
  const parts = String(value || "").split(".");
  return parts.length === 4 && parts.every((part) => {
    if (!/^\d{1,3}$/.test(part)) {
      return false;
    }
    const numberValue = Number(part);
    return numberValue >= 0 && numberValue <= 255;
  });
}

function isValidIpv6Address(value) {
  const address = String(value || "");
  if (!address.includes(":") || /[\s[\]]/.test(address)) {
    return false;
  }
  try {
    new URL(`http://[${address}]/`);
    return true;
  } catch (_error) {
    return false;
  }
}

function isValidCidr(value, family) {
  const cidr = String(value || "").trim();
  if (!cidr) {
    return true;
  }
  const parts = cidr.split("/");
  if (parts.length !== 2 || !parts[0] || !/^\d+$/.test(parts[1])) {
    return false;
  }
  const prefix = Number(parts[1]);
  if (family === "ipv4") {
    return prefix >= 0 && prefix <= 32 && isValidIpv4Address(parts[0]);
  }
  return prefix >= 0 && prefix <= 128 && isValidIpv6Address(parts[0]);
}

function showCidrInputError(cell, family) {
  const table = typeof cell.getTable === "function" ? cell.getTable() : null;
  const tableElement = table?.element;
  const target = tableElement?.id === "vlan-interfaces-table" ? "vlan-interface-error" : "physical-interface-error";
  showNetworkMessage(target, family === "ipv4" ? "Enter a valid IPv4 CIDR such as 192.168.50.1/24." : "Enter a valid IPv6 CIDR such as fd00:50::1/64.");
}

function cidrInputEditor(cell, onRendered, success, cancel, editorParams = {}) {
  const family = editorParams.family || "ipv4";
  const input = document.createElement("input");
  input.type = "text";
  input.value = cell.getValue() || "";
  input.placeholder = editorParams.placeholder || (family === "ipv4" ? "192.168.50.1/24" : "fd00:50::1/64");
  input.autocomplete = "off";
  input.spellcheck = false;
  input.inputMode = family === "ipv4" ? "decimal" : "text";
  input.setAttribute("aria-label", family === "ipv4" ? "IPv4 CIDR" : "IPv6 CIDR");
  const disallowed = family === "ipv4" ? /[^0-9./]/g : /[^0-9A-Fa-f:./]/g;

  const updateValidity = () => {
    const nextValue = input.value.replace(disallowed, "");
    if (nextValue !== input.value) {
      input.value = nextValue;
    }
    input.classList.toggle("invalid-cidr-input", !isValidCidr(input.value, family));
  };

  const submit = () => {
    const value = input.value.trim();
    if (!isValidCidr(value, family)) {
      input.classList.add("invalid-cidr-input");
      showCidrInputError(cell, family);
      return false;
    }
    success(value);
    return true;
  };

  input.addEventListener("input", updateValidity);
  input.addEventListener("change", submit);
  input.addEventListener("blur", () => {
    if (!submit()) {
      cancel();
    }
  });
  input.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      submit();
    } else if (event.key === "Escape") {
      cancel();
    }
  });

  onRendered(() => {
    input.focus();
    input.select();
    updateValidity();
  });
  return input;
}

function adminStateFormatter(cell) {
  return networkStateIcon(cell.getValue() ? "up" : "down", cell.getValue() ? "up" : "down");
}

function operStateFormatter(cell) {
  const value = cell.getValue() || "unknown";
  return networkStateIcon(value, value);
}

function vlanEnabledFormatter(cell) {
  const data = cell.getRow().getData();
  if (data.parent_missing) {
    return '<span class="network-state-icon missing" title="Parent NIC is missing; this VLAN is disabled until moved to an available trunk parent."><span class="state-symbol" aria-hidden="true">!</span><span class="state-label">missing</span></span>';
  }
  return adminStateFormatter(cell);
}

function hasRequiredVlanFields(data) {
  return Boolean((data.parent_interface || "").trim() && String(data.vlan_id || "").trim() && (String(data.ip_cidr || "").trim() || String(data.ipv6_cidr || "").trim()));
}

function editNewRowCell(cell, fieldName) {
  const row = cell.getRow();
  if (!row.getData().is_new) {
    return;
  }
  const target = row.getCell(fieldName);
  if (target && typeof target.edit === "function") {
    target.edit();
  }
}

async function activateNewVlanRow(cell) {
  const row = cell.getRow();
  const data = row.getData();
  if (!data.is_new) {
    return;
  }
  data.is_activated = true;
  row.reformat();
  row.getElement().classList.remove("new-record-row-pending");
  const vlanIdCell = row.getCell("vlan_id");
  if (vlanIdCell && typeof vlanIdCell.edit === "function") {
    vlanIdCell.edit();
  }
}

function physicalRoleFormatter(cell) {
  if (cell.getRow().getData().mode === "trunk") {
    return "";
  }
  return escapeHtml(cell.getValue());
}

async function autoSaveVlanParent(cell, csrf, parentMtus) {
  const row = cell.getRow();
  const data = row.getData();
  if (data.is_new) {
    const parentMtu = Number(parentMtus[data.parent_interface]);
    if (Number.isInteger(parentMtu) && parentMtu > 0) {
      await row.update({ mtu: parentMtu });
    }
  }
  await updateVlanDerivedName(row);
  await autoSaveVlanInterface(cell, csrf);
}

function vlanDerivedName(data) {
  const parent = String(data.parent_interface || "").trim();
  const vlanId = String(data.vlan_id || "").trim();
  return parent && vlanId ? `${parent}.${vlanId}` : "";
}

async function updateVlanDerivedName(row) {
  const name = vlanDerivedName(row.getData());
  if (row.getData().name !== name) {
    await row.update({ name });
  }
}

async function autoSaveVlanId(cell, csrf) {
  await updateVlanDerivedName(cell.getRow());
  await autoSaveVlanInterface(cell, csrf);
}

function roleValues(options) {
  return Object.fromEntries(options.map((item) => [item, item]));
}

function labeledValues(options, labels) {
  return Object.fromEntries(options.map((item) => [item, labels[item] || item]));
}

async function refreshNetworkSideStack() {
  const currentSideStack = document.querySelector("aside.side-stack");
  if (!(currentSideStack instanceof HTMLElement)) {
    return;
  }
  const response = await fetch(window.location.href, {
    credentials: "same-origin",
    headers: { "X-Requested-With": "LabFoundrySideRefresh" },
  });
  if (!response.ok) {
    return;
  }
  const html = await response.text();
  const nextDocument = new DOMParser().parseFromString(html, "text/html");
  const nextSideStack = nextDocument.querySelector("aside.side-stack");
  if (nextSideStack instanceof HTMLElement) {
    currentSideStack.replaceWith(nextSideStack);
    highlightConfigPreviews(nextSideStack);
  }
}

async function autoSavePhysicalInterface(cell, csrf) {
  clearCaMessage("physical-interface-error");
  const row = cell.getRow();
  const data = row.getData();
  data.admin_state = data.admin_up ? "up" : "down";
  if (data.ipv4_method === "dhcp") {
    data.ip_cidr = "";
  }
  try {
    await postNetworkAction(`/physical-interfaces/${data.id}/edit`, data, csrf, { reload: false });
    showTransientGridStatus("Saved");
    await refreshNetworkSideStack();
  } catch (error) {
    showNetworkMessage("physical-interface-error", error instanceof Error ? error.message : "The physical interface could not be saved.");
    if (typeof cell.restoreOldValue === "function") {
      cell.restoreOldValue();
    }
  }
}

async function savePhysicalInterfaceRow(row, csrf, successMessage = "Saved") {
  clearCaMessage("physical-interface-error");
  const data = row.getData();
  data.admin_state = data.admin_up ? "up" : "down";
  if (data.ipv4_method === "dhcp") {
    data.ip_cidr = "";
  }
  try {
    await postNetworkAction(`/physical-interfaces/${data.id}/edit`, data, csrf, { reload: false });
    showTransientGridStatus(successMessage);
    await refreshNetworkSideStack();
  } catch (error) {
    showNetworkMessage("physical-interface-error", error instanceof Error ? error.message : "The physical interface could not be saved.");
    throw error;
  }
}

async function togglePhysicalInterfaceFromMenu(row, csrf) {
  const data = row.getData();
  const nextAdminUp = !Boolean(data.admin_up);
  const actionLabel = nextAdminUp ? "Enable interface" : "Disable interface";
  if (data.role === "management" && !nextAdminUp) {
    showNetworkMessage("physical-interface-error", "The management interface must stay enabled.");
    return;
  }
  const confirmed = await requestConfirmation({
    title: `${actionLabel} ${data.name}?`,
    message: `This changes ${data.name} desired admin state to ${nextAdminUp ? "up" : "down"}. It will not touch the appliance until global appliance apply runs.`,
    label: actionLabel,
  });
  if (!confirmed) {
    return;
  }
  const previousAdminUp = data.admin_up;
  try {
    await row.update({ admin_up: nextAdminUp, admin_state: nextAdminUp ? "up" : "down" });
    await savePhysicalInterfaceRow(row, csrf, nextAdminUp ? "Enabled" : "Disabled");
  } catch (_error) {
    await row.update({ admin_up: previousAdminUp, admin_state: previousAdminUp ? "up" : "down" });
  }
}

async function convertManagementDhcpInterfaceToStatic(row, csrf) {
  const data = row.getData();
  const observedIpv4 = String(data.host_ip_cidr || "").trim();
  const observedIpv6 = String(data.host_ipv6_cidr || "").trim();
  if (data.role !== "management" || data.ipv4_method !== "dhcp") {
    showNetworkMessage("physical-interface-error", "Only a management interface using IPv4 DHCP can be converted to static addressing.");
    return;
  }
  if (!observedIpv4 && !observedIpv6) {
    showNetworkMessage("physical-interface-error", `${data.name} has no observed DHCP IPv4 or IPv6 CIDR to copy into desired state.`);
    return;
  }
  const confirmed = await requestConfirmation({
    title: `Convert ${data.name} DHCP lease to static?`,
    message: `This copies the observed DHCP address${observedIpv6 ? "es" : ""} into desired IPv4/IPv6 CIDR fields and changes IPv4 method to static. If DHCP-provided DNS was being used, LabFoundry also preserves it as static resolver or DNS forwarder desired state. The appliance is not changed until global appliance apply runs.`,
    label: "Convert to static",
  });
  if (!confirmed) {
    return;
  }
  const previous = {
    ipv4_method: data.ipv4_method,
    ip_cidr: data.ip_cidr,
    ipv6_cidr: data.ipv6_cidr,
  };
  try {
    await row.update({
      ipv4_method: "static",
      ip_cidr: observedIpv4 || data.ip_cidr || "",
      ipv6_cidr: observedIpv6 || data.ipv6_cidr || "",
    });
    await savePhysicalInterfaceRow(row, csrf, "Converted");
  } catch (_error) {
    await row.update(previous);
  }
}

async function forgetPhysicalInterfaceFromMenu(row, csrf) {
  clearCaMessage("physical-interface-error");
  const data = row.getData();
  if (data.oper_state !== "missing") {
    showNetworkMessage("physical-interface-error", "Only interfaces already marked missing from host inventory can be forgotten.");
    return;
  }
  const confirmed = await requestConfirmation({
    title: `Forget ${data.name}?`,
    message: "This removes the missing interface row from LabFoundry inventory and deletes disabled VLAN rows tied to it. It does not touch the appliance until global appliance apply runs.",
    label: "Forget interface",
  });
  if (!confirmed) {
    return;
  }
  try {
    await postNetworkAction(`/physical-interfaces/${data.id}/forget`, {}, csrf);
  } catch (error) {
    showNetworkMessage("physical-interface-error", error instanceof Error ? error.message : "The missing interface could not be forgotten.");
  }
}

async function autoSaveVlanInterface(cell, csrf) {
  clearCaMessage("vlan-interface-error");
  const row = cell.getRow();
  const data = row.getData();
  if (data.parent_missing) {
    row.update({ enabled: false });
    showNetworkMessage("vlan-interface-error", `${data.parent_interface} is missing from host inventory. Move this VLAN to an available trunk parent before enabling it.`);
    if (typeof cell.restoreOldValue === "function") {
      cell.restoreOldValue();
    }
    return;
  }
  if (data.is_new) {
    if (!hasRequiredVlanFields(data)) {
      return;
    }
    try {
      await postNetworkAction("/vlan-interfaces", data, csrf, { reload: false });
      showTransientGridStatus("Added");
      window.location.reload();
    } catch (error) {
      showNetworkMessage("vlan-interface-error", error instanceof Error ? error.message : "The VLAN interface could not be added.");
      if (typeof cell.restoreOldValue === "function") {
        cell.restoreOldValue();
      }
    }
    return;
  }
  try {
    await postNetworkAction(`/vlan-interfaces/${data.id}/edit`, data, csrf, { reload: false });
    showTransientGridStatus("Saved");
    await refreshNetworkSideStack();
  } catch (error) {
    showNetworkMessage("vlan-interface-error", error instanceof Error ? error.message : "The VLAN interface could not be saved.");
    if (typeof cell.restoreOldValue === "function") {
      cell.restoreOldValue();
    }
  }
}

async function deleteVlanInterfaceFromMenu(row, csrf) {
  clearCaMessage("vlan-interface-error");
  const data = row.getData();
  if (data.is_new) {
    return;
  }
  const confirmed = await requestConfirmation({
    title: `Delete ${data.name || `${data.parent_interface}.${data.vlan_id}`}?`,
    message: "This removes the VLAN interface from LabFoundry desired state. It will not touch the appliance until global appliance apply runs.",
    label: "Delete VLAN",
  });
  if (!confirmed) {
    return;
  }
  try {
    await postNetworkAction(`/vlan-interfaces/${data.id}/delete`, {}, csrf);
  } catch (error) {
    showNetworkMessage("vlan-interface-error", error instanceof Error ? error.message : "The VLAN interface could not be deleted.");
  }
}

function initializePhysicalInterfacesTable() {
  const tableElement = document.getElementById("physical-interfaces-table");
  if (!(tableElement instanceof HTMLElement)) {
    return;
  }
  const fallback = document.getElementById(tableElement.dataset.fallbackId || "");
  if (typeof Tabulator === "undefined") {
    showNetworkMessage("physical-interface-error", "Tabulator did not load. Showing the fallback table.");
    return;
  }
  const csrf = tableElement.dataset.csrf || "";
  const roleOptions = roleValues(JSON.parse(tableElement.dataset.roleOptions || "[]"));
  const ipv4MethodOptions = labeledValues(JSON.parse(tableElement.dataset.ipv4MethodOptions || "[]"), {
    static: "Static",
    dhcp: "DHCP",
  });
  const modeOptions = labeledValues(JSON.parse(tableElement.dataset.modeOptions || "[]"), {
    access: "Access (untagged)",
    trunk: "Trunk (tagged VLANs)",
    unused: "Unused",
  });
  const rows = JSON.parse(tableElement.dataset.interfaces || "[]");
  try {
    new Tabulator(tableElement, {
      data: rows,
      index: "id",
      layout: "fitColumns",
      height: "420px",
      rowHeight: 28,
      placeholder: "No physical interfaces discovered.",
      reactiveData: false,
      rowContextMenu: [
        {
          label: (component) => (component.getData().admin_up ? "Disable interface" : "Enable interface"),
          disabled: (component) => {
            const data = component.getData();
            return data.role === "management" && data.admin_up;
          },
          action: (event, row) => togglePhysicalInterfaceFromMenu(row, csrf),
        },
        {
          label: "Convert DHCP lease to static",
          disabled: (component) => {
            const data = component.getData();
            return data.role !== "management" || data.ipv4_method !== "dhcp" || (!data.host_ip_cidr && !data.host_ipv6_cidr);
          },
          action: (event, row) => convertManagementDhcpInterfaceToStatic(row, csrf),
        },
        {
          label: "Forget missing interface",
          disabled: (component) => component.getData().oper_state !== "missing",
          action: (event, row) => forgetPhysicalInterfaceFromMenu(row, csrf),
        },
      ],
      columns: [
        { title: "Name", field: "name", width: 100, headerSort: false },
        { title: "MAC", field: "mac_address", minWidth: 170, headerSort: false },
        { title: "Driver", field: "driver", width: 110 },
        { title: "Speed", field: "speed", width: 110 },
        { title: "Observed IPv4", field: "host_ip_cidr", minWidth: 150, headerSort: false },
        { title: "Observed IPv6", field: "host_ipv6_cidr", minWidth: 180, headerSort: false },
        {
          title: "IPv4 Method",
          field: "ipv4_method",
          editor: "list",
          editorParams: { values: ipv4MethodOptions },
          editable: (cell) => cell.getRow().getData().role === "management",
          formatter: (cell) => escapeHtml(ipv4MethodOptions[cell.getValue()] || cell.getValue() || "Static"),
          width: 130,
          cellEdited: async (cell) => {
            const row = cell.getRow();
            const data = row.getData();
            if (data.ipv4_method === "dhcp" && data.role !== "management") {
              showNetworkMessage("physical-interface-error", "IPv4 DHCP is available only for the management interface.");
              if (typeof cell.restoreOldValue === "function") {
                cell.restoreOldValue();
              }
              return;
            }
            if (data.ipv4_method === "dhcp") {
              await row.update({ ip_cidr: "" });
            }
            await autoSavePhysicalInterface(cell, csrf);
          },
        },
        {
          title: "IPv4 CIDR",
          field: "ip_cidr",
          editor: (cell, onRendered, success, cancel, editorParams) => {
            const data = cell.getRow().getData();
            if (data.mode === "trunk" || data.ipv4_method === "dhcp") {
              cancel();
              return document.createElement("span");
            }
            return cidrInputEditor(cell, onRendered, success, cancel, editorParams);
          },
          editorParams: { family: "ipv4", placeholder: "192.168.50.1/24" },
          editable: (cell) => cell.getRow().getData().mode !== "trunk",
          formatter: (cell) => {
            const data = cell.getRow().getData();
            if (data.mode === "trunk") {
              return "";
            }
            if (data.ipv4_method === "dhcp") {
              return '<span class="status-pill muted">DHCP</span>';
            }
            return dnsAddRowHintFormatter(cell, "192.168.50.1/24");
          },
          minWidth: 160,
          cellEdited: (cell) => autoSavePhysicalInterface(cell, csrf),
        },
        {
          title: "IPv6 CIDR",
          field: "ipv6_cidr",
          editor: cidrInputEditor,
          editorParams: { family: "ipv6", placeholder: "fd00:50::1/64" },
          editable: (cell) => cell.getRow().getData().mode !== "trunk",
          formatter: (cell) => (cell.getRow().getData().mode === "trunk" ? "" : dnsAddRowHintFormatter(cell, "fd00:50::1/64")),
          minWidth: 180,
          cellEdited: (cell) => autoSavePhysicalInterface(cell, csrf),
        },
        {
          title: "Role",
          field: "role",
          editor: "list",
          editorParams: { values: roleOptions },
          editable: (cell) => cell.getRow().getData().mode !== "trunk",
          formatter: physicalRoleFormatter,
          width: 125,
          cellEdited: (cell) => autoSavePhysicalInterface(cell, csrf),
        },
        {
          title: "Link Type",
          field: "mode",
          editor: "list",
          editorParams: { values: modeOptions },
          editable: (cell) => Number(cell.getRow().getData().vlan_count || 0) === 0,
          formatter: (cell) => physicalLinkTypeFormatter(cell, modeOptions),
          cellClick: (event, cell) => {
            const data = cell.getRow().getData();
            const vlanCount = Number(data.vlan_count || 0);
            if (vlanCount > 0) {
              showNetworkMessage(
                "physical-interface-error",
                `${data.name} is the parent of ${vlanCount} VLAN interface${vlanCount === 1 ? "" : "s"}. Move or delete those VLANs before changing the link type.`,
              );
            }
          },
          minWidth: 220,
          cellEdited: async (cell) => {
            if (cell.getValue() === "trunk") {
              await cell.getRow().update({ role: "unused", ipv4_method: "static", ip_cidr: "", ipv6_cidr: "" });
            }
            await autoSavePhysicalInterface(cell, csrf);
            cell.getRow().reformat();
          },
        },
        {
          title: "MTU",
          field: "mtu",
          editor: "number",
          width: 90,
          cellEdited: (cell) => autoSavePhysicalInterface(cell, csrf),
        },
        {
          title: "Admin Up",
          field: "admin_up",
          formatter: adminStateFormatter,
          editor: "tickCross",
          editable: (cell) => {
            const data = cell.getRow().getData();
            return data.role !== "management" || !data.admin_up;
          },
          hozAlign: "center",
          width: 110,
          headerSort: false,
          cellEdited: (cell) => autoSavePhysicalInterface(cell, csrf),
        },
        { title: "Oper", field: "oper_state", formatter: operStateFormatter, width: 105, headerSort: false },
        { title: "Source", field: "inventory_source", width: 100, headerSort: false },
      ],
    });
    if (fallback) {
      fallback.classList.add("hidden");
    }
  } catch (error) {
    showNetworkMessage("physical-interface-error", error instanceof Error ? error.message : "Tabulator could not render. Showing the fallback table.");
  }
}

function initializeVlanInterfacesTable() {
  const tableElement = document.getElementById("vlan-interfaces-table");
  if (!(tableElement instanceof HTMLElement)) {
    return;
  }
  const fallback = document.getElementById(tableElement.dataset.fallbackId || "");
  if (typeof Tabulator === "undefined") {
    showNetworkMessage("vlan-interface-error", "Tabulator did not load. Showing the fallback table.");
    return;
  }
  const csrf = tableElement.dataset.csrf || "";
  const parentOptionRows = JSON.parse(tableElement.dataset.parentOptions || "[]");
  const parentOptions = parentOptionRows.map((item) => (typeof item === "string" ? { name: item, label: item } : item));
  const roleOptions = roleValues(JSON.parse(tableElement.dataset.roleOptions || "[]"));
  const parentValues = Object.fromEntries(parentOptions.map((item) => [item.name, item.label || item.name]));
  const parentMtus = Object.fromEntries(parentOptions.map((item) => [item.name, Number(item.mtu) || 1500]));
  const defaultParent = parentOptions[0]?.name || "";
  const defaultMtu = parentMtus[defaultParent] || 1500;
  const rows = [...JSON.parse(tableElement.dataset.vlans || "[]"), newVlanInterfaceRow(defaultParent, defaultMtu)];
  try {
    new Tabulator(tableElement, {
      data: rows,
      index: "id",
      layout: "fitColumns",
      height: "420px",
      rowHeight: 28,
      placeholder: "No VLAN interfaces configured.",
      reactiveData: false,
      rowContextMenu: [
        {
          label: "Delete VLAN",
          action: (event, row) => deleteVlanInterfaceFromMenu(row, csrf),
        },
      ],
      columns: lockNewRecordColumns([
        {
          title: "Add VLAN +",
          field: "add_vlan",
          formatter: (cell) => {
            const data = cell.getRow().getData();
            if (data.is_new) {
              return '<span class="add-row-hint">+ Add VLAN</span>';
            }
            return "";
          },
          width: 115,
          headerSort: false,
          editable: false,
          cellClick: (event, cell) => activateNewVlanRow(cell),
        },
        {
          title: "VLAN ID",
          field: "vlan_id",
          editor: "number",
          width: 100,
          cellEdited: (cell) => autoSaveVlanId(cell, csrf),
        },
        {
          title: "Parent",
          field: "parent_interface",
          editor: "list",
          editorParams: { values: parentValues },
          formatter: (cell) => {
            const value = cell.getValue();
            if (!value) {
              return '<span class="add-row-hint">mark a physical NIC as trunk first</span>';
            }
            return escapeHtml(value);
          },
          minWidth: 120,
          cellEdited: (cell) => autoSaveVlanParent(cell, csrf, parentMtus),
        },
        {
          title: "Name",
          field: "name",
          formatter: (cell) => escapeHtml(cell.getValue()),
          minWidth: 140,
          headerSort: false,
          editable: false,
        },
        {
          title: "IPv4 CIDR",
          field: "ip_cidr",
          editor: cidrInputEditor,
          editorParams: { family: "ipv4", placeholder: "192.168.50.1/24" },
          formatter: (cell) => dnsAddRowHintFormatter(cell, "192.168.50.1/24"),
          minWidth: 170,
          cellEdited: (cell) => autoSaveVlanInterface(cell, csrf),
        },
        {
          title: "IPv6 CIDR",
          field: "ipv6_cidr",
          editor: cidrInputEditor,
          editorParams: { family: "ipv6", placeholder: "fd00:50::1/64" },
          formatter: (cell) => dnsAddRowHintFormatter(cell, "fd00:50::1/64"),
          minWidth: 180,
          cellEdited: (cell) => autoSaveVlanInterface(cell, csrf),
        },
        {
          title: "MTU",
          field: "mtu",
          editor: "number",
          width: 90,
          cellEdited: (cell) => autoSaveVlanInterface(cell, csrf),
        },
        {
          title: "Role",
          field: "role",
          editor: "list",
          editorParams: { values: roleOptions },
          minWidth: 130,
          cellEdited: (cell) => autoSaveVlanInterface(cell, csrf),
        },
        {
          title: "Admin Up",
          field: "enabled",
          formatter: vlanEnabledFormatter,
          editor: "tickCross",
          editable: (cell) => !cell.getRow().getData().parent_missing,
          hozAlign: "center",
          width: 100,
          headerSort: false,
          cellEdited: (cell) => autoSaveVlanInterface(cell, csrf),
        },
      ], "vlan_id"),
      rowFormatter: (row) => {
        markNewRecordRow(row, "vlan_id", "add_vlan");
        row.getElement().classList.toggle("locked-record-row", Boolean(row.getData().parent_missing));
      },
    });
    if (fallback) {
      fallback.classList.add("hidden");
    }
  } catch (error) {
    showNetworkMessage("vlan-interface-error", error instanceof Error ? error.message : "Tabulator could not render. Showing the fallback table.");
  }
}

function initializeDnsRecordsTable() {
  const tableElements = document.querySelectorAll(".dns-records-table");
  if (!tableElements.length) {
    return;
  }
  if (typeof Tabulator === "undefined") {
    showTableError("Tabulator did not load. Showing the fallback table.");
    return;
  }
  tableElements.forEach((tableElement) => initializeDnsRecordsTableElement(tableElement));
}

function initializeDnsRecordsTableElement(tableElement) {
  const fallback = document.getElementById(tableElement.dataset.fallbackId || "");
  const domain = tableElement.dataset.domain || "";
  const records = [...JSON.parse(tableElement.dataset.records || "[]"), newDnsRecordRow(domain, tableElement.dataset.suggestedIpv4 || "")];
  const csrf = tableElement.dataset.csrf || "";

  try {
    const table = new Tabulator(tableElement, {
      data: records,
      index: "id",
      layout: "fitColumns",
      height: "420px",
      rowHeight: 28,
      placeholder: "No DNS records configured.",
      reactiveData: false,
      rowContextMenu: [
        {
          label: "Delete record",
          action: (event, row) => deleteDnsRecordFromMenu(row, csrf),
        },
      ],
      columns: [
        {
          title: "Host",
          field: "host_label",
          editor: "input",
          editable: dnsRecordCellEditable,
          formatter: (cell) => dnsAddRowHintFormatter(cell, "+ Add record here"),
          minWidth: 180,
          cellEdited: (cell) => autoSaveDnsRecord(cell, csrf),
        },
        { title: "Domain", field: "domain", formatter: dnsRecordDomainFormatter, minWidth: 190, headerSort: false },
        {
          title: "Family",
          field: "record_type",
          editor: "list",
          editable: dnsRecordCellEditable,
          editorParams: { values: dnsRecordTypeOptions() },
          formatter: (cell) => dnsRecordTypeLabel(cell.getValue()),
          width: 130,
          headerSort: false,
          cellEdited: (cell) => autoSaveDnsRecord(cell, csrf),
        },
        {
          title: "Value",
          field: "address",
          editor: "input",
          editable: dnsRecordCellEditable,
          formatter: (cell) => dnsAddRowHintFormatter(cell, dnsRecordValueHint(cell.getRow().getData().record_type)),
          minWidth: 170,
          cellEdited: (cell) => autoSaveDnsRecord(cell, csrf),
        },
        {
          title: "Reverse/PTR",
          field: "reverse_label",
          formatter: reverseStatusFormatter,
          minWidth: 260,
          headerSort: false,
        },
        {
          title: "Enabled",
          field: "enabled",
          formatter: labFoundryBooleanFormatter,
          editor: "tickCross",
          editable: dnsRecordCellEditable,
          hozAlign: "center",
          width: 110,
          headerSort: false,
          cellEdited: (cell) => autoSaveDnsRecord(cell, csrf),
        },
        {
          title: "Description",
          field: "description",
          editor: "input",
          editable: dnsRecordCellEditable,
          formatter: (cell) => dnsAddRowHintFormatter(cell, "optional note..."),
          minWidth: 220,
          cellEdited: (cell) => autoSaveDnsRecord(cell, csrf),
        },
      ],
      rowFormatter: (row) => {
        markNewRecordRow(row, "host_label");
      },
    });
    labFoundryDnsRecordTables.set(tableElement, table);
    if (fallback) {
      fallback.classList.add("hidden");
    }
    redrawDnsRecordTables(tableElement);
  } catch (error) {
    showTableError(error instanceof Error ? error.message : "Tabulator could not render. Showing the fallback table.");
  }
}

function initializeDhcpScopesTable() {
  const tableElement = document.getElementById("dhcp-scopes-table");
  if (!(tableElement instanceof HTMLElement)) {
    return;
  }
  const fallback = document.getElementById(tableElement.dataset.fallbackId || "");
  if (typeof Tabulator === "undefined") {
    showDhcpScopeError("Tabulator did not load. Showing the fallback table.");
    return;
  }
  const csrf = tableElement.dataset.csrf || "";
  const interfaceOptions = JSON.parse(tableElement.dataset.interfaceOptions || "[]");
  const scopeDefaults = JSON.parse(tableElement.dataset.scopeDefaults || "{}");
  const domainOptions = JSON.parse(tableElement.dataset.domainOptions || "[]");
  const defaultInterface = interfaceOptions[0] || "eth1";
  const existingRows = JSON.parse(tableElement.dataset.scopes || "[]");
  const existingScopeNames = new Set([
    ...(Array.isArray(scopeDefaults.existing_names) ? scopeDefaults.existing_names : []),
    ...existingRows.map((row) => normalizeDhcpZoneName(row.name)).filter(Boolean),
  ]);
  const rows = [...existingRows, newDhcpScopeRow(defaultInterface, scopeDefaults)];
  const interfaceValues = Object.fromEntries(interfaceOptions.map((item) => [item, item]));
  const domainValues = Object.fromEntries(domainOptions.map((item) => [item, item]));
  const handleDhcpScopeEdited = (cell) => {
    const row = cell.getRow();
    const data = row.getData();
    if (data.is_new) {
      if (cell.getField() === "name" && isUniqueNewDhcpScopeName(data, existingScopeNames)) {
        if (!data.address_family) {
          data.address_family = dhcpDefaultFamilyForInterface(scopeDefaults, data.interface_name || defaultInterface);
        }
        if (!data.interface_name) {
          data.interface_name = defaultInterface;
        }
        if (!data.lease_time) {
          data.lease_time = "12h";
        }
      }
      if (["name", "interface_name", "address_family"].includes(cell.getField())) {
        const updated = applyDhcpScopeInterfaceDefaults(data, scopeDefaults, { overwrite: cell.getField() !== "name" });
        row.update(updated);
      }
      row.reformat();
    }
    return autoSaveDhcpScope(cell, csrf);
  };
  try {
    new Tabulator(tableElement, {
      data: rows,
      index: "id",
      layout: "fitColumns",
      height: "300px",
      rowHeight: 28,
      placeholder: "No DHCP IP zones configured.",
      reactiveData: false,
      rowContextMenu: [
        {
          label: "Delete IP zone",
          action: (event, row) => deleteDhcpScopeFromMenu(row, csrf),
        },
      ],
      columns: [
        {
          title: "Zone",
          field: "name",
          editor: "input",
          editable: true,
          formatter: (cell) => dnsAddRowHintFormatter(cell, "+ Add IP zone here"),
          minWidth: 140,
          cellEdited: handleDhcpScopeEdited,
        },
        {
          title: "Family",
          field: "address_family",
          editor: "list",
          editable: (cell) => dhcpScopeFamilyEditable(cell, existingScopeNames),
          editorParams: { values: { ipv4: "IPv4", ipv6: "IPv6" } },
          formatter: (cell) => {
            const value = cell.getValue();
            if (cell.getRow().getData().is_new && !value) {
              return "";
            }
            return value === "ipv6" ? "IPv6" : "IPv4";
          },
          width: 100,
          cellEdited: handleDhcpScopeEdited,
        },
        {
          title: "Interface",
          field: "interface_name",
          editor: "list",
          editable: (cell) => dhcpScopeCellEditable(cell, existingScopeNames),
          editorParams: { values: interfaceValues },
          minWidth: 120,
          cellEdited: handleDhcpScopeEdited,
        },
        {
          title: "Gateway",
          field: "site_address",
          editor: "input",
          editable: (cell) => dhcpScopeCellEditable(cell, existingScopeNames),
          formatter: (cell) => dnsAddRowHintFormatter(cell, "gateway..."),
          minWidth: 140,
          cellEdited: handleDhcpScopeEdited,
        },
        {
          title: "Prefix",
          field: "prefix_length",
          editor: "number",
          editable: (cell) => dhcpScopeCellEditable(cell, existingScopeNames),
          width: 90,
          cellEdited: handleDhcpScopeEdited,
        },
        {
          title: "Range",
          field: "range_expression",
          editor: "input",
          editable: (cell) => dhcpScopeCellEditable(cell, existingScopeNames),
          formatter: dhcpRangeFormatter,
          cellMouseEnter: (event, cell) => showDhcpRangeTooltip(event, cell.getRow().getData()),
          cellMouseMove: moveDhcpRangeTooltip,
          cellMouseLeave: hideDhcpRangeTooltip,
          minWidth: 240,
          cellEdited: handleDhcpScopeEdited,
        },
        {
          title: "Lease",
          field: "lease_time",
          editor: "input",
          editable: (cell) => dhcpScopeCellEditable(cell, existingScopeNames),
          width: 90,
          cellEdited: handleDhcpScopeEdited,
        },
        {
          title: "DNS",
          field: "dns_server",
          editor: "input",
          editable: (cell) => dhcpScopeCellEditable(cell, existingScopeNames),
          formatter: (cell) => dnsAddRowHintFormatter(cell, "DNS IP..."),
          minWidth: 140,
          cellEdited: handleDhcpScopeEdited,
        },
        {
          title: "NTP",
          field: "ntp_server",
          editor: "input",
          editable: (cell) => dhcpScopeCellEditable(cell, existingScopeNames),
          formatter: (cell) => dnsAddRowHintFormatter(cell, "NTP IP..."),
          minWidth: 140,
          cellEdited: handleDhcpScopeEdited,
        },
        {
          title: "Domain",
          field: "domain_name",
          editor: "list",
          editable: (cell) => dhcpScopeCellEditable(cell, existingScopeNames),
          editorParams: {
            values: domainValues,
            autocomplete: true,
            allowEmpty: false,
          },
          minWidth: 180,
          cellEdited: handleDhcpScopeEdited,
        },
        {
          title: "Enabled",
          field: "enabled",
          formatter: labFoundryBooleanFormatter,
          editor: "tickCross",
          editable: (cell) => dhcpScopeCellEditable(cell, existingScopeNames),
          hozAlign: "center",
          width: 100,
          headerSort: false,
          cellEdited: handleDhcpScopeEdited,
        },
      ],
      rowFormatter: (row) => {
        const data = row.getData();
        row.getElement().classList.toggle("new-record-row", Boolean(data.is_new));
        row.getElement().classList.toggle("new-record-row-locked", Boolean(data.is_new && !isUniqueNewDhcpScopeName(data, existingScopeNames)));
      },
    });
    if (fallback) {
      fallback.classList.add("hidden");
    }
  } catch (error) {
    showDhcpScopeError(error instanceof Error ? error.message : "Tabulator could not render. Showing the fallback table.");
  }
}

function initializeDhcpOptionsTable() {
  const tableElement = document.getElementById("dhcp-options-table");
  if (!(tableElement instanceof HTMLElement)) {
    return;
  }
  const fallback = document.getElementById(tableElement.dataset.fallbackId || "");
  if (typeof Tabulator === "undefined") {
    showDhcpOptionError("Tabulator did not load. Showing the fallback table.");
    return;
  }
  const csrf = tableElement.dataset.csrf || "";
  const scopeOptions = JSON.parse(tableElement.dataset.scopeOptions || "[]");
  const scopeValues = Object.fromEntries(scopeOptions.map((item) => [item.id, item.label]));
  const rows = [...JSON.parse(tableElement.dataset.options || "[]"), newDhcpOptionRow()];
  try {
    new Tabulator(tableElement, {
      data: rows,
      index: "id",
      layout: "fitColumns",
      height: "260px",
      rowHeight: 28,
      placeholder: "No DHCP options configured.",
      reactiveData: false,
      rowContextMenu: [
        {
          label: "Delete option",
          action: (event, row) => deleteDhcpOptionFromMenu(row, csrf),
        },
      ],
      columns: [
        {
          title: "Applies to",
          field: "scope_id",
          editor: "list",
          editorParams: { values: scopeValues },
          formatter: (cell) => scopeValues[cell.getValue()] || "Global defaults",
          minWidth: 150,
          cellEdited: (cell) => autoSaveDhcpOption(cell, csrf),
        },
        {
          title: "Option",
          field: "option_code",
          editor: "input",
          formatter: (cell) => dnsAddRowHintFormatter(cell, "+ Add DHCP option here"),
          minWidth: 150,
          cellEdited: (cell) => autoSaveDhcpOption(cell, csrf),
        },
        {
          title: "Value",
          field: "value",
          editor: "input",
          formatter: (cell) => dnsAddRowHintFormatter(cell, "option value..."),
          minWidth: 220,
          cellEdited: (cell) => autoSaveDhcpOption(cell, csrf),
        },
        {
          title: "Enabled",
          field: "enabled",
          formatter: labFoundryBooleanFormatter,
          editor: "tickCross",
          hozAlign: "center",
          width: 100,
          headerSort: false,
          cellEdited: (cell) => autoSaveDhcpOption(cell, csrf),
        },
        {
          title: "Description",
          field: "description",
          editor: "input",
          formatter: (cell) => dnsAddRowHintFormatter(cell, "optional note..."),
          minWidth: 220,
          cellEdited: (cell) => autoSaveDhcpOption(cell, csrf),
        },
      ],
      rowFormatter: (row) => {
        row.getElement().classList.toggle("new-record-row", Boolean(row.getData().is_new));
      },
    });
    if (fallback) {
      fallback.classList.add("hidden");
    }
  } catch (error) {
    showDhcpOptionError(error instanceof Error ? error.message : "Tabulator could not render. Showing the fallback table.");
  }
}

function initializeDhcpReservationsTable() {
  const tableElement = document.getElementById("dhcp-reservations-table");
  if (!(tableElement instanceof HTMLElement)) {
    return;
  }
  const fallback = document.getElementById(tableElement.dataset.fallbackId || "");
  if (typeof Tabulator === "undefined") {
    showDhcpReservationError("Tabulator did not load. Showing the fallback table.");
    return;
  }
  const csrf = tableElement.dataset.csrf || "";
  const rows = [...JSON.parse(tableElement.dataset.reservations || "[]"), newDhcpReservationRow()];
  const handleDhcpReservationEdited = (cell) => {
    if (cell.getRow().getData().is_new) {
      cell.getRow().reformat();
    }
    return autoSaveDhcpReservation(cell, csrf);
  };
  try {
    new Tabulator(tableElement, {
      data: rows,
      index: "id",
      layout: "fitColumns",
      height: "420px",
      rowHeight: 28,
      placeholder: "No DHCP reservations configured.",
      reactiveData: false,
      rowContextMenu: [
        {
          label: "Delete reservation",
          action: (event, row) => deleteDhcpReservationFromMenu(row, csrf),
        },
      ],
      columns: [
        {
          title: "DNS name / FQDN",
          field: "hostname",
          editor: "input",
          formatter: (cell) => dnsAddRowHintFormatter(cell, "+ Add reservation here"),
          minWidth: 180,
          cellEdited: handleDhcpReservationEdited,
        },
        {
          title: "MAC address",
          field: "mac_address",
          editor: "input",
          editable: dhcpReservationCellEditable,
          formatter: (cell) => dhcpReservationAddRowHintFormatter(cell, "enter MAC..."),
          minWidth: 180,
          cellEdited: handleDhcpReservationEdited,
        },
        {
          title: "IP address",
          field: "ip_address",
          editor: "input",
          editable: dhcpReservationCellEditable,
          formatter: (cell) => dhcpReservationAddRowHintFormatter(cell, "enter IP..."),
          minWidth: 150,
          cellEdited: handleDhcpReservationEdited,
        },
        {
          title: "Zone",
          field: "zone_name",
          formatter: (cell) => escapeHtml(cell.getValue() || "-"),
          minWidth: 120,
          editor: false,
        },
        {
          title: "Enabled",
          field: "enabled",
          formatter: labFoundryBooleanFormatter,
          editor: "tickCross",
          editable: dhcpReservationCellEditable,
          hozAlign: "center",
          width: 110,
          headerSort: false,
          cellEdited: handleDhcpReservationEdited,
        },
        {
          title: "Description",
          field: "description",
          editor: "input",
          editable: dhcpReservationCellEditable,
          formatter: (cell) => dhcpReservationAddRowHintFormatter(cell, "optional note..."),
          minWidth: 220,
          cellEdited: handleDhcpReservationEdited,
        },
      ],
      rowFormatter: (row) => {
        row.getElement().classList.toggle("new-record-row", Boolean(row.getData().is_new));
      },
    });
    if (fallback) {
      fallback.classList.add("hidden");
    }
  } catch (error) {
    showDhcpReservationError(error instanceof Error ? error.message : "Tabulator could not render. Showing the fallback table.");
  }
}

function initializeEsxiPxeHostsTable() {
  const tableElement = document.getElementById("esxi-pxe-hosts-table");
  if (!(tableElement instanceof HTMLElement)) {
    return;
  }
  const fallback = document.getElementById(tableElement.dataset.fallbackId || "");
  if (typeof Tabulator === "undefined") {
    showEsxiHostError("Tabulator did not load. Showing the fallback table.");
    return;
  }
  const csrf = tableElement.dataset.csrf || "";
  const canWrite = tableElement.dataset.canWrite === "true";
  const kickstartOptions = JSON.parse(tableElement.dataset.kickstartOptions || "[]");
  const isoOptions = JSON.parse(tableElement.dataset.isoOptions || "[]");
  const kickstartValues = Object.fromEntries(kickstartOptions.map((item) => [item.id, item.label]));
  const isoValues = Object.fromEntries(isoOptions.map((item) => [item.id, item.label]));
  const defaultIsoPath = isoOptions.find((item) => item.id)?.id || "";
  const rows = [...JSON.parse(tableElement.dataset.hosts || "[]"), newEsxiHostRow(defaultIsoPath)];
  try {
    new Tabulator(tableElement, {
      data: rows,
      index: "id",
      layout: "fitColumns",
      height: "360px",
      rowHeight: 30,
      placeholder: "No ESXi PXE host references configured.",
      reactiveData: false,
      rowContextMenu: canWrite ? [
        {
          label: "Delete host reference",
          action: (event, row) => deleteEsxiHost(row, csrf),
        },
      ] : false,
      columns: lockNewRecordColumns([
        {
          title: "Host",
          field: "hostname",
          editor: canWrite ? "input" : false,
          editable: (cell) => !cell.getRow().getData().is_default,
          formatter: (cell) => {
            const data = cell.getRow().getData();
            if (data.is_default) {
              return "Default / undefined MACs";
            }
            return dnsAddRowHintFormatter(cell, "+ Add host reference here");
          },
          minWidth: 200,
          cellEdited: (cell) => autoSaveEsxiHost(cell, csrf),
        },
        {
          title: "MAC address",
          field: "mac_address",
          editor: canWrite ? "input" : false,
          editable: (cell) => !cell.getRow().getData().is_default,
          formatter: (cell) => {
            if (cell.getRow().getData().is_default) {
              return "*";
            }
            return dnsAddRowHintFormatter(cell, "00:50:56:aa:bb:cc");
          },
          minWidth: 170,
          cellEdited: (cell) => autoSaveEsxiHost(cell, csrf),
        },
        {
          title: "IP address",
          field: "ip_address",
          editor: canWrite ? "input" : false,
          editable: (cell) => !cell.getRow().getData().is_default,
          formatter: (cell) => {
            if (cell.getRow().getData().is_default) {
              return "DHCP";
            }
            return dnsAddRowHintFormatter(cell, "DHCP");
          },
          minWidth: 140,
          cellEdited: (cell) => autoSaveEsxiHost(cell, csrf),
        },
        {
          title: "Kickstart",
          field: "kickstart_id",
          editor: canWrite ? "list" : false,
          editorParams: { values: kickstartValues },
          formatter: (cell) => kickstartValues[cell.getValue()] || "No Kickstart",
          minWidth: 180,
          cellEdited: (cell) => autoSaveEsxiHost(cell, csrf),
        },
        {
          title: "Installer ISO",
          field: "installer_iso_path",
          editor: canWrite ? "list" : false,
          editorParams: { values: isoValues, autocomplete: true },
          formatter: (cell) => isoValues[cell.getValue()] || "No ISO selected",
          minWidth: 320,
          cellEdited: (cell) => autoSaveEsxiHost(cell, csrf),
        },
        {
          title: "Variables JSON",
          field: "variables_json",
          editor: canWrite ? "input" : false,
          editable: (cell) => !cell.getRow().getData().is_default,
          formatter: (cell) => {
            if (cell.getRow().getData().is_default) {
              return "";
            }
            return dnsAddRowHintFormatter(cell, '{"custom_name":"value"}');
          },
          minWidth: 240,
          cellEdited: (cell) => autoSaveEsxiHost(cell, csrf),
        },
        {
          title: "Enabled",
          field: "enabled",
          formatter: labFoundryBooleanFormatter,
          editor: canWrite ? "tickCross" : false,
          hozAlign: "center",
          width: 100,
          headerSort: false,
          cellEdited: (cell) => autoSaveEsxiHost(cell, csrf),
        },
      ], "hostname"),
      rowFormatter: (row) => {
        const data = row.getData();
        markNewRecordRow(row, "hostname");
        row.getElement().classList.toggle("managed-record-row", Boolean(data.is_default));
      },
    });
    if (fallback) {
      fallback.classList.add("hidden");
    }
  } catch (error) {
    showEsxiHostError(error instanceof Error ? error.message : "Tabulator could not render. Showing the fallback table.");
  }
}

function initializeHostsFileEditor() {
  document.querySelectorAll(".hosts-file-input").forEach((input) => {
    if (!(input instanceof HTMLInputElement)) {
      return;
    }
    input.addEventListener("change", async () => {
      const editor = document.getElementById(input.dataset.editorId || "");
      if (!(editor instanceof HTMLTextAreaElement)) {
        return;
      }
      const file = input.files?.[0];
      if (!file) {
        return;
      }
      const fileText = await file.text();
      if (window.LabFoundryCodeMirror && typeof window.LabFoundryCodeMirror.setValue === "function") {
        window.LabFoundryCodeMirror.setValue(editor, fileText);
        window.LabFoundryCodeMirror.focus(editor);
        return;
      }
      editor.value = fileText;
      editor.dispatchEvent(new Event("input", { bubbles: true }));
      editor.focus();
    });
  });
}

function initializeCodeMirrorEditors() {
  if (!window.LabFoundryCodeMirror || typeof window.LabFoundryCodeMirror.enhanceTextarea !== "function") {
    return;
  }
  document.querySelectorAll("textarea[data-codemirror-editor]").forEach((textarea) => {
    if (!(textarea instanceof HTMLTextAreaElement)) {
      return;
    }
    const view = window.LabFoundryCodeMirror.enhanceTextarea(textarea, {
      language: textarea.dataset.codemirrorLanguage || "labfoundry-hosts",
    });
    installCodeMirrorPlainTextFallback(textarea, view);
  });
}

function installCodeMirrorPlainTextFallback(textarea, view) {
  if (!(textarea instanceof HTMLTextAreaElement) || !view || textarea.dataset.codemirrorLanguage !== "labfoundry-kickstart") {
    return;
  }
  const editorDom = view.dom instanceof HTMLElement ? view.dom : null;
  const contentDom = view.contentDOM instanceof HTMLElement ? view.contentDOM : editorDom?.querySelector?.(".cm-content");
  const eventTarget = editorDom || contentDom;
  if (!(eventTarget instanceof HTMLElement) || !(contentDom instanceof HTMLElement) || eventTarget.dataset.labfoundryPlainTextFallback === "1") {
    return;
  }
  eventTarget.dataset.labfoundryPlainTextFallback = "1";
  const insertTextAtSelection = (text) => {
    const selection = view.state?.selection?.main;
    if (!selection || typeof selection.from !== "number" || typeof selection.to !== "number") {
      return false;
    }
    const cursor = selection.from + text.length;
    view.dispatch({
      changes: { from: selection.from, to: selection.to, insert: text },
      selection: { anchor: cursor },
      scrollIntoView: true,
      userEvent: "input.type",
    });
    view.focus();
    return true;
  };
  eventTarget.addEventListener("pointerdown", () => view.focus());
  eventTarget.addEventListener("click", (event) => {
    event.stopPropagation();
    view.focus();
  });
  eventTarget.addEventListener(
    "beforeinput",
    (event) => {
      if (event.defaultPrevented || event.isComposing || event.inputType !== "insertText" || typeof event.data !== "string" || event.data.length === 0) {
        return;
      }
      event.preventDefault();
      insertTextAtSelection(event.data);
    },
    true
  );
  eventTarget.addEventListener(
    "keydown",
    (event) => {
      if (event.defaultPrevented || event.isComposing || event.ctrlKey || event.metaKey || event.altKey || event.key.length !== 1) {
        return;
      }
      event.preventDefault();
      insertTextAtSelection(event.key);
    },
    true
  );
}

function initializeKickstartEditorDirtyState() {
  document.querySelectorAll("[data-kickstart-editor-form]").forEach((form) => {
    if (!(form instanceof HTMLFormElement)) {
      return;
    }
    const status = form.querySelector("[data-kickstart-dirty-state]");
    const editor = form.querySelector("textarea[name='content']");
    if (!(status instanceof HTMLElement) || !(editor instanceof HTMLTextAreaElement)) {
      return;
    }
    let initialValue = editor.value;
    const refresh = () => {
      const dirty = editor.value !== initialValue;
      status.textContent = dirty ? "Unsaved changes" : "Saved";
      status.classList.toggle("dirty", dirty);
    };
    editor.addEventListener("input", refresh);
    form.addEventListener("submit", () => {
      initialValue = editor.value;
      refresh();
    });
    refresh();
  });
}

function initializeZoneEditors() {
  document.querySelectorAll(".zone-editor-form").forEach((form) => {
    const editor = form.querySelector(".zone-code-editor");
    const input = form.querySelector(".zone-file-input");
    if (!(form instanceof HTMLFormElement) || !(editor instanceof HTMLElement) || !(input instanceof HTMLInputElement)) {
      return;
    }
    const syncEditor = () => {
      input.value = editor.innerText.replace(/\u00a0/g, " ");
    };
    editor.addEventListener("input", syncEditor);
    form.addEventListener("submit", syncEditor);
    syncEditor();
  });
}

function requestConfirmation(options = {}) {
  const modal = document.getElementById("confirm-modal");
  const title = document.getElementById("confirm-modal-title");
  const message = document.getElementById("confirm-modal-message");
  const confirmButton = document.getElementById("confirm-modal-confirm");
  if (!(modal instanceof HTMLDialogElement) || !(title instanceof HTMLElement) || !(message instanceof HTMLElement) || !(confirmButton instanceof HTMLButtonElement)) {
    return Promise.resolve(false);
  }

  title.textContent = options.title || "Confirm action";
  message.textContent = options.message || "This action cannot be undone.";
  confirmButton.textContent = options.label || "Confirm";

  return new Promise((resolve) => {
    const handleClose = () => {
      modal.removeEventListener("close", handleClose);
      resolve(modal.returnValue === "confirm");
    };
    modal.addEventListener("close", handleClose);
    modal.showModal();
  });
}

function initializeConfirmationModals() {
  document.querySelectorAll("form[data-confirm-modal]").forEach((form) => {
    if (!(form instanceof HTMLFormElement)) {
      return;
    }
    form.addEventListener("submit", async (event) => {
      if (form.dataset.confirmed === "1") {
        delete form.dataset.confirmed;
        return;
      }
      event.preventDefault();
      const confirmed = await requestConfirmation({
        title: form.dataset.confirmTitle,
        message: form.dataset.confirmMessage,
        label: form.dataset.confirmLabel,
      });
      if (!confirmed) {
        return;
      }
      form.dataset.confirmed = "1";
      form.requestSubmit();
    });
  });
}

function setAutosaveStatus(element, message, state = "idle") {
  if (!element) {
    return;
  }
  element.textContent = message;
  element.dataset.state = state;
}

function updateApplianceApplySidebar(payload = {}) {
  const sidebar = document.querySelector("[data-appliance-apply-sidebar]");
  if (!(sidebar instanceof HTMLElement)) {
    return;
  }
  const pendingCount = Number(payload.pending_count || 0);
  const hasPending = pendingCount > 0;
  const title = sidebar.querySelector("[data-appliance-apply-sidebar-title]");
  const detail = sidebar.querySelector("[data-appliance-apply-sidebar-detail]");
  const badge = sidebar.querySelector("[data-appliance-apply-sidebar-badge]");
  sidebar.dataset.pendingCount = String(pendingCount);
  sidebar.classList.toggle("pending", hasPending);
  sidebar.classList.toggle("current", !hasPending);
  if (title instanceof HTMLElement) {
    title.textContent = hasPending ? "Review appliance changes" : "Appliance Apply";
  }
  if (detail instanceof HTMLElement) {
    detail.textContent = hasPending ? `${pendingCount} pending ${pendingCount === 1 ? "unit" : "units"}` : "Desired state current";
  }
  if (badge instanceof HTMLElement) {
    badge.textContent = hasPending ? "pending" : "current";
  }
}

async function refreshApplianceApplySidebar() {
  if (!document.querySelector("[data-appliance-apply-sidebar]")) {
    return;
  }
  const response = await fetch("/appliance-apply/status", {
    method: "GET",
    credentials: "same-origin",
    headers: { Accept: "application/json" },
  });
  if (!response.ok) {
    return;
  }
  updateApplianceApplySidebar(await response.json());
}

function initializeAutosaveForms() {
  document.querySelectorAll("[data-autosave-form]").forEach((form) => {
    if (!(form instanceof HTMLFormElement)) {
      return;
    }
    form.addEventListener("labfoundry:autosave-success", (event) => {
      updateDerivedListenAddressSummary(form, event.detail || {});
    });
    const statusElement = document.getElementById(form.dataset.autosaveStatusId || "");
    const inputAutosave = form.dataset.autosaveTrigger !== "change";
    let timer = 0;
    let inFlightRequest = null;

    const selectedFiles = () =>
      Array.from(form.querySelectorAll('input[type="file"]')).flatMap((input) =>
        input instanceof HTMLInputElement && input.files ? Array.from(input.files) : [],
      );

    const clearSelectedFileInputs = () => {
      form.querySelectorAll('input[type="file"]').forEach((input) => {
        if (input instanceof HTMLInputElement) {
          input.value = "";
        }
      });
    };

    const uploadProgress = () => form.querySelector("[data-autosave-upload-progress]");

    const resetUploadProgress = () => {
      const progress = uploadProgress();
      if (progress instanceof HTMLProgressElement) {
        progress.hidden = true;
        progress.value = 0;
        progress.max = 100;
      }
    };

    const autosaveErrorFromText = (text) => {
      try {
        const payload = JSON.parse(text || "{}");
        if (payload.detail) {
          return String(payload.detail);
        }
      } catch {
        // Fall through to the generic message below.
      }
      return "Settings could not be saved.";
    };

    const postWithFetch = async (actionUrl, formData) => {
      const controller = new AbortController();
      const request = { abort: () => controller.abort() };
      inFlightRequest = request;
      const response = await fetch(new URL(actionUrl, window.location.href), {
        method: form.method || "POST",
        body: formData,
        credentials: "same-origin",
        headers: { "X-LabFoundry-Autosave": "1" },
        signal: controller.signal,
      });
      if (!response.ok) {
        throw new Error(autosaveErrorFromText(await response.text()));
      }
      return { payload: await response.json(), request };
    };

    const postWithUploadProgress = (actionUrl, formData, files) =>
      new Promise((resolve, reject) => {
        const xhr = new XMLHttpRequest();
        const request = { abort: () => xhr.abort() };
        inFlightRequest = request;
        const progress = uploadProgress();
        if (progress instanceof HTMLProgressElement) {
          progress.hidden = false;
          progress.value = 0;
          progress.max = 100;
        }
        xhr.open((form.method || "POST").toUpperCase(), new URL(actionUrl, window.location.href).toString());
        xhr.withCredentials = true;
        xhr.setRequestHeader("X-LabFoundry-Autosave", "1");
        xhr.upload.addEventListener("progress", (event) => {
          if (!(progress instanceof HTMLProgressElement)) {
            return;
          }
          if (event.lengthComputable && event.total > 0) {
            const percent = Math.max(1, Math.min(100, Math.round((event.loaded / event.total) * 100)));
            progress.value = percent;
            setAutosaveStatus(statusElement, `Uploading ${files[0]?.name || "file"} (${percent}%)...`, "saving");
          } else {
            progress.removeAttribute("value");
            setAutosaveStatus(statusElement, `Uploading ${files[0]?.name || "file"}...`, "saving");
          }
        });
        xhr.addEventListener("load", () => {
          if (xhr.status < 200 || xhr.status >= 300) {
            reject(new Error(autosaveErrorFromText(xhr.responseText)));
            return;
          }
          try {
            resolve({ payload: JSON.parse(xhr.responseText || "{}"), request });
          } catch {
            reject(new Error("Settings could not be saved."));
          }
        });
        xhr.addEventListener("error", () => reject(new Error("Settings could not be saved.")));
        xhr.addEventListener("abort", () => reject(new DOMException("Request aborted.", "AbortError")));
        xhr.send(formData);
      });

    const save = async () => {
      window.clearTimeout(timer);
      if (inFlightRequest) {
        inFlightRequest.abort();
      }
      const files = selectedFiles();
      const hasFiles = files.length > 0;
      const uploadedFileName = files[0]?.name || "file";
      setAutosaveStatus(statusElement, hasFiles ? `Uploading ${files[0]?.name || "file"}...` : "Saving changes...", "saving");
      try {
        const actionUrl = form.getAttribute("action") || window.location.href;
        const formData = new FormData(form);
        const { payload, request } = hasFiles
          ? await postWithUploadProgress(actionUrl, formData, files)
          : await postWithFetch(actionUrl, formData);
        form.dispatchEvent(new CustomEvent("labfoundry:autosave-success", { detail: payload }));
        if (hasFiles) {
          clearSelectedFileInputs();
        }
        setAutosaveStatus(
          statusElement,
          hasFiles
            ? `Uploaded ${payload.tool_archive_name || payload.download_token_name || uploadedFileName}.`
            : payload.updated_at
              ? `Saved automatically at ${new Date(payload.updated_at).toLocaleTimeString()}.`
              : "Saved automatically.",
          "saved",
        );
        if (inFlightRequest === request) {
          inFlightRequest = null;
        }
        resetUploadProgress();
      } catch (error) {
        if (error instanceof DOMException && error.name === "AbortError") {
          return;
        }
        inFlightRequest = null;
        resetUploadProgress();
        setAutosaveStatus(statusElement, error instanceof Error ? error.message : "Settings could not be saved.", "error");
      } finally {
        if (!hasFiles) {
          resetUploadProgress();
        }
      }
    };

    const scheduleSave = () => {
      window.clearTimeout(timer);
      timer = window.setTimeout(save, 350);
    };

    form.addEventListener("input", (event) => {
      if (!inputAutosave) {
        return;
      }
      if (event.target instanceof HTMLInputElement && event.target.type === "file") {
        return;
      }
      scheduleSave();
    });
    form.addEventListener("change", scheduleSave);
    form.addEventListener("tag-editor:change", scheduleSave);
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      save();
    });
  });
}

function initializeSwitchFields() {
  document.querySelectorAll(".switch-field").forEach((field) => {
    if (!(field instanceof HTMLLabelElement)) {
      return;
    }
    const input = field.querySelector(".switch-input");
    if (!(input instanceof HTMLInputElement) || input.type !== "checkbox") {
      return;
    }
    field.addEventListener("click", (event) => {
      const target = event.target;
      if (!(target instanceof HTMLElement) || target === input || target.closest(".help-icon")) {
        return;
      }
      event.preventDefault();
      input.checked = !input.checked;
      input.dispatchEvent(new Event("input", { bubbles: true }));
      input.dispatchEvent(new Event("change", { bubbles: true }));
    });
  });
}

function initializeNonTabbableHelperControls() {
  document.querySelectorAll(".help-icon, .password-toggle").forEach((control) => {
    if (!(control instanceof HTMLElement)) {
      return;
    }
    control.setAttribute("tabindex", "-1");
  });
}

function initializeSecretToggles() {
  document.querySelectorAll("[data-secret-toggle]").forEach((button) => {
    if (!(button instanceof HTMLButtonElement)) {
      return;
    }
    const display = button.closest(".secret-value")?.querySelector("[data-secret-display]");
    if (!(display instanceof HTMLElement)) {
      return;
    }
    const mask = display.dataset.secretMask || "hidden";
    const secretText = display.dataset.secretText || "";
    display.textContent = mask;
    button.addEventListener("click", () => {
      const nextVisible = button.getAttribute("aria-pressed") !== "true";
      button.setAttribute("aria-pressed", nextVisible ? "true" : "false");
      button.setAttribute("aria-label", `${nextVisible ? "Hide" : "Show"} secrets key source`);
      display.textContent = nextVisible ? secretText : mask;
    });
  });
}

function updateFirewallDesiredState(payload = {}) {
  if (payload.enabled !== undefined) {
    document.querySelectorAll("[data-firewall-enabled-status]").forEach((status) => {
      if (!(status instanceof HTMLElement)) {
        return;
      }
      const enabled = Boolean(payload.enabled);
      status.textContent = enabled ? "enabled" : "disabled";
      status.classList.toggle("good", enabled);
      status.classList.toggle("muted", !enabled);
    });
  }

  const validationPanel = document.querySelector("[data-firewall-validation-panel]");
  if (!(validationPanel instanceof HTMLElement)) {
    return;
  }
  const errors = Array.isArray(payload.validation_errors) ? payload.validation_errors : [];
  const valid = payload.valid !== undefined ? Boolean(payload.valid) : errors.length === 0;
  const status = validationPanel.querySelector("[data-firewall-validation-status]");
  if (status instanceof HTMLElement) {
    status.textContent = valid ? "valid" : "needs attention";
    status.classList.toggle("good", valid);
    status.classList.toggle("warn", !valid);
  }
  const previewAnchor = validationPanel.querySelector("[data-config-preview-row]");
  let errorList = validationPanel.querySelector("[data-firewall-validation-errors]");
  let message = validationPanel.querySelector("[data-firewall-validation-message]");
  if (valid) {
    if (errorList instanceof HTMLElement) {
      errorList.remove();
    }
    if (!(message instanceof HTMLElement)) {
      message = document.createElement("p");
      message.className = "muted";
      message.setAttribute("data-firewall-validation-message", "");
      validationPanel.insertBefore(message, previewAnchor);
    }
    message.textContent =
      "The desired firewall state passes LabFoundry validation. Appliance validation still runs through the allowlisted nftables helper before apply.";
  } else {
    if (message instanceof HTMLElement) {
      message.remove();
    }
    if (!(errorList instanceof HTMLElement)) {
      errorList = document.createElement("ul");
      errorList.className = "error-list";
      errorList.setAttribute("data-firewall-validation-errors", "");
      validationPanel.insertBefore(errorList, previewAnchor);
    }
    errorList.innerHTML = "";
    errors.forEach((error) => {
      const item = document.createElement("li");
      item.textContent = error;
      errorList.append(item);
    });
  }
  const configPath = validationPanel.querySelector("[data-firewall-config-path]");
  if (configPath instanceof HTMLElement && typeof payload.config_path === "string") {
    configPath.textContent = payload.config_path;
  }
  const configPreview = validationPanel.querySelector("[data-firewall-config-preview]");
  if (configPreview instanceof HTMLElement && typeof payload.config_preview === "string") {
    configPreview.textContent = payload.config_preview;
    highlightConfigPreviewElement(configPreview);
  }
  const refreshStatus = validationPanel.querySelector("[data-firewall-validation-refresh]");
  if (refreshStatus instanceof HTMLElement) {
    const updatedAt = typeof payload.updated_at === "string" ? new Date(payload.updated_at) : new Date();
    const timestamp = Number.isNaN(updatedAt.getTime()) ? new Date() : updatedAt;
    refreshStatus.textContent = `Preview refreshed ${timestamp.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" })}.`;
    refreshStatus.dataset.state = "saved";
  }
  validationPanel.classList.remove("validation-panel-refreshed");
  void validationPanel.offsetWidth;
  validationPanel.classList.add("validation-panel-refreshed");
}

const FIREWALL_SOURCE_GROUP_SELECTION_KEY = "labfoundry:firewall:active-source-group";

function rememberFirewallSourceGroup(groupId) {
  try {
    window.localStorage.setItem(FIREWALL_SOURCE_GROUP_SELECTION_KEY, groupId || "");
  } catch {
    // Remembering the selected editor is only a convenience.
  }
}

function storedFirewallSourceGroup() {
  try {
    return window.localStorage.getItem(FIREWALL_SOURCE_GROUP_SELECTION_KEY) || "";
  } catch {
    return "";
  }
}

function showFirewallSourceGroupEditor(manager, groupId) {
  manager.querySelectorAll("[data-source-group-editor]").forEach((editor) => {
    if (!(editor instanceof HTMLElement)) {
      return;
    }
    const active = editor.dataset.sourceGroupId === groupId;
    editor.classList.toggle("active", active);
    if (active) {
      editor.removeAttribute("hidden");
    } else {
      editor.setAttribute("hidden", "");
    }
  });
}

function triggerSourceGroupAutosave(form) {
  if (form instanceof HTMLFormElement) {
    form.dispatchEvent(new Event("change", { bubbles: true }));
  }
}

function addSourceGroupEntry(form, value) {
  const list = form.querySelector("[data-source-group-entry-list]");
  const entry = String(value || "").trim();
  if (!(list instanceof HTMLElement) || !entry) {
    return;
  }
  const existingRows = Array.from(list.querySelectorAll(".source-group-entry-row"));
  if (entry.toLowerCase() === "any") {
    existingRows.forEach((row) => row.remove());
  } else {
    existingRows.forEach((row) => {
      const input = row.querySelector('input[name="group_entries"]');
      if (input instanceof HTMLInputElement && input.value.trim().toLowerCase() === "any") {
        row.remove();
      }
    });
  }
  const duplicate = Array.from(list.querySelectorAll('input[name="group_entries"]')).some((input) => input instanceof HTMLInputElement && input.value.trim().toLowerCase() === entry.toLowerCase());
  if (duplicate) {
    triggerSourceGroupAutosave(form);
    return;
  }
  const row = document.createElement("div");
  row.className = "source-group-entry-row";
  const input = document.createElement("input");
  input.type = "hidden";
  input.name = "group_entries";
  input.value = entry;
  const label = document.createElement("span");
  label.className = "source-group-entry-label";
  label.textContent = entry;
  const remove = document.createElement("button");
  remove.className = "icon-button source-group-entry-remove";
  remove.type = "button";
  remove.dataset.sourceGroupRemoveEntry = "";
  remove.setAttribute("aria-label", "Remove entry");
  remove.textContent = "x";
  row.append(input, label, remove);
  list.append(row);
  triggerSourceGroupAutosave(form);
}

function ensureSourceGroupHasEntry(form) {
  const list = form.querySelector("[data-source-group-entry-list]");
  if (!(list instanceof HTMLElement) || list.querySelector("[name='group_entries']")) {
    return;
  }
  addSourceGroupEntry(form, "any");
}

function initializeFirewallSourceGroupManager() {
  const renameModal = document.getElementById("firewall-rename-group-modal");
  const renameForm = renameModal instanceof HTMLDialogElement ? renameModal.querySelector("form") : null;
  const renameGroupId = renameForm instanceof HTMLFormElement ? renameForm.querySelector('input[name="group_id"]') : null;
  const renameGroupName = renameForm instanceof HTMLFormElement ? renameForm.querySelector('input[name="group_name"]') : null;
  if (renameModal instanceof HTMLDialogElement) {
    renameModal.querySelectorAll("[data-firewall-rename-group-cancel]").forEach((button) => {
      button.addEventListener("click", () => renameModal.close());
    });
  }

  document.querySelectorAll("[data-source-group-manager]").forEach((manager) => {
    if (!(manager instanceof HTMLElement)) {
      return;
    }
    const select = manager.querySelector("[data-source-group-select]");
    if (!(select instanceof HTMLSelectElement)) {
      return;
    }
    const storedGroup = storedFirewallSourceGroup();
    if (storedGroup && Array.from(select.options).some((option) => option.value === storedGroup)) {
      select.value = storedGroup;
    }
    showFirewallSourceGroupEditor(manager, select.value);
    select.addEventListener("change", () => {
      rememberFirewallSourceGroup(select.value);
      showFirewallSourceGroupEditor(manager, select.value);
    });
  });

  document.querySelectorAll("[data-source-group-editor]").forEach((editor) => {
    if (!(editor instanceof HTMLElement)) {
      return;
    }
    const form = editor.querySelector('form[data-firewall-source-groups]');
    if (!(form instanceof HTMLFormElement)) {
      return;
    }
    form.addEventListener("input", (event) => {
      const target = event.target;
      if (!(target instanceof HTMLInputElement) || target.name !== "group_name") {
        return;
      }
      const manager = editor.closest("[data-source-group-manager]");
      const select = manager?.querySelector("[data-source-group-select]");
      const groupId = editor.dataset.sourceGroupId || "";
      const option = select instanceof HTMLSelectElement ? Array.from(select.options).find((item) => item.value === groupId) : null;
      if (option) {
        option.textContent = target.value.trim() || groupId;
      }
    });
    form.addEventListener("click", (event) => {
      const target = event.target;
      if (!(target instanceof HTMLElement)) {
        return;
      }
      if (target.matches("[data-source-group-remove-entry]")) {
        event.preventDefault();
        target.closest(".source-group-entry-row")?.remove();
        ensureSourceGroupHasEntry(form);
        triggerSourceGroupAutosave(form);
        return;
      }
      if (target.matches("[data-source-group-add-any]")) {
        event.preventDefault();
        addSourceGroupEntry(form, "any");
        return;
      }
      if (target.matches("[data-source-group-add-cidr]")) {
        event.preventDefault();
        const input = form.querySelector("[data-source-group-cidr-input]");
        if (input instanceof HTMLInputElement) {
          addSourceGroupEntry(form, input.value);
          input.value = "";
          input.focus();
        }
        return;
      }
      if (target.matches("[data-source-group-add-ref]")) {
        event.preventDefault();
        const select = form.querySelector("[data-source-group-ref-select]");
        if (select instanceof HTMLSelectElement && select.value) {
          addSourceGroupEntry(form, select.value);
          select.value = "";
        }
      }
    });
  });

  document.querySelectorAll("[data-source-group-rename]").forEach((button) => {
    button.addEventListener("click", () => {
      if (!(renameModal instanceof HTMLDialogElement) || !(renameGroupId instanceof HTMLInputElement) || !(renameGroupName instanceof HTMLInputElement)) {
        return;
      }
      renameGroupId.value = button instanceof HTMLElement ? button.dataset.groupId || "" : "";
      renameGroupName.value = button instanceof HTMLElement ? button.dataset.groupName || "" : "";
      renameModal.showModal();
      renameGroupName.focus();
      renameGroupName.select();
    });
  });
}

function initializeFirewallSettings() {
  document.querySelectorAll('form[action="/firewall/settings"]').forEach((form) => {
    if (!(form instanceof HTMLFormElement)) {
      return;
    }
    form.addEventListener("labfoundry:autosave-success", (event) => {
      updateFirewallDesiredState(event.detail || {});
    });
  });
  document.querySelectorAll("[data-firewall-source-groups]").forEach((form) => {
    if (!(form instanceof HTMLFormElement)) {
      return;
    }
    form.addEventListener("labfoundry:autosave-success", (event) => {
      updateFirewallDesiredState(event.detail || {});
    });
  });
}

function updateValidationList(list, items = []) {
  if (!(list instanceof HTMLElement)) {
    return;
  }
  list.innerHTML = "";
  const useRoleListItems = list.getAttribute("role") === "list";
  items.forEach((message) => {
    const item = document.createElement(useRoleListItems ? "div" : "li");
    if (useRoleListItems) {
      item.setAttribute("role", "listitem");
    }
    item.textContent = message;
    list.append(item);
  });
  list.classList.toggle("hidden", items.length === 0);
}

function updateApplianceSettingsDhcpDns(payload = {}) {
  const servers = Array.isArray(payload.observed_dhcp_dns_servers) ? payload.observed_dhcp_dns_servers.filter(Boolean) : [];
  const usingDhcp = payload.resolver_mode === "dhcp";
  const textarea = document.querySelector("[data-appliance-settings-external-dns]");
  if (textarea instanceof HTMLTextAreaElement) {
    textarea.placeholder = usingDhcp && servers.length ? `DHCP: ${servers.join(", ")}` : "";
  }
  const sourceList = document.querySelector("[data-appliance-settings-dhcp-dns]");
  if (!(sourceList instanceof HTMLElement)) {
    return;
  }
  sourceList.classList.toggle("hidden", !usingDhcp);
  const values = sourceList.querySelector("[data-appliance-settings-dhcp-dns-values]");
  if (!(values instanceof HTMLElement)) {
    return;
  }
  values.innerHTML = "";
  if (!servers.length) {
    const empty = document.createElement("span");
    empty.className = "muted";
    empty.textContent = "No lease resolver servers reported yet.";
    values.append(empty);
    return;
  }
  servers.forEach((server) => {
    const code = document.createElement("code");
    code.textContent = server;
    values.append(code);
  });
}

function updateApplianceSettingsValidation(payload = {}) {
  const errors = Array.isArray(payload.validation_errors) ? payload.validation_errors : [];
  const warnings = Array.isArray(payload.validation_warnings) ? payload.validation_warnings : [];
  const valid = payload.valid !== undefined ? Boolean(payload.valid) : errors.length === 0;
  const pill = document.querySelector("[data-appliance-settings-valid-pill]");
  if (pill instanceof HTMLElement) {
    pill.textContent = valid ? "valid" : "needs attention";
    pill.classList.toggle("good", valid);
    pill.classList.toggle("warn", !valid);
  }
  updateValidationList(document.querySelector("[data-appliance-settings-errors]"), errors);
  updateValidationList(document.querySelector("[data-appliance-settings-warnings]"), warnings);

  const configPath = document.querySelector("[data-appliance-settings-config-path]");
  if (configPath instanceof HTMLElement && typeof payload.config_path === "string") {
    configPath.textContent = payload.config_path;
  }
  const preview = document.querySelector("[data-appliance-settings-preview]");
  if (preview instanceof HTMLElement && typeof payload.config_preview === "string") {
    preview.textContent = payload.config_preview;
    highlightConfigPreviewElement(preview);
  }
  const management = document.querySelector("[data-appliance-settings-management]");
  if (management instanceof HTMLElement && payload.management_interface) {
    const name = payload.management_interface.name || "not found";
    const ip = payload.management_interface.ip ? ` / ${payload.management_interface.ip}` : "";
    management.textContent = `${name}${ip}`;
  }
  const rootSsh = document.querySelector("[data-appliance-settings-root-ssh]");
  if (rootSsh instanceof HTMLElement && payload.root_ssh_enabled !== undefined) {
    rootSsh.textContent = payload.root_ssh_enabled ? "enabled" : "disabled";
  }
  updateApplianceSettingsDhcpDns(payload);
  const dnsStatus = document.querySelector("[data-appliance-settings-dns-status]");
  if (dnsStatus instanceof HTMLElement) {
    const localDnsEnabled = Boolean(payload.local_dns_enabled);
    dnsStatus.classList.toggle("success", localDnsEnabled);
    dnsStatus.classList.toggle("warning", !localDnsEnabled);
    const fqdn = typeof payload.fqdn === "string" ? payload.fqdn : "the appliance FQDN";
    const actionMessages = {
      created: "Created the app-owned appliance DNS record.",
      updated: "Updated the app-owned appliance DNS record.",
      unchanged: "The app-owned appliance DNS record already matched the management IP.",
      "updated+removed-old": "Updated the appliance DNS record and removed the old app-owned record.",
      "created+removed-old": "Created the appliance DNS record and removed the old app-owned record.",
      conflict: "A user-owned DNS record already uses this appliance FQDN.",
    };
    if (payload.dns_record_action && actionMessages[payload.dns_record_action]) {
      dnsStatus.textContent = actionMessages[payload.dns_record_action];
    } else if (localDnsEnabled) {
      dnsStatus.textContent = `Local DNS is enabled. Autosave manages the app-owned appliance DNS record for ${fqdn}.`;
    } else if (payload.resolver_mode === "dhcp") {
      dnsStatus.textContent = "Local DNS is disabled. Management DHCP will keep lease-provided resolver servers unless external DNS servers are entered.";
    } else {
      dnsStatus.textContent = "Local DNS is disabled. External DNS servers are required for appliance resolver apply.";
    }
  }
}

function initializeApplianceSettings() {
  document.querySelectorAll("[data-appliance-settings]").forEach((form) => {
    if (!(form instanceof HTMLFormElement)) {
      return;
    }
    form.addEventListener("labfoundry:autosave-success", (event) => {
      updateApplianceSettingsValidation(event.detail || {});
    });
  });
}

function updateDnsValidation(payload = {}) {
  const validationPanel = document.querySelector("[data-dns-validation-panel]");
  if (!(validationPanel instanceof HTMLElement)) {
    return;
  }
  const errors = Array.isArray(payload.validation_errors) ? payload.validation_errors : [];
  const warnings = Array.isArray(payload.validation_warnings) ? payload.validation_warnings : [];
  const valid = payload.valid !== undefined ? Boolean(payload.valid) : errors.length === 0;
  const status = validationPanel.querySelector("[data-dns-validation-status]");
  if (status instanceof HTMLElement) {
    status.textContent = valid ? "valid" : "needs attention";
    status.classList.toggle("good", valid);
    status.classList.toggle("warn", !valid);
  }
  const errorList = validationPanel.querySelector("[data-dns-validation-errors]");
  if (errorList instanceof HTMLElement) {
    errorList.innerHTML = "";
    errors.forEach((error) => {
      const item = document.createElement("div");
      item.textContent = error;
      errorList.append(item);
    });
    errorList.classList.toggle("hidden", errors.length === 0);
  }
  const warningList = validationPanel.querySelector("[data-dns-validation-warnings]");
  if (warningList instanceof HTMLElement) {
    warningList.innerHTML = "";
    warnings.forEach((warning) => {
      const item = document.createElement("div");
      item.textContent = warning;
      warningList.append(item);
    });
    warningList.classList.toggle("hidden", warnings.length === 0);
  }
  const message = validationPanel.querySelector("[data-dns-validation-message]");
  if (message instanceof HTMLElement) {
    if (!valid) {
      message.textContent = "";
    } else if (warnings.length) {
      message.textContent = "The desired DNS/DHCP state is valid, but review the warning before using this domain with VCF.";
    } else {
      message.innerHTML =
        "The desired DNS/DHCP state passes LabFoundry validation. Host validation still runs through <code>dnsmasq --test</code> on the appliance.";
    }
  }
  const configPath = validationPanel.querySelector("[data-dns-config-path]");
  if (configPath instanceof HTMLElement && typeof payload.config_path === "string") {
    configPath.textContent = payload.config_path;
  }
  const configPreview = validationPanel.querySelector("[data-dns-config-preview]");
  if (configPreview instanceof HTMLElement && typeof payload.config_preview === "string") {
    configPreview.textContent = payload.config_preview;
    highlightConfigPreviewElement(configPreview);
  }
  const dhcpUpstreams = Array.isArray(payload.observed_dhcp_upstream_servers) ? payload.observed_dhcp_upstream_servers : null;
  if (dhcpUpstreams) {
    const upstreamInput = document.querySelector('form[action="/dns/settings"] textarea[name="upstream_servers"]');
    if (upstreamInput instanceof HTMLTextAreaElement) {
      upstreamInput.placeholder = dhcpUpstreams.length ? `DHCP: ${dhcpUpstreams.join(", ")}` : "";
    }
    const upstreamList = document.querySelector("[data-dns-dhcp-upstreams]");
    if (upstreamList instanceof HTMLElement) {
      const values = upstreamList.querySelector("span:last-child");
      if (values instanceof HTMLElement) {
        values.innerHTML = "";
        dhcpUpstreams.forEach((server) => {
          const code = document.createElement("code");
          code.textContent = server;
          values.append(code);
          values.append(" ");
        });
      }
      upstreamList.classList.toggle("hidden", dhcpUpstreams.length === 0);
    }
  }
}

function initializeDnsSettings() {
  document.querySelectorAll('form[action="/dns/settings"]').forEach((form) => {
    if (!(form instanceof HTMLFormElement)) {
      return;
    }
    form.addEventListener("labfoundry:autosave-success", (event) => {
      updateDnsValidation(event.detail || {});
    });
  });
}

function vcfFqdnRowsElement() {
  const rows = document.querySelector("[data-vcf-fqdn-rows]");
  return rows instanceof HTMLElement ? rows : null;
}

function vcfFqdnComponents() {
  const rows = vcfFqdnRowsElement();
  if (!rows) {
    return [];
  }
  const target = document.querySelector("[data-vcf-fqdn-target]");
  try {
    const targetComponents = JSON.parse(rows.dataset.targetComponents || "{}");
    const selectedTarget = target instanceof HTMLSelectElement ? target.value : "";
    if (selectedTarget && targetComponents && typeof targetComponents === "object" && Array.isArray(targetComponents[selectedTarget])) {
      return targetComponents[selectedTarget];
    }
    const parsed = JSON.parse(rows.dataset.components || "[]");
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

function vcfFqdnTargetLabel() {
  const target = document.querySelector("[data-vcf-fqdn-target]");
  if (!(target instanceof HTMLSelectElement)) {
    return "VCF";
  }
  return target.selectedOptions[0]?.textContent?.trim() || target.value || "VCF";
}

function vcfFqdnExistingData() {
  const rows = vcfFqdnRowsElement();
  if (!rows) {
    return { fqdns: [], addressRecords: {} };
  }
  try {
    const fqdns = JSON.parse(rows.dataset.existingFqdns || "[]");
    const addressRecords = JSON.parse(rows.dataset.existingAddressRecords || rows.dataset.existingARecords || "{}");
    return {
      fqdns: Array.isArray(fqdns) ? fqdns : [],
      addressRecords: addressRecords && typeof addressRecords === "object" && !Array.isArray(addressRecords) ? addressRecords : {},
    };
  } catch {
    return { fqdns: [], addressRecords: {} };
  }
}

function vcfFqdnHostLabel(component, prefix, suffix) {
  return `${String(prefix || "").trim().toLowerCase()}${component.host || ""}${String(suffix || "").trim().toLowerCase()}`;
}

function vcfFqdnForComponent(component, domain, prefix, suffix) {
  const hostLabel = vcfFqdnHostLabel(component, prefix, suffix);
  const zone = String(domain || "").trim().replace(/\.$/, "").toLowerCase();
  if (!hostLabel || !zone) {
    return "";
  }
  return `${hostLabel}.${zone}`;
}

function vcfFqdnAddressFor(fqdn, payload = {}) {
  const created = Array.isArray(payload.created) ? payload.created : [];
  const skipped = Array.isArray(payload.skipped) ? payload.skipped : [];
  const existing = vcfFqdnExistingData();
  const createdRow = created.find((row) => row.fqdn === fqdn);
  if (createdRow?.address) {
    return createdRow.address;
  }
  const skippedRow = skipped.find((row) => row.fqdn === fqdn);
  if (skippedRow?.address) {
    return skippedRow.address;
  }
  const existingAddresses = existing.addressRecords[fqdn];
  if (Array.isArray(existingAddresses) && existingAddresses.length) {
    return existingAddresses.join(", ");
  }
  return "";
}

function vcfFqdnCurrentFqdns() {
  const domain = document.querySelector("[data-vcf-fqdn-domain]");
  const target = document.querySelector("[data-vcf-fqdn-target]");
  const prefix = document.querySelector("[data-vcf-fqdn-prefix]");
  const suffix = document.querySelector("[data-vcf-fqdn-suffix]");
  if (!(domain instanceof HTMLSelectElement) || !(target instanceof HTMLSelectElement)) {
    return [];
  }
  const prefixValue = prefix instanceof HTMLInputElement ? prefix.value : "";
  const suffixValue = suffix instanceof HTMLInputElement ? suffix.value : "";
  return vcfFqdnComponents()
    .map((component) => vcfFqdnForComponent(component, domain.value, prefixValue, suffixValue))
    .filter(Boolean);
}

function vcfFqdnHasAnyAddress(payload = {}) {
  return vcfFqdnCurrentFqdns().some((fqdn) => Boolean(vcfFqdnAddressFor(fqdn, payload)));
}

function vcfFqdnRowsHaveAddresses(payload = {}) {
  const fqdns = vcfFqdnCurrentFqdns();
  return fqdns.length > 0 && fqdns.every((fqdn) => Boolean(vcfFqdnAddressFor(fqdn, payload)));
}

function updateVcfFqdnActions(payload = {}) {
  const submit = document.querySelector("[data-vcf-fqdn-submit]");
  const cancelButton = document.querySelector("[data-vcf-fqdn-modal-cancel]");
  const deleteButton = document.querySelector("[data-vcf-fqdn-delete]");
  const complete = vcfFqdnRowsHaveAddresses(payload);
  if (submit instanceof HTMLButtonElement) {
    submit.textContent = complete ? "Done" : "Create DNS records";
    submit.dataset.complete = complete ? "1" : "0";
  }
  if (cancelButton instanceof HTMLButtonElement) {
    cancelButton.hidden = complete;
  }
  if (deleteButton instanceof HTMLButtonElement) {
    deleteButton.disabled = !vcfFqdnHasAnyAddress(payload);
  }
}

function vcfFqdnApplyDeletionPayload(payload = {}) {
  const rows = vcfFqdnRowsElement();
  if (!rows) {
    return;
  }
  const deleted = Array.isArray(payload.deleted) ? payload.deleted : [];
  if (!deleted.length) {
    return;
  }
  const deletedFqdns = new Set(deleted.map((row) => row.fqdn).filter(Boolean));
  const existing = vcfFqdnExistingData();
  rows.dataset.existingFqdns = JSON.stringify(existing.fqdns.filter((fqdn) => !deletedFqdns.has(fqdn)));
  const nextAddressRecords = { ...existing.addressRecords };
  deletedFqdns.forEach((fqdn) => {
    delete nextAddressRecords[fqdn];
  });
  rows.dataset.existingAddressRecords = JSON.stringify(nextAddressRecords);
}

function vcfFqdnRowStatus(fqdn, payload = {}) {
  const address = vcfFqdnAddressFor(fqdn, payload);
  if (address) {
    return address;
  }
  const skipped = Array.isArray(payload.skipped) ? payload.skipped : [];
  const existing = vcfFqdnExistingData();
  const skippedRow = skipped.find((row) => row.fqdn === fqdn);
  if (skippedRow || existing.fqdns.includes(fqdn)) {
    return "existing record skipped";
  }
  return "allocated on confirm";
}

function renderVcfFqdnRows(payload = {}) {
  const rows = vcfFqdnRowsElement();
  const domain = document.querySelector("[data-vcf-fqdn-domain]");
  const target = document.querySelector("[data-vcf-fqdn-target]");
  const prefix = document.querySelector("[data-vcf-fqdn-prefix]");
  const suffix = document.querySelector("[data-vcf-fqdn-suffix]");
  if (!rows || !(domain instanceof HTMLSelectElement) || !(target instanceof HTMLSelectElement)) {
    return;
  }
  const prefixValue = prefix instanceof HTMLInputElement ? prefix.value : "";
  const suffixValue = suffix instanceof HTMLInputElement ? suffix.value : "";
  rows.innerHTML = "";
  vcfFqdnComponents().forEach((component) => {
    const fqdn = vcfFqdnForComponent(component, domain.value, prefixValue, suffixValue);
    const row = document.createElement("tr");
    const componentCell = document.createElement("td");
    const fqdnCell = document.createElement("td");
    const statusCell = document.createElement("td");
    componentCell.textContent = component.description || component.host || "";
    fqdnCell.textContent = fqdn;
    statusCell.textContent = vcfFqdnRowStatus(fqdn, payload);
    if (statusCell.textContent === "existing record skipped" || statusCell.textContent === "allocated on confirm") {
      statusCell.className = "muted";
    }
    row.append(componentCell, fqdnCell, statusCell);
    rows.append(row);
  });
  updateVcfFqdnActions(payload);
}

function setVcfFqdnMessage(selector, messages, type = "error") {
  const element = document.querySelector(selector);
  if (!(element instanceof HTMLElement)) {
    return;
  }
  const values = Array.isArray(messages) ? messages : [messages];
  element.innerHTML = "";
  values.filter(Boolean).forEach((message) => {
    const item = document.createElement("div");
    item.textContent = message;
    element.append(item);
  });
  element.classList.toggle("hidden", values.filter(Boolean).length === 0);
  element.classList.toggle("error", type === "error");
  element.classList.toggle("success", type === "success");
}

function initializeVcfFqdnGenerator() {
  const modal = document.getElementById("vcf-fqdn-modal");
  const form = document.querySelector("[data-vcf-fqdn-form]");
  if (!(modal instanceof HTMLDialogElement) || !(form instanceof HTMLFormElement)) {
    return;
  }
  const submit = form.querySelector("[data-vcf-fqdn-submit]");
  const deleteButton = form.querySelector("[data-vcf-fqdn-delete]");
  const clearButton = form.querySelector("[data-vcf-fqdn-clear]");
  const controls = form.querySelectorAll("[data-vcf-fqdn-target], [data-vcf-fqdn-prefix], [data-vcf-fqdn-suffix], [data-vcf-fqdn-domain]");
  let currentPayload = {};
  document.querySelectorAll("[data-vcf-fqdn-modal-open]").forEach((button) => {
    if (!(button instanceof HTMLButtonElement)) {
      return;
    }
    button.addEventListener("click", () => {
      currentPayload = {};
      setVcfFqdnMessage("[data-vcf-fqdn-errors]", []);
      setVcfFqdnMessage("[data-vcf-fqdn-result]", [], "success");
      renderVcfFqdnRows(currentPayload);
      modal.showModal();
    });
  });
  document.querySelectorAll("[data-vcf-fqdn-modal-cancel]").forEach((button) => {
    if (button instanceof HTMLButtonElement) {
      button.addEventListener("click", () => modal.close("cancel"));
    }
  });
  controls.forEach((control) => {
    control.addEventListener("input", () => {
      currentPayload = {};
      renderVcfFqdnRows(currentPayload);
    });
    control.addEventListener("change", () => {
      currentPayload = {};
      renderVcfFqdnRows(currentPayload);
    });
  });
  if (clearButton instanceof HTMLButtonElement) {
    clearButton.addEventListener("click", () => {
      const prefix = form.querySelector("[data-vcf-fqdn-prefix]");
      const suffix = form.querySelector("[data-vcf-fqdn-suffix]");
      if (prefix instanceof HTMLInputElement) {
        prefix.value = "";
      }
      if (suffix instanceof HTMLInputElement) {
        suffix.value = "";
      }
      currentPayload = {};
      renderVcfFqdnRows(currentPayload);
    });
  }
  const setActionsDisabled = (disabled) => {
    if (submit instanceof HTMLButtonElement) {
      submit.disabled = disabled;
    }
    if (deleteButton instanceof HTMLButtonElement) {
      deleteButton.disabled = disabled;
    }
  };
  const submitRequest = async (url, action) => {
    setVcfFqdnMessage("[data-vcf-fqdn-errors]", []);
    setVcfFqdnMessage("[data-vcf-fqdn-result]", [], "success");
    setActionsDisabled(true);
    try {
      const response = await fetch(url, {
        method: "POST",
        body: new FormData(form),
        credentials: "same-origin",
        headers: {
          "X-LabFoundry-VCF-Helper": "1",
        },
      });
      const payload = await response.json();
      if (!response.ok) {
        setVcfFqdnMessage("[data-vcf-fqdn-errors]", payload.errors || `Generated VCF FQDNs could not be ${action}.`);
        return null;
      }
      return payload;
    } catch (error) {
      setVcfFqdnMessage("[data-vcf-fqdn-errors]", error instanceof Error ? error.message : `Generated VCF FQDNs could not be ${action}.`);
      return null;
    } finally {
      setActionsDisabled(false);
      updateVcfFqdnActions(currentPayload);
    }
  };
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (submit instanceof HTMLButtonElement && submit.dataset.complete === "1") {
      modal.close("done");
      window.location.reload();
      return;
    }
    const domain = form.querySelector("[data-vcf-fqdn-domain]");
    const zone = domain instanceof HTMLSelectElement ? domain.value : "the selected domain";
    const confirmed = await requestConfirmation({
      title: "Create generated VCF DNS records?",
      message: `Create missing DNS A or AAAA records for ${vcfFqdnComponents().length} ${vcfFqdnTargetLabel()} components in ${zone}? Existing FQDNs remain unchanged, and allocation stays inside the selected IP subnet.`,
      label: "Create DNS records",
    });
    if (confirmed) {
      const payload = await submitRequest(form.action, "created");
      if (payload) {
        currentPayload = payload;
        renderVcfFqdnRows(currentPayload);
        const createdCount = Array.isArray(payload.created) ? payload.created.length : 0;
        const skippedCount = Array.isArray(payload.skipped) ? payload.skipped.length : 0;
        setVcfFqdnMessage(
          "[data-vcf-fqdn-result]",
          `Created ${createdCount} DNS records${skippedCount ? `; skipped ${skippedCount} existing records` : ""}.`,
          "success"
        );
      }
    }
  });
  if (deleteButton instanceof HTMLButtonElement) {
    deleteButton.addEventListener("click", async () => {
      const domain = form.querySelector("[data-vcf-fqdn-domain]");
      const zone = domain instanceof HTMLSelectElement ? domain.value : "the selected domain";
      const confirmed = await requestConfirmation({
        title: "Delete generated VCF DNS records?",
        message: `Delete VCF Helper A or AAAA records matching the current deployment, prefix, suffix, and ${zone} domain? Unrelated and skipped existing records are preserved. The appliance changes only after global Appliance Apply.`,
        label: "Delete DNS records",
      });
      if (confirmed) {
        const payload = await submitRequest(`${form.action}/delete`, "deleted");
        if (payload) {
          vcfFqdnApplyDeletionPayload(payload);
          currentPayload = { skipped: Array.isArray(payload.preserved) ? payload.preserved : [] };
          renderVcfFqdnRows(currentPayload);
          const deletedCount = Array.isArray(payload.deleted) ? payload.deleted.length : 0;
          const preservedCount = Array.isArray(payload.preserved) ? payload.preserved.length : 0;
          setVcfFqdnMessage(
            "[data-vcf-fqdn-result]",
            `Deleted ${deletedCount} VCF Helper DNS records${preservedCount ? `; preserved ${preservedCount} unrelated records` : ""}.`,
            "success"
          );
        }
      }
    });
  }
  renderVcfFqdnRows(currentPayload);
}

function updateVcfBackupDerivedAddress(form, payload = {}) {
  const portInput = form.querySelector('input[name="port"]');
  const { interfaceLabel: bindInterfaceLabel, address, addresses, addressLabel } = serviceBindSelection(form, payload);
  const port = payload.port || portInput?.value || "22";
  const endpoint = document.querySelector("[data-vcf-endpoint]");
  const host = document.querySelector("[data-vcf-host]");
  const targetPort = document.querySelector("[data-vcf-port]");
  const interfaceElement = document.querySelector("[data-vcf-interface]");
  const sftpUser = document.querySelector("[data-vcf-sftp-user]");
  const targetUser = document.querySelector("[data-vcf-target-user]");
  const storagePaths = document.querySelectorAll("[data-vcf-storage-path]");
  const remoteDirectories = document.querySelectorAll("[data-vcf-remote-directory]");
  const chrootLabel = document.querySelector("[data-vcf-chroot-label]");
  const authMethods = document.querySelector("[data-vcf-auth-methods]");
  const maxSessions = document.querySelector("[data-vcf-max-sessions]");
  if (endpoint instanceof HTMLElement) {
    endpoint.textContent = address ? `${address}:${port}` : `no interface IP:${port}`;
  }
  if (host instanceof HTMLElement) {
    host.textContent = address || "no interface IP";
  }
  if (targetPort instanceof HTMLElement) {
    targetPort.textContent = String(port);
  }
  if (interfaceElement instanceof HTMLElement) {
    interfaceElement.textContent = `${bindInterfaceLabel || "no interface"} / ${addressLabel || "no interface IP"}`;
  }
  if (sftpUser instanceof HTMLElement && payload.sftp_username !== undefined) {
    sftpUser.textContent = payload.sftp_username || "not selected";
  }
  if (targetUser instanceof HTMLElement && payload.sftp_username !== undefined) {
    targetUser.textContent = payload.sftp_username || "select a user";
  }
  if (payload.storage_path) {
    storagePaths.forEach((storagePath) => {
      if (storagePath instanceof HTMLElement) {
        storagePath.textContent = payload.storage_path;
      }
    });
  }
  if (payload.remote_directory) {
    remoteDirectories.forEach((remoteDirectory) => {
      if (remoteDirectory instanceof HTMLElement) {
        remoteDirectory.textContent = payload.remote_directory;
      }
    });
  }
  if (chrootLabel instanceof HTMLElement && payload.chroot_label) {
    chrootLabel.textContent = payload.chroot_label;
  }
  if (authMethods instanceof HTMLElement && payload.auth_methods) {
    authMethods.textContent = payload.auth_methods;
  }
  if (maxSessions instanceof HTMLElement && payload.max_sessions !== undefined) {
    maxSessions.textContent = `${payload.max_sessions} max sessions`;
  }
}

function updateVcfBackupValidation(payload = {}) {
  const status = document.querySelector("[data-vcf-validation-status]");
  const validationPanel = status?.closest(".panel");
  const applyButton = document.querySelector("[data-vcf-apply-button]");
  const configPath = document.querySelector("[data-vcf-config-path]");
  const configPreview = document.querySelector("[data-vcf-config-preview]");
  const errors = Array.isArray(payload.validation_errors) ? payload.validation_errors : [];
  if (status instanceof HTMLElement && payload.valid !== undefined) {
    status.textContent = payload.valid ? "valid" : "needs attention";
    status.classList.toggle("good", Boolean(payload.valid));
    status.classList.toggle("warn", !payload.valid);
  }
  if (applyButton instanceof HTMLButtonElement && payload.valid !== undefined) {
    applyButton.disabled = !payload.valid;
  }
  if (configPath instanceof HTMLElement && payload.config_path) {
    configPath.textContent = payload.config_path;
  }
  if (configPreview instanceof HTMLElement && payload.config_preview !== undefined) {
    configPreview.textContent = payload.config_preview;
    highlightConfigPreviewElement(configPreview);
  }
  if (validationPanel instanceof HTMLElement && payload.valid !== undefined) {
    const previewAnchor = validationPanel.querySelector("[data-config-preview-row]");
    let errorList = validationPanel.querySelector("[data-vcf-validation-errors]");
    let message = validationPanel.querySelector("[data-vcf-validation-message]");
    if (payload.valid) {
      if (errorList) {
        errorList.remove();
      }
      if (!(message instanceof HTMLElement)) {
        message = document.createElement("p");
        message.className = "muted";
        message.setAttribute("data-vcf-validation-message", "");
        validationPanel.insertBefore(message, previewAnchor);
      }
      message.textContent = "The desired VCF backup SFTP state passes LabFoundry validation. Appliance validation still runs through the allowlisted OpenSSH helper before apply.";
    } else {
      if (message) {
        message.remove();
      }
      if (!(errorList instanceof HTMLElement)) {
        errorList = document.createElement("ul");
        errorList.className = "error-list";
        errorList.setAttribute("data-vcf-validation-errors", "");
        validationPanel.insertBefore(errorList, previewAnchor);
      }
      errorList.innerHTML = "";
      errors.forEach((error) => {
        const item = document.createElement("li");
        item.textContent = error;
        errorList.appendChild(item);
      });
    }
  }
}

function initializeVcfBackupSettings() {
  document.querySelectorAll("[data-vcf-backup-settings]").forEach((form) => {
    if (!(form instanceof HTMLFormElement)) {
      return;
    }
    const portInput = form.querySelector('input[name="port"]');
    const refresh = () => updateVcfBackupDerivedAddress(form);
    if (portInput instanceof HTMLInputElement) {
      portInput.addEventListener("input", refresh);
    }
    form.addEventListener("labfoundry:autosave-success", (event) => {
      const payload = event.detail || {};
      updateVcfBackupDerivedAddress(form, payload);
      updateVcfBackupValidation(payload);
    });
    refresh();
  });
}

function showVcfRegistryMessage(message, type = "error") {
  const element = document.getElementById("vcf-registry-bundle-error");
  if (!element) {
    return;
  }
  element.textContent = message;
  element.classList.toggle("error", type === "error");
  element.classList.toggle("success", type === "success");
  element.classList.remove("hidden");
}

function newVcfRegistryBundleRow() {
  return {
    id: "__new__",
    name: "",
    source_reference: "",
    target_reference: "",
    enabled: true,
    status: "planned",
    notes: "",
    is_new: true,
  };
}

function hasRequiredVcfRegistryBundleFields(data) {
  return Boolean((data.name || "").trim() && (data.source_reference || "").trim());
}

async function postVcfRegistryBundleAction(url, data, csrf) {
  const body = new FormData();
  body.set("csrf", csrf);
  for (const [key, value] of Object.entries(data)) {
    if (["id", "is_new", "created_at", "updated_at"].includes(key)) {
      continue;
    }
    if (key === "enabled") {
      if (value) {
        body.set(key, "on");
      }
      continue;
    }
    body.set(key, value ?? "");
  }
  const response = await fetch(url, {
    method: "POST",
    body,
    credentials: "same-origin",
  });
  if (!response.ok) {
    const text = await response.text();
    const plainText = text.trim().replace(/<[^>]+>/g, " ").replace(/\s+/g, " ");
    throw new Error(plainText || "The Supervisor Service bundle could not be saved.");
  }
  window.location.reload();
}

async function autoSaveVcfRegistryBundle(cell, csrf) {
  const row = cell.getRow();
  const data = row.getData();
  if (data.is_new && !hasRequiredVcfRegistryBundleFields(data)) {
    reformatPendingNewRecord(cell);
    return;
  }
  const url = data.is_new ? "/vcf-private-registry/bundles" : `/vcf-private-registry/bundles/${data.id}/edit`;
  try {
    await postVcfRegistryBundleAction(url, data, csrf);
  } catch (error) {
    showVcfRegistryMessage(error instanceof Error ? error.message : "The Supervisor Service bundle could not be saved.");
  }
}

async function deleteVcfRegistryBundleFromMenu(row, csrf) {
  const data = row.getData();
  if (data.is_new) {
    row.getTable().deleteRow(data.id);
    return;
  }
  const confirmed = await requestConfirmation({
    title: `Delete ${data.name || "Supervisor Service"} bundle?`,
    message: "This removes the Supervisor Service bundle from LabFoundry desired state. It does not remove images from Harbor until a future appliance task explicitly does so.",
    label: "Delete",
  });
  if (!confirmed) {
    return;
  }
  try {
    await postVcfRegistryBundleAction(`/vcf-private-registry/bundles/${data.id}/delete`, data, csrf);
  } catch (error) {
    showVcfRegistryMessage(error instanceof Error ? error.message : "The Supervisor Service bundle could not be deleted.");
  }
}

function initializeVcfRegistryBundlesTable() {
  const tableElement = document.getElementById("vcf-registry-bundles-table");
  if (!(tableElement instanceof HTMLElement)) {
    return;
  }
  const fallback = document.getElementById(tableElement.dataset.fallbackId || "");
  if (typeof Tabulator === "undefined") {
    showVcfRegistryMessage("Tabulator did not load. Showing the fallback table.");
    return;
  }
  const csrf = tableElement.dataset.csrf || "";
  const rows = [...JSON.parse(tableElement.dataset.bundles || "[]"), newVcfRegistryBundleRow()];
  try {
    new Tabulator(tableElement, {
      data: rows,
      index: "id",
      layout: "fitColumns",
      height: "360px",
      rowHeight: 28,
      placeholder: "No Supervisor Service bundles configured.",
      reactiveData: false,
      rowContextMenu: [
        {
          label: "Delete bundle",
          action: (_event, row) => deleteVcfRegistryBundleFromMenu(row, csrf),
        },
      ],
      columns: lockNewRecordColumns([
        {
          title: "Name",
          field: "name",
          editor: "input",
          formatter: (cell) => dnsAddRowHintFormatter(cell, "+ Add bundle here"),
          minWidth: 170,
          cellEdited: (cell) => autoSaveVcfRegistryBundle(cell, csrf),
        },
        {
          title: "Source reference",
          field: "source_reference",
          editor: "input",
          formatter: (cell) => dnsAddRowHintFormatter(cell, "source bundle or image..."),
          minWidth: 260,
          cellEdited: (cell) => autoSaveVcfRegistryBundle(cell, csrf),
        },
        {
          title: "Target reference",
          field: "target_reference",
          editor: "input",
          formatter: (cell) => dnsAddRowHintFormatter(cell, "derived if blank..."),
          minWidth: 260,
          cellEdited: (cell) => autoSaveVcfRegistryBundle(cell, csrf),
        },
        {
          title: "Status",
          field: "status",
          editor: "list",
          editorParams: { values: { planned: "planned", ready: "ready", relocated: "relocated", blocked: "blocked" } },
          width: 120,
          cellEdited: (cell) => autoSaveVcfRegistryBundle(cell, csrf),
        },
        {
          title: "Enabled",
          field: "enabled",
          formatter: labFoundryBooleanFormatter,
          editor: "tickCross",
          hozAlign: "center",
          width: 95,
          headerSort: false,
          cellEdited: (cell) => autoSaveVcfRegistryBundle(cell, csrf),
        },
        {
          title: "Notes",
          field: "notes",
          editor: "input",
          formatter: (cell) => dnsAddRowHintFormatter(cell, "optional note..."),
          minWidth: 180,
          cellEdited: (cell) => autoSaveVcfRegistryBundle(cell, csrf),
        },
      ], "name"),
      rowFormatter: (row) => {
        markNewRecordRow(row, "name");
      },
    });
    if (fallback) {
      fallback.classList.add("hidden");
    }
  } catch (error) {
    showVcfRegistryMessage(error instanceof Error ? error.message : "Tabulator could not render. Showing the fallback table.");
  }
}

function updateVcfRegistrySummary(form, payload = {}) {
  const portInput = form.querySelector('input[name="port"]');
  const hostnameInput = form.querySelector('input[name="hostname"]');
  const projectInput = form.querySelector('input[name="harbor_project"]');
  const { interfaceLabel: bindInterfaceLabel, addressLabel } = serviceBindSelection(form, payload);
  const port = payload.port || portInput?.value || "443";
  const hostname = payload.hostname || hostnameInput?.value || "";
  const endpointValue = payload.endpoint || (port === "443" || port === 443 ? hostname : `${hostname}:${port}`);
  const endpoint = document.querySelector("[data-vcf-registry-endpoint]");
  const interfaceLabel = document.querySelector("[data-vcf-registry-interface]");
  const project = document.querySelector("[data-vcf-registry-project]");
  const robot = document.querySelector("[data-vcf-registry-robot]");
  const storagePaths = document.querySelectorAll("[data-vcf-registry-storage]");
  const caBundleSource = document.querySelector("[data-vcf-registry-ca-bundle-source]");
  const caBundlePath = document.querySelector("[data-vcf-registry-ca-bundle-path]");
  if (endpoint instanceof HTMLElement) {
    endpoint.textContent = endpointValue || "registry hostname required";
  }
  if (interfaceLabel instanceof HTMLElement) {
    interfaceLabel.textContent = `${bindInterfaceLabel || "no interface"} / ${addressLabel || "no interface IP"}`;
  }
  if (project instanceof HTMLElement) {
    project.textContent = payload.harbor_project || projectInput?.value || "";
  }
  if (robot instanceof HTMLElement && payload.robot_account !== undefined) {
    robot.textContent = payload.robot_account || "";
  }
  if (payload.storage_path) {
    storagePaths.forEach((storagePath) => {
      if (storagePath instanceof HTMLElement) {
        storagePath.textContent = payload.storage_path;
      }
    });
  }
  if (caBundleSource instanceof HTMLElement && payload.ca_bundle_source_label !== undefined) {
    const uploadedName = payload.ca_bundle_uploaded_name || "not uploaded";
    const sourceText = `${payload.ca_bundle_source === "uploaded" ? uploadedName : payload.ca_bundle_source_label} / `;
    if (caBundleSource.firstChild) {
      caBundleSource.firstChild.textContent = sourceText;
    } else {
      caBundleSource.prepend(document.createTextNode(sourceText));
    }
  }
  if (caBundlePath instanceof HTMLElement && payload.ca_bundle_path) {
    caBundlePath.textContent = payload.ca_bundle_path;
  }
}

function updateVcfRegistryValidation(payload = {}) {
  const status = document.querySelector("[data-vcf-registry-validation-status]");
  const validationPanel = status?.closest(".panel");
  const applyButton = document.querySelector("[data-vcf-registry-apply-button]");
  const configPath = document.querySelector("[data-vcf-registry-config-path]");
  const harborPreview = document.querySelector("[data-vcf-registry-harbor-preview]");
  const relocationPreview = document.querySelector("[data-vcf-registry-relocation-preview]");
  const errors = Array.isArray(payload.validation_errors) ? payload.validation_errors : [];
  const warnings = Array.isArray(payload.validation_warnings) ? payload.validation_warnings : [];
  if (status instanceof HTMLElement && payload.valid !== undefined) {
    status.textContent = payload.valid ? "valid" : "needs attention";
    status.classList.toggle("good", Boolean(payload.valid));
    status.classList.toggle("warn", !payload.valid);
  }
  if (applyButton instanceof HTMLButtonElement && payload.valid !== undefined) {
    applyButton.disabled = !payload.valid;
  }
  if (configPath instanceof HTMLElement && payload.config_path) {
    configPath.textContent = payload.config_path;
  }
  if (harborPreview instanceof HTMLElement && payload.harbor_config_preview !== undefined) {
    harborPreview.textContent = payload.harbor_config_preview;
    highlightConfigPreviewElement(harborPreview);
  }
  if (relocationPreview instanceof HTMLElement && payload.relocation_preview !== undefined) {
    relocationPreview.textContent = payload.relocation_preview;
    highlightConfigPreviewElement(relocationPreview);
  }
  if (validationPanel instanceof HTMLElement && payload.valid !== undefined) {
    const previewAnchor = validationPanel.querySelector("[data-config-preview-row]");
    let errorList = validationPanel.querySelector("[data-vcf-registry-validation-errors]");
    let message = validationPanel.querySelector("[data-vcf-registry-validation-message]");
    let warningList = validationPanel.querySelector("[data-vcf-registry-validation-warnings]");
    if (payload.valid) {
      if (errorList) {
        errorList.remove();
      }
      if (!(message instanceof HTMLElement)) {
        message = document.createElement("p");
        message.className = "muted";
        message.setAttribute("data-vcf-registry-validation-message", "");
        validationPanel.insertBefore(message, previewAnchor);
      }
      message.textContent = "The desired VCF private registry state passes LabFoundry validation. Appliance validation still runs through the allowlisted Harbor helper before apply.";
    } else {
      if (message) {
        message.remove();
      }
      if (!(errorList instanceof HTMLElement)) {
        errorList = document.createElement("ul");
        errorList.className = "error-list";
        errorList.setAttribute("data-vcf-registry-validation-errors", "");
        validationPanel.insertBefore(errorList, previewAnchor);
      }
      errorList.innerHTML = "";
      errors.forEach((error) => {
        const item = document.createElement("li");
        item.textContent = error;
        errorList.appendChild(item);
      });
    }
    if (!(warningList instanceof HTMLElement)) {
      warningList = document.createElement("ul");
      warningList.className = "warning-list";
      warningList.setAttribute("data-vcf-registry-validation-warnings", "");
      validationPanel.insertBefore(warningList, previewAnchor);
    }
    warningList.innerHTML = "";
    warnings.forEach((warning) => {
      const item = document.createElement("li");
      item.textContent = warning;
      warningList.appendChild(item);
    });
    warningList.classList.toggle("hidden", warnings.length === 0);
  }
}

function initializeVcfRegistrySettings() {
  document.querySelectorAll("[data-vcf-registry-settings]").forEach((form) => {
    if (!(form instanceof HTMLFormElement)) {
      return;
    }
    const portInput = form.querySelector('input[name="port"]');
    const hostnameInput = form.querySelector('input[name="hostname"]');
    const projectInput = form.querySelector('input[name="harbor_project"]');
    const refresh = () => updateVcfRegistrySummary(form);
    [portInput, hostnameInput, projectInput].forEach((input) => {
      if (input instanceof HTMLElement) {
        input.addEventListener("input", refresh);
        input.addEventListener("change", refresh);
      }
    });
    form.addEventListener("labfoundry:autosave-success", (event) => {
      const payload = event.detail || {};
      updateVcfRegistrySummary(form, payload);
      updateVcfRegistryValidation(payload);
    });
    refresh();
  });
}

function showVcfDepotMessage(message, type = "error") {
  const element = document.getElementById("vcf-depot-profile-error");
  if (!element) {
    return;
  }
  element.textContent = message;
  element.classList.toggle("error", type === "error");
  element.classList.toggle("success", type === "success");
  element.classList.remove("hidden");
}

function newVcfDepotProfileRow() {
  return {
    id: "__new__",
    name: "",
    profile_type: "binaries",
    sku: "VCF",
    vcf_version: "9.1.0",
    binary_type: "INSTALL",
    automated_install: true,
    upgrades_only: false,
    patches_only: false,
    download_mode: "automated_install",
    component: "",
    component_version: "",
    disabled_platforms: [],
    enabled: false,
    status: "planned",
    notes: "",
    is_new: true,
  };
}

function hasRequiredVcfDepotProfileFields(data) {
  return Boolean((data.name || "").trim() && (data.profile_type || "").trim());
}

function vcfDepotListValues(value) {
  if (Array.isArray(value)) {
    return value.map((item) => String(item || "").trim()).filter(Boolean);
  }
  return String(value || "")
    .split(/\r?\n/)
    .map((item) => item.trim())
    .filter(Boolean);
}

function formatVcfDepotChoiceList(cell, values, emptyText) {
  const selected = vcfDepotListValues(cell.getValue());
  if (!selected.length) {
    return `<span class="muted">${escapeHtml(emptyText)}</span>`;
  }
  return selected.map((item) => escapeHtml(values[item] || item)).join("<br>");
}

function formatVcfDepotDisabledPlatforms(cell, values, emptyText) {
  const selected = vcfDepotListValues(cell.getValue());
  if (!selected.length) {
    return `<span class="muted">${escapeHtml(emptyText)}</span>`;
  }
  const summaryItems = selected.slice(0, 3).map((item) => escapeHtml(values[item] || item));
  const summary = [
    summaryItems.join("<br>"),
    selected.length > summaryItems.length ? `<span class="muted">+ ${selected.length - summaryItems.length} more</span>` : "",
  ].filter(Boolean).join("<br>");
  const rows = selected.map((item) => {
    const label = values[item] || item;
    return `<tr><td><code>${escapeHtml(item)}</code></td><td>${escapeHtml(label)}</td></tr>`;
  }).join("");
  const ariaLabel = selected.map((item) => values[item] || item).join(", ");
  return [
    `<span class="vcf-platform-tooltip" tabindex="0" aria-label="Disabled platforms: ${escapeHtml(ariaLabel)}">`,
    `<span class="vcf-platform-summary">${summary}</span>`,
    '<span class="vcf-platform-tip" role="tooltip">',
    '<strong>Disabled platforms</strong>',
    '<table><thead><tr><th>Value</th><th>Label</th></tr></thead>',
    `<tbody>${rows}</tbody></table>`,
    '</span>',
    '</span>',
  ].join("");
}

function rememberActiveTab(storageKey, targetId) {
  if (!storageKey || !targetId) {
    return;
  }
  try {
    window.localStorage.setItem(storageKey, targetId);
  } catch {
    // Tab persistence is a convenience only; private browsing can disable it.
  }
}

function vcfDepotDisabledPlatformsEditor(cell, onRendered, success, cancel, editorParams) {
  const values = editorParams.values || {};
  const selected = new Set(vcfDepotListValues(cell.getValue()));
  const wrapper = document.createElement("div");
  wrapper.className = "tabulator-checklist-editor";
  const options = document.createElement("div");
  options.className = "tabulator-checklist-options";
  Object.entries(values).forEach(([value, label]) => {
    const row = document.createElement("label");
    row.className = "tabulator-checklist-option";
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.value = value;
    checkbox.checked = selected.has(value);
    const text = document.createElement("span");
    text.textContent = label;
    row.append(checkbox, text);
    options.appendChild(row);
  });
  const actions = document.createElement("div");
  actions.className = "tabulator-checklist-actions";
  const clearButton = document.createElement("button");
  clearButton.type = "button";
  clearButton.textContent = "Clear";
  const doneButton = document.createElement("button");
  doneButton.type = "button";
  doneButton.textContent = "Done";
  doneButton.className = "primary";
  actions.append(clearButton, doneButton);
  wrapper.append(options, actions);

  function selectedValues() {
    return Array.from(wrapper.querySelectorAll("input[type='checkbox']:checked")).map((input) => input.value);
  }

  clearButton.addEventListener("click", (event) => {
    event.preventDefault();
    wrapper.querySelectorAll("input[type='checkbox']").forEach((input) => {
      input.checked = false;
    });
  });
  doneButton.addEventListener("click", (event) => {
    event.preventDefault();
    success(selectedValues());
  });
  wrapper.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && event.ctrlKey) {
      event.preventDefault();
      success(selectedValues());
    }
    if (event.key === "Escape") {
      event.preventDefault();
      cancel();
    }
  });
  onRendered(() => {
    const firstCheckbox = wrapper.querySelector("input[type='checkbox']");
    if (firstCheckbox instanceof HTMLElement) {
      firstCheckbox.focus();
    }
  });
  return wrapper;
}

async function postVcfDepotProfileAction(url, data, csrf) {
  const body = new FormData();
  body.set("csrf", csrf);
  const downloadMode = data.download_mode || "automated_install";
  body.set(downloadMode, "on");
  for (const [key, value] of Object.entries(data)) {
    if (["id", "is_new", "created_at", "updated_at"].includes(key)) {
      continue;
    }
    if (["automated_install", "upgrades_only", "patches_only", "download_mode"].includes(key)) {
      continue;
    }
    if (key === "enabled") {
      if (value) {
        body.set(key, "on");
      }
      continue;
    }
    if (key === "disabled_platforms") {
      body.set(key, vcfDepotListValues(value).join("\n"));
      continue;
    }
    body.set(key, value ?? "");
  }
  const response = await fetch(url, {
    method: "POST",
    body,
    credentials: "same-origin",
  });
  if (!response.ok) {
    const text = await response.text();
    const plainText = text.trim().replace(/<[^>]+>/g, " ").replace(/\s+/g, " ");
    throw new Error(plainText || "The VCFDT download profile could not be saved.");
  }
  window.location.reload();
}

async function autoSaveVcfDepotProfile(cell, csrf) {
  const row = cell.getRow();
  const data = row.getData();
  if (data.is_new && !hasRequiredVcfDepotProfileFields(data)) {
    reformatPendingNewRecord(cell);
    return;
  }
  const url = data.is_new ? "/vcf-offline-depot/profiles" : `/vcf-offline-depot/profiles/${data.id}/edit`;
  try {
    await postVcfDepotProfileAction(url, data, csrf);
  } catch (error) {
    showVcfDepotMessage(error instanceof Error ? error.message : "The VCFDT download profile could not be saved.");
  }
}

async function deleteVcfDepotProfileFromMenu(row, csrf) {
  const data = row.getData();
  if (data.is_new) {
    row.getTable().deleteRow(data.id);
    return;
  }
  const confirmed = await requestConfirmation({
    title: `Delete ${data.name || "VCFDT"} profile?`,
    message: "This removes the VCFDT download profile from LabFoundry desired state. It does not remove files from the appliance depot until a future task explicitly does so.",
    label: "Delete",
  });
  if (!confirmed) {
    return;
  }
  try {
    await postVcfDepotProfileAction(`/vcf-offline-depot/profiles/${data.id}/delete`, data, csrf);
  } catch (error) {
    showVcfDepotMessage(error instanceof Error ? error.message : "The VCFDT download profile could not be deleted.");
  }
}

async function startVcfDepotProfileDownload(row, csrf) {
  const data = row.getData();
  if (data.is_new) {
    return;
  }
  if (!data.enabled) {
    showVcfDepotMessage("Enable the VCFDT download profile before starting a download.");
    return;
  }
  if (data.download_active) {
    showVcfDepotMessage(data.active_task_blocker || "Wait for the active VCFDT task to finish before starting another download.");
    return;
  }
  if (!data.can_start) {
    showVcfDepotMessage(data.start_blocker || "Stage Broadcom credentials before starting this VCFDT download profile.");
    return;
  }
  try {
    const body = new FormData();
    body.set("csrf", csrf);
    const response = await fetch(`/vcf-offline-depot/profiles/${data.id}/download`, {
      method: "POST",
      body,
      credentials: "same-origin",
      headers: { "X-LabFoundry-Autosave": "1" },
    });
    const payload = await response.json();
    if (!response.ok) {
      throw new Error(payload.detail || "The VCFDT download job could not be started.");
    }
    const blocker = `Wait for VCFDT task ${payload.job_id} to finish before starting another download.`;
    await row.update({
      status: payload.profile_status || data.status,
      download_active: true,
      active_task_blocker: blocker,
    });
    setVcfDepotDownloadActive(true, payload.job_id);
    if (vcfDepotTasksTable) {
      await vcfDepotTasksTable.setPage(1);
      await refreshVcfDepotTasksTable();
    }
    showVcfDepotMessage(`VCFDT task ${payload.job_id} started for ${payload.profile_name}.`, "success");
  } catch (error) {
    showVcfDepotMessage(error instanceof Error ? error.message : "The VCFDT download job could not be started.");
  }
}

async function previewVcfDepotProfileScript(row) {
  const data = row.getData();
  if (data.is_new) {
    showVcfDepotMessage("Save the VCFDT download profile before previewing its script.");
    return;
  }
  try {
    const response = await fetch(`/vcf-offline-depot/profiles/${data.id}/preview`, {
      method: "GET",
      credentials: "same-origin",
      headers: { Accept: "application/json" },
    });
    const payload = await response.json();
    if (!response.ok) {
      throw new Error(payload.detail || "The VCFDT profile script could not be rendered.");
    }
    const sourceCode = document.createElement("code");
    sourceCode.className = "language-bash";
    openPreviewModal(`${payload.profile_name || data.name || "VCFDT"} profile script`, payload.script || "", sourceCode);
  } catch (error) {
    showVcfDepotMessage(error instanceof Error ? error.message : "The VCFDT profile script could not be rendered.");
  }
}

let vcfDepotProfilesTable = null;

function setVcfDepotDownloadActive(active, activeJobId = "") {
  if (!vcfDepotProfilesTable) {
    return;
  }
  const blocker = active
    ? `Wait for VCFDT task ${activeJobId || "in progress"} to finish before starting another download.`
    : "";
  vcfDepotProfilesTable.getRows().forEach((row) => {
    const data = row.getData();
    if (!data.is_new) {
      row.update({ download_active: active, active_task_blocker: blocker });
    }
  });
}

function initializeVcfDepotProfilesTable() {
  const tableElement = document.getElementById("vcf-depot-profiles-table");
  if (!(tableElement instanceof HTMLElement)) {
    return;
  }
  const fallback = document.getElementById(tableElement.dataset.fallbackId || "");
  if (typeof Tabulator === "undefined") {
    showVcfDepotMessage("Tabulator did not load. Showing the fallback table.");
    return;
  }
  const csrf = tableElement.dataset.csrf || "";
  const componentOptions = JSON.parse(tableElement.dataset.components || "[]");
  const componentValues = {
    "": "All components",
    ...Object.fromEntries(componentOptions.map((item) => [item.value, item.label])),
  };
  const esxPlatformOptions = JSON.parse(tableElement.dataset.esxPlatforms || "[]");
  const esxPlatformValues = Object.fromEntries(esxPlatformOptions.map((item) => [item.value, item.label]));
  const rows = [
    ...JSON.parse(tableElement.dataset.profiles || "[]").map((row) => ({
      ...row,
      disabled_platforms: vcfDepotListValues(row.disabled_platforms),
    })),
    newVcfDepotProfileRow(),
  ];
  try {
    vcfDepotProfilesTable = new Tabulator(tableElement, {
      data: rows,
      index: "id",
      layout: "fitColumns",
      height: "380px",
      rowHeight: 34,
      placeholder: "No VCFDT download profiles configured.",
      reactiveData: false,
      rowContextMenu: [
        {
          label: "Preview script",
          action: (_event, row) => previewVcfDepotProfileScript(row),
          disabled: (component) => Boolean(component.getData().is_new),
        },
        {
          label: "Start download",
          action: (_event, row) => startVcfDepotProfileDownload(row, csrf),
          disabled: (component) => {
            const data = component.getData();
            return data.is_new || !data.enabled || !data.can_start;
          },
        },
        {
          label: "Delete profile",
          action: (_event, row) => deleteVcfDepotProfileFromMenu(row, csrf),
        },
      ],
      columns: lockNewRecordColumns([
        {
          title: "Name",
          field: "name",
          editor: "input",
          formatter: (cell) => dnsAddRowHintFormatter(cell, "+ Add profile here"),
          minWidth: 180,
          cellEdited: (cell) => autoSaveVcfDepotProfile(cell, csrf),
        },
        {
          title: "Start",
          field: "start",
          formatter: (cell) => {
            const data = cell.getRow().getData();
            const disabled = data.is_new || data.download_active || !data.enabled || !data.can_start ? " disabled" : "";
            const blocker = data.active_task_blocker || data.start_blocker;
            const title = blocker ? ` title="${escapeHtml(blocker)}"` : "";
            return `<button class="button tiny secondary" type="button" data-vcf-depot-start-download${disabled}${title}>Start</button>`;
          },
          width: 90,
          hozAlign: "center",
          headerSort: false,
          cellClick: (_event, cell) => startVcfDepotProfileDownload(cell.getRow(), csrf),
        },
        {
          title: "Type",
          field: "profile_type",
          editor: "list",
          editorParams: { values: { binaries: "binaries", metadata: "metadata", esx: "esx" } },
          width: 110,
          cellEdited: (cell) => autoSaveVcfDepotProfile(cell, csrf),
        },
        {
          title: "Enabled",
          field: "enabled",
          formatter: labFoundryBooleanFormatter,
          editor: "tickCross",
          hozAlign: "center",
          width: 95,
          headerSort: false,
          cellEdited: (cell) => autoSaveVcfDepotProfile(cell, csrf),
        },
        {
          title: "SKU",
          field: "sku",
          editor: "list",
          editorParams: { values: { VCF: "VCF", VVF: "VVF" } },
          width: 85,
          cellEdited: (cell) => autoSaveVcfDepotProfile(cell, csrf),
        },
        {
          title: "VCF version",
          field: "vcf_version",
          editor: "input",
          width: 125,
          cellEdited: (cell) => autoSaveVcfDepotProfile(cell, csrf),
        },
        {
          title: "Binary type",
          field: "binary_type",
          editor: "list",
          editorParams: { values: { INSTALL: "INSTALL", UPGRADE: "UPGRADE" } },
          width: 125,
          cellEdited: (cell) => autoSaveVcfDepotProfile(cell, csrf),
        },
        {
          title: "Download mode",
          field: "download_mode",
          editor: "list",
          editorParams: {
            values: {
              automated_install: "Automated install",
              upgrades_only: "Upgrades only",
              patches_only: "Patches only",
            },
          },
          formatter: (cell) => ({
            automated_install: "Automated install",
            upgrades_only: "Upgrades only",
            patches_only: "Patches only",
          })[cell.getValue()] || "Automated install",
          minWidth: 150,
          cellEdited: (cell) => autoSaveVcfDepotProfile(cell, csrf),
        },
        {
          title: "Component",
          field: "component",
          editor: "list",
          editorParams: {
            values: componentValues,
            autocomplete: true,
            listOnEmpty: true,
            clearable: true,
          },
          formatter: (cell) => componentValues[cell.getValue()] || escapeHtml(cell.getValue()),
          minWidth: 210,
          cellEdited: (cell) => autoSaveVcfDepotProfile(cell, csrf),
        },
        {
          title: "Component version",
          field: "component_version",
          editor: "input",
          minWidth: 145,
          cellEdited: (cell) => autoSaveVcfDepotProfile(cell, csrf),
        },
        {
          title: "Disabled platforms",
          field: "disabled_platforms",
          editor: vcfDepotDisabledPlatformsEditor,
          editorParams: {
            values: esxPlatformValues,
          },
          formatter: (cell) => formatVcfDepotDisabledPlatforms(cell, esxPlatformValues, "none"),
          minWidth: 190,
          cssClass: "vcf-platforms-cell",
          cellEdited: (cell) => autoSaveVcfDepotProfile(cell, csrf),
        },
        {
          title: "Last run",
          field: "status",
          formatter: (cell) => ({
            planned: "Never run",
            ready: "Running",
            synced: "Succeeded",
            blocked: "Failed",
          })[cell.getValue()] || "Unknown",
          width: 110,
          headerSort: false,
        },
      ], "name"),
      rowFormatter: (row) => {
        markNewRecordRow(row, "name");
      },
    });
    if (fallback) {
      fallback.classList.add("hidden");
    }
  } catch (error) {
    showVcfDepotMessage(error instanceof Error ? error.message : "Tabulator could not render. Showing the fallback table.");
  }
}

let vcfDepotTasksTable = null;
let vcfDepotTasksRefreshInterval = null;
let vcfDepotTasksRefreshPending = false;

async function refreshVcfDepotTasksTable() {
  if (!vcfDepotTasksTable || vcfDepotTasksRefreshPending) {
    return;
  }
  vcfDepotTasksRefreshPending = true;
  try {
    await vcfDepotTasksTable.replaceData();
  } catch (error) {
    showVcfDepotMessage(error instanceof Error ? error.message : "Unable to refresh VCFDT tasks.");
  } finally {
    vcfDepotTasksRefreshPending = false;
  }
}

function initializeVcfDepotTasksTable() {
  const tableElement = document.getElementById("vcf-depot-tasks-table");
  if (!(tableElement instanceof HTMLElement) || typeof window.Tabulator !== "function") {
    return;
  }
  const fallback = document.getElementById(tableElement.dataset.fallbackId || "");
  try {
    const tasks = JSON.parse(tableElement.dataset.tasks || "[]");
    vcfDepotTasksTable = new window.Tabulator(tableElement, {
      data: tasks,
      ajaxURL: "/vcf-offline-depot/tasks/status",
      pagination: true,
      paginationMode: "remote",
      paginationSize: 10,
      paginationCounter: "rows",
      dataSendParams: { page: "page", size: "size" },
      ajaxResponse: (_url, _params, response) => {
        setVcfDepotDownloadActive(Boolean(response.download_active), response.active_job_id || "");
        return response;
      },
      layout: "fitColumns",
      height: "380px",
      placeholder: "No VCFDT tasks have been executed.",
      rowContextMenu: [
        {
          label: "View log",
          action: (_event, row) => {
            const logUrl = row.getData().log_url;
            if (logUrl) {
              openVcfDepotTaskLog(logUrl);
            }
          },
        },
      ],
      columns: [
        { title: "Task", field: "id", minWidth: 190, formatter: (cell) => `<code>${escapeHtml(cell.getValue())}</code>` },
        { title: "Profile", field: "profile_name", minWidth: 130, formatter: (cell) => escapeHtml(cell.getValue() || "Unknown profile") },
        {
          title: "State",
          field: "status",
          width: 105,
          headerSort: false,
          formatter: (cell) => {
            const value = String(cell.getValue() || "unknown");
            const pill = value === "succeeded" ? "success" : value === "failed" ? "error" : "warn";
            return `<span class="status-pill ${pill}">${escapeHtml(value)}</span>`;
          },
        },
        { title: "Mode", field: "dry_run", width: 90, formatter: (cell) => cell.getValue() === "yes" ? "dry-run" : "real" },
        { title: "Created", field: "created_at", minWidth: 210 },
        { title: "Started", field: "started_at", minWidth: 210, formatter: (cell) => escapeHtml(cell.getValue() || "—") },
        { title: "Finished", field: "finished_at", minWidth: 210, formatter: (cell) => escapeHtml(cell.getValue() || "—") },
      ],
    });
    fallback?.classList.add("hidden");
    if (vcfDepotTasksRefreshInterval) {
      window.clearInterval(vcfDepotTasksRefreshInterval);
    }
    vcfDepotTasksRefreshInterval = window.setInterval(refreshVcfDepotTasksTable, 2000);
  } catch (error) {
    showVcfDepotMessage(error instanceof Error ? error.message : "VCFDT tasks could not render. Showing the fallback table.");
  }
}

let vcfDepotTaskLogRefreshTimer = null;

async function openVcfDepotTaskLog(logUrl) {
  const modal = document.getElementById("vcf-depot-task-log-modal");
  const content = document.querySelector("[data-vcf-depot-task-log-content]");
  const title = document.querySelector("[data-vcf-depot-task-log-title]");
  const meta = document.querySelector("[data-vcf-depot-task-log-meta]");
  if (!(modal instanceof HTMLDialogElement) || !(content instanceof HTMLElement)) {
    return;
  }
  content.textContent = "Loading task log…";
  if (vcfDepotTaskLogRefreshTimer) {
    window.clearTimeout(vcfDepotTaskLogRefreshTimer);
    vcfDepotTaskLogRefreshTimer = null;
  }
  modal.showModal();
  const loadLog = async () => {
    try {
      const response = await fetch(logUrl, { headers: { "X-LabFoundry-Task-Log": "1" } });
      const payload = await response.json();
      if (!response.ok) {
        throw new Error(payload.detail || "Unable to load the VCFDT task log.");
      }
      if (title instanceof HTMLElement) {
        title.textContent = `${payload.profile_name || "Unknown profile"} task log`;
      }
      if (meta instanceof HTMLElement) {
        meta.textContent = `${payload.job_id} · ${payload.status} · ${payload.updated_at || "not written"}`;
      }
      content.textContent = payload.text || "No task log is available.";
      registerLabFoundryPrismLanguages();
      if (window.Prism && typeof window.Prism.highlightElement === "function") {
        window.Prism.highlightElement(content);
      }
      if (modal.open && ["pending", "running"].includes(payload.status)) {
        vcfDepotTaskLogRefreshTimer = window.setTimeout(loadLog, 2000);
      }
    } catch (error) {
      content.textContent = error instanceof Error ? error.message : "Unable to load the VCFDT task log.";
    }
  };
  await loadLog();
}

function initializeVcfDepotTaskLogModal() {
  const modal = document.getElementById("vcf-depot-task-log-modal");
  const closeButton = document.querySelector("[data-vcf-depot-task-log-close]");
  if (modal instanceof HTMLDialogElement && closeButton instanceof HTMLButtonElement) {
    closeButton.addEventListener("click", () => {
      if (vcfDepotTaskLogRefreshTimer) {
        window.clearTimeout(vcfDepotTaskLogRefreshTimer);
        vcfDepotTaskLogRefreshTimer = null;
      }
      modal.close();
    });
  }
}

let labFoundryTasksTable = null;
let labFoundryTasks = [];
let labFoundrySelectedTaskId = "";
let labFoundryTasksRefreshTimer = 0;

function taskStatusActive(status) {
  return ["pending", "running"].includes(String(status || ""));
}

function taskById(taskId) {
  return labFoundryTasks.find((task) => task.id === taskId) || null;
}

function taskStatusPillHtml(task) {
  return `<span class="status-pill ${escapeHtml(task.status_pill || "muted")}">${escapeHtml(task.status || "unknown")}</span>`;
}

function renderTaskDetail(task) {
  const modal = document.getElementById("task-detail-modal");
  if (!(modal instanceof HTMLDialogElement) || !task) {
    return;
  }
  labFoundrySelectedTaskId = task.id;
  const title = modal.querySelector("[data-task-detail-title]");
  const statusPill = modal.querySelector("[data-task-detail-status]");
  const summary = modal.querySelector("[data-task-detail-summary]");
  const facts = modal.querySelector("[data-task-detail-facts]");
  const error = modal.querySelector("[data-task-detail-error]");
  const result = modal.querySelector("[data-task-detail-result]");
  const cancelButton = modal.querySelector("[data-task-detail-cancel]");
  const logButton = modal.querySelector("[data-task-detail-log]");
  if (title instanceof HTMLElement) {
    title.textContent = `${task.type_label || task.type || "Task"} ${task.id}`;
  }
  if (statusPill instanceof HTMLElement) {
    statusPill.className = `status-pill ${task.status_pill || "muted"}`;
    statusPill.textContent = task.status || "unknown";
  }
  if (summary instanceof HTMLElement) {
    summary.textContent = task.summary || task.state || "";
  }
  if (facts instanceof HTMLElement) {
    facts.replaceChildren();
    [
      ["Type", task.type_label || task.type || "—"],
      ["State", task.state || task.status || "—"],
      ["Progress", `${task.progress_percent || 0}%`],
      ["Created", task.created_at || "—"],
      ["Started", task.started_at || "—"],
      ["Finished", task.finished_at || "—"],
      ["Created by", task.created_by || "—"],
    ].forEach(([label, value]) => {
      const row = document.createElement("div");
      const key = document.createElement("span");
      const val = document.createElement("strong");
      key.textContent = label;
      val.textContent = value;
      row.append(key, val);
      facts.append(row);
    });
  }
  if (error instanceof HTMLElement) {
    error.textContent = task.error || "";
    error.classList.toggle("hidden", !task.error);
  }
  if (result instanceof HTMLElement) {
    result.textContent = task.result_json || "{}";
  }
  if (cancelButton instanceof HTMLButtonElement) {
    cancelButton.classList.toggle("hidden", !task.can_cancel);
    cancelButton.disabled = !task.can_cancel;
    cancelButton.dataset.taskId = task.id;
  }
  if (logButton instanceof HTMLButtonElement) {
    logButton.dataset.taskId = task.id;
  }
}

function openTaskDetail(taskOrId) {
  const task = typeof taskOrId === "string" ? taskById(taskOrId) : taskOrId;
  const modal = document.getElementById("task-detail-modal");
  if (!(modal instanceof HTMLDialogElement) || !task) {
    return;
  }
  renderTaskDetail(task);
  const url = new URL(window.location.href);
  url.searchParams.set("job_id", task.id);
  window.history.replaceState({}, "", url);
  if (!modal.open) {
    modal.showModal();
  }
}

async function refreshTasksPage({ reopen = false } = {}) {
  const page = document.querySelector("[data-tasks-page]");
  if (!(page instanceof HTMLElement)) {
    return;
  }
  const queryId = labFoundrySelectedTaskId || page.dataset.selectedTaskId || "";
  const response = await fetch(`/tasks/status?job_id=${encodeURIComponent(queryId)}`, { credentials: "same-origin" });
  const payload = await response.json();
  if (!response.ok) {
    throw new Error(payload.detail || "Unable to refresh tasks.");
  }
  labFoundryTasks = Array.isArray(payload.tasks) ? payload.tasks : [];
  const count = document.querySelector("[data-tasks-count]");
  if (count instanceof HTMLElement) {
    const active = Number(payload.active_count || 0);
    count.textContent = active ? `${active} active · ${labFoundryTasks.length} total` : `${labFoundryTasks.length} tasks`;
    count.className = `status-pill ${active ? "warn" : "muted"}`;
  }
  if (labFoundryTasksTable) {
    labFoundryTasksTable.replaceData(labFoundryTasks);
  }
  const selected = payload.selected_task || taskById(queryId);
  if (selected && (reopen || document.getElementById("task-detail-modal")?.open)) {
    renderTaskDetail(selected);
    if (reopen) {
      openTaskDetail(selected);
    }
  }
  window.clearTimeout(labFoundryTasksRefreshTimer);
  if (labFoundryTasks.some((task) => taskStatusActive(task.status))) {
    labFoundryTasksRefreshTimer = window.setTimeout(() => refreshTasksPage().catch(() => {}), 2000);
  }
}

async function openTaskLog(taskId) {
  const modal = document.getElementById("task-log-modal");
  const title = document.querySelector("[data-task-log-title]");
  const meta = document.querySelector("[data-task-log-meta]");
  const content = document.querySelector("[data-task-log-content]");
  if (!(modal instanceof HTMLDialogElement) || !(content instanceof HTMLElement)) {
    return;
  }
  content.textContent = "Loading task log…";
  if (title instanceof HTMLElement) {
    title.textContent = "Task log";
  }
  if (meta instanceof HTMLElement) {
    meta.textContent = taskId;
  }
  if (!modal.open) {
    modal.showModal();
  }
  try {
    const response = await fetch(`/tasks/${encodeURIComponent(taskId)}/log`, { credentials: "same-origin" });
    const payload = await response.json();
    if (!response.ok) {
      throw new Error(payload.detail || "Unable to load task log.");
    }
    if (title instanceof HTMLElement) {
      title.textContent = payload.title || "Task log";
    }
    if (meta instanceof HTMLElement) {
      meta.textContent = `${payload.job_id || taskId} · ${payload.status || "unknown"}`;
    }
    content.textContent = payload.text || "No task log is available.";
  } catch (error) {
    content.textContent = error instanceof Error ? error.message : "Unable to load task log.";
  }
}

async function cancelTask(taskId) {
  const page = document.querySelector("[data-tasks-page]");
  if (!(page instanceof HTMLElement)) {
    return;
  }
  const confirmed = await requestConfirmation({
    title: "Cancel task",
    message: `Cancel task ${taskId}? If the worker is already inside a target system operation, LabFoundry will request cancellation and record the task as cancelled.`,
    label: "Cancel task",
  });
  if (!confirmed) {
    return;
  }
  const body = new URLSearchParams();
  body.set("csrf", page.dataset.csrf || "");
  const response = await fetch(`/tasks/${encodeURIComponent(taskId)}/cancel`, {
    method: "POST",
    credentials: "same-origin",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body,
  });
  const payload = await response.json();
  if (!response.ok) {
    throw new Error(payload.detail || "Unable to cancel task.");
  }
  await refreshTasksPage();
  const task = payload.task || taskById(taskId);
  if (task) {
    renderTaskDetail(task);
  }
}

function initializeTasksPage() {
  const page = document.querySelector("[data-tasks-page]");
  if (!(page instanceof HTMLElement)) {
    return;
  }
  try {
    labFoundryTasks = JSON.parse(page.dataset.tasks || "[]");
  } catch (_error) {
    labFoundryTasks = [];
  }
  labFoundrySelectedTaskId = page.dataset.selectedTaskId || new URLSearchParams(window.location.search).get("job_id") || "";
  const tableElement = document.getElementById("tasks-table");
  const fallback = document.getElementById(tableElement?.dataset.fallbackId || "");
  if (tableElement instanceof HTMLElement && typeof window.Tabulator === "function") {
    labFoundryTasksTable = new window.Tabulator(tableElement, {
      data: labFoundryTasks,
      layout: "fitColumns",
      height: "100%",
      pagination: true,
      paginationMode: "local",
      paginationSize: 25,
      paginationCounter: "rows",
      placeholder: "No tasks have been recorded yet.",
      selectableRows: 1,
      rowClick: (_event, row) => {
        labFoundrySelectedTaskId = row.getData().id || "";
        row.select();
      },
      rowContextMenu: [
        {
          label: "Details",
          action: (_event, row) => openTaskDetail(row.getData()),
        },
        {
          label: "Log",
          action: (_event, row) => openTaskLog(row.getData().id),
        },
        {
          label: "Cancel task",
          disabled: (component) => !component.getData().can_cancel,
          action: (_event, row) => {
            const task = row.getData();
            if (!task.can_cancel) {
              return;
            }
            cancelTask(task.id).catch((error) => window.alert(error instanceof Error ? error.message : "Unable to cancel task."));
          },
        },
      ],
      columns: [
        { title: "Status", field: "status", width: 130, formatter: (cell) => taskStatusPillHtml(cell.getRow().getData()) },
        { title: "Task", field: "id", minWidth: 190, formatter: (cell) => `<code>${escapeHtml(cell.getValue())}</code>` },
        { title: "Type", field: "type_label", minWidth: 190, formatter: (cell) => escapeHtml(cell.getValue() || "") },
        { title: "State", field: "state", minWidth: 170, formatter: (cell) => escapeHtml(cell.getValue() || "") },
        { title: "Summary", field: "summary", minWidth: 180, formatter: (cell) => escapeHtml(cell.getValue() || "—") },
        { title: "Progress", field: "progress_percent", width: 105, formatter: (cell) => `${Number(cell.getValue() || 0)}%` },
        { title: "Created", field: "created_at", minWidth: 210, formatter: (cell) => escapeHtml(cell.getValue() || "—") },
        { title: "Finished", field: "finished_at", minWidth: 210, formatter: (cell) => escapeHtml(cell.getValue() || "—") },
      ],
    });
    labFoundryTasksTable.on("rowDblClick", (_event, row) => openTaskDetail(row.getData()));
    fallback?.classList.add("hidden");
  }
  document.querySelector("[data-task-detail-close]")?.addEventListener("click", () => document.getElementById("task-detail-modal")?.close());
  document.querySelector("[data-task-log-close]")?.addEventListener("click", () => document.getElementById("task-log-modal")?.close());
  document.querySelector("[data-task-detail-log]")?.addEventListener("click", () => {
    if (labFoundrySelectedTaskId) {
      openTaskLog(labFoundrySelectedTaskId);
    }
  });
  document.querySelector("[data-task-detail-cancel]")?.addEventListener("click", () => {
    if (labFoundrySelectedTaskId) {
      cancelTask(labFoundrySelectedTaskId).catch((error) => window.alert(error instanceof Error ? error.message : "Unable to cancel task."));
    }
  });
  const shouldOpenSelected = Boolean(labFoundrySelectedTaskId);
  refreshTasksPage({ reopen: shouldOpenSelected }).catch(() => {
    if (shouldOpenSelected) {
      openTaskDetail(labFoundrySelectedTaskId);
    }
  });
}

function updateVcfDepotSummary(form, payload = {}) {
  const portInput = form.querySelector('input[name="port"]');
  const hostnameInput = form.querySelector('input[name="hostname"]');
  const userSelect = form.querySelector('select[name="http_user_id"]');
  const unauthenticatedInput = form.querySelector('input[name="allow_unauthenticated_access"]');
  const { interfaceLabel: bindInterfaceLabel, address, addressLabel, addresses } = serviceBindSelection(form, payload);
  const port = payload.port || portInput?.value || "443";
  const hostname = payload.hostname || hostnameInput?.value || "";
  const selectedUsername =
    userSelect instanceof HTMLSelectElement
      ? (userSelect.selectedOptions[0]?.textContent || "").replace(/\s+\(disabled\)\s*$/, "").trim()
      : "";
  const allowUnauthenticated =
    payload.allow_unauthenticated_access !== undefined
      ? Boolean(payload.allow_unauthenticated_access)
      : unauthenticatedInput instanceof HTMLInputElement && unauthenticatedInput.checked;
  const endpointValue = payload.endpoint || (port === "443" || port === 443 ? hostname : `${hostname}:${port}`);
  const endpoint = document.querySelector("[data-vcf-depot-endpoint]");
  const interfaceLabel = document.querySelector("[data-vcf-depot-interface]");
  const storePaths = document.querySelectorAll("[data-vcf-depot-store]");
  const toolVersions = document.querySelectorAll("[data-vcf-depot-tool-version]");
  const toolStatuses = document.querySelectorAll("[data-vcf-depot-tool-status]");
  const toolArchiveNames = document.querySelectorAll("[data-vcf-depot-tool-archive-name]");
  const toolUploadActions = document.querySelectorAll("[data-vcf-depot-tool-upload-action]");
  const toolUploadNames = document.querySelectorAll("[data-vcf-depot-tool-upload-name]");
  const toolResetPanels = document.querySelectorAll("[data-vcf-depot-tool-reset-panel], [data-vcf-depot-tool-reset-action]");
  const accessLabel = document.querySelector("[data-vcf-depot-access]");
  const dnsStatus = document.querySelector("[data-vcf-depot-dns-status]");
  const propertiesStatus = document.querySelector("[data-vcf-depot-properties-status]");
  const softwareDepotGenerateButtons = document.querySelectorAll("[data-vcf-depot-generate-id-modal-open]");
  if (endpoint instanceof HTMLElement) {
    endpoint.textContent = endpointValue || "depot hostname required";
  }
  if (interfaceLabel instanceof HTMLElement) {
    interfaceLabel.textContent = `${bindInterfaceLabel || "no interface"} / ${addressLabel || "no interface IP"}`;
  }
  if (payload.depot_store_path) {
    storePaths.forEach((storePath) => {
      if (storePath instanceof HTMLElement) {
        storePath.textContent = payload.depot_store_path;
      }
    });
  }
  if (accessLabel instanceof HTMLElement && (payload.allow_unauthenticated_access !== undefined || payload.http_username !== undefined || selectedUsername)) {
    accessLabel.textContent = allowUnauthenticated ? "unauthenticated" : payload.http_username || selectedUsername || "user required";
  }
  if (payload.tool_archive_name !== undefined) {
    const toolAvailable = Boolean(payload.tool_archive_name);
    toolStatuses.forEach((toolStatus) => {
      if (toolStatus instanceof HTMLElement) {
        toolStatus.textContent = toolAvailable ? "tool staged" : "upload required";
      }
    });
    toolArchiveNames.forEach((toolArchiveName) => {
      if (toolArchiveName instanceof HTMLElement) {
        toolArchiveName.textContent = payload.tool_archive_name || "no package staged";
      }
    });
    toolUploadActions.forEach((toolUploadAction) => {
      if (toolUploadAction instanceof HTMLElement) {
        toolUploadAction.textContent = toolAvailable ? "Update" : "Add";
      }
    });
    toolUploadNames.forEach((toolUploadName) => {
      if (toolUploadName instanceof HTMLElement) {
        toolUploadName.textContent = payload.tool_archive_name || "vcf-download-tool-*.tar.gz";
      }
    });
    toolResetPanels.forEach((toolResetPanel) => {
      if (toolResetPanel instanceof HTMLElement) {
        toolResetPanel.classList.toggle("hidden", !toolAvailable);
      }
    });
    softwareDepotGenerateButtons.forEach((softwareDepotGenerateButton) => {
      if (softwareDepotGenerateButton instanceof HTMLButtonElement) {
        softwareDepotGenerateButton.disabled = !toolAvailable;
      }
    });
    setVcfDepotToolDependentActions(toolAvailable);
  }
  if (payload.tool_version !== undefined) {
    toolVersions.forEach((toolVersion) => {
      if (toolVersion instanceof HTMLElement) {
        toolVersion.textContent = payload.tool_version || "not uploaded";
      }
    });
  }
  updateVcfDepotCredentialStatus(payload);
  if (propertiesStatus instanceof HTMLElement && payload.application_properties_source !== undefined) {
    propertiesStatus.textContent = payload.application_properties_updated_at
      ? `${payload.application_properties_source || "operator saved"} · saved`
      : payload.application_properties_source || "LabFoundry default";
  }
  updateVcfDepotSoftwareDepotId(payload);
  if (dnsStatus instanceof HTMLElement && payload.dns_record_action !== undefined) {
    const dnsMessages = {
      created: "DNS alias and target records created for this endpoint.",
      updated: "DNS alias and target records updated for this endpoint.",
      unchanged: "DNS alias already matches this endpoint.",
      "created+removed-old": "DNS alias created and old endpoint records removed.",
      "updated+removed-old": "DNS alias updated and old endpoint records removed.",
      "unchanged+removed-old": "Old endpoint DNS alias and target records removed.",
      "removed-old": "Old endpoint DNS alias and target records removed.",
    };
    dnsStatus.textContent = dnsMessages[payload.dns_record_action] || "DNS alias follows the first selected service listener.";
  }
  const livePreviewPayload = {
    ...payload,
    hostname,
    endpoint: endpointValue,
    listen_address: address,
    listen_addresses: addresses,
    port,
    http_username: payload.http_username || selectedUsername,
    allow_unauthenticated_access: allowUnauthenticated,
    server_certificate: payload.server_certificate || hostname,
  };
  updateVcfDepotHttpsPreview(livePreviewPayload);
}

function updateVcfDepotCredentialStatus(payload = {}) {
  const tokenStatus = document.querySelector("[data-vcf-depot-token-status]");
  const activationStatus = document.querySelector("[data-vcf-depot-activation-status]");
  const credentialSeparator = document.querySelector("[data-vcf-depot-credential-separator]");
  const credentialsStatus = document.querySelector("[data-vcf-depot-credentials-status]");
  const currentTokenText = tokenStatus instanceof HTMLElement ? tokenStatus.textContent?.trim() || "" : "";
  const currentActivationText = activationStatus instanceof HTMLElement ? activationStatus.textContent?.trim() || "" : "";
  const tokenPresent =
    payload.download_token_present !== undefined ? Boolean(payload.download_token_present) : Boolean(currentTokenText && currentTokenText !== "token not uploaded");
  const activationPresent =
    payload.activation_code_present !== undefined
      ? Boolean(payload.activation_code_present)
      : Boolean(currentActivationText && currentActivationText !== "code not uploaded");
  const tokenName = payload.download_token_name || (tokenPresent ? currentTokenText : "");
  const activationName = payload.activation_code_name || (activationPresent ? currentActivationText : "");

  if (tokenStatus instanceof HTMLElement && payload.download_token_present !== undefined) {
    tokenStatus.textContent = tokenPresent ? tokenName || "token uploaded" : activationPresent ? "" : "token not uploaded";
  }
  if (activationStatus instanceof HTMLElement && payload.activation_code_present !== undefined) {
    activationStatus.textContent = activationPresent ? activationName || "code uploaded" : tokenPresent ? "" : "code not uploaded";
  }
  if (credentialSeparator instanceof HTMLElement) {
    credentialSeparator.textContent = tokenPresent && activationPresent ? " / " : tokenPresent || activationPresent ? "" : " / ";
  }
  if (credentialsStatus instanceof HTMLElement) {
    const staged = [];
    if (tokenPresent) {
      staged.push(`Download token staged${tokenName && tokenName !== "token uploaded" ? `: ${tokenName}` : ""}`);
    }
    if (activationPresent) {
      staged.push(`Activation code staged${activationName && activationName !== "code uploaded" ? `: ${activationName}` : ""}`);
    }
    credentialsStatus.textContent = staged.length
      ? `${staged.join(" · ")}. Runtime files refresh during Appliance Apply or profile download.`
      : "No Broadcom credentials staged.";
  }
}

function setVcfDepotToolDependentActions(toolAvailable) {
  document.querySelectorAll("[data-vcf-depot-requires-tool]").forEach((control) => {
    if (control instanceof HTMLButtonElement) {
      control.disabled = !toolAvailable;
    }
  });
}

function updateVcfDepotSoftwareDepotId(payload = {}) {
  const softwareDepotId = document.querySelector("[data-vcf-depot-software-depot-id]");
  const softwareDepotCell = document.querySelector("[data-vcf-depot-software-depot-cell]");
  const softwareDepotMessage = document.querySelector("[data-vcf-depot-software-depot-message]");
  const softwareDepotCopy = document.querySelector("[data-vcf-depot-software-depot-copy]");
  if (softwareDepotId instanceof HTMLElement && payload.software_depot_id !== undefined) {
    const depotId = payload.software_depot_id || "";
    if (softwareDepotId instanceof HTMLInputElement) {
      softwareDepotId.value = depotId;
    } else {
      softwareDepotId.textContent = depotId;
    }
    softwareDepotId.classList.toggle("hidden", !depotId);
    if (softwareDepotCopy instanceof HTMLButtonElement) {
      softwareDepotCopy.dataset.copyValue = depotId;
      softwareDepotCopy.classList.toggle("hidden", !depotId);
    }
    const button = softwareDepotCell?.querySelector("[data-vcf-depot-generate-id-modal-open]");
    if (button instanceof HTMLButtonElement) {
      button.textContent = "↻";
      button.classList.add("icon-button");
      button.classList.remove("compact-button");
      button.setAttribute("aria-label", "Refresh software depot ID");
      button.setAttribute("title", "Refresh software depot ID");
    }
  }
  if (softwareDepotMessage instanceof HTMLElement && (payload.software_depot_id_error !== undefined || payload.software_depot_id_generated_at !== undefined)) {
    if (payload.software_depot_id_error) {
      softwareDepotMessage.textContent = payload.software_depot_id_error;
      softwareDepotMessage.classList.add("error-text");
    } else if (payload.software_depot_id_generated_at) {
      softwareDepotMessage.textContent = `Generated ${new Date(payload.software_depot_id_generated_at).toLocaleString()}.`;
      softwareDepotMessage.classList.remove("error-text");
    } else {
      softwareDepotMessage.textContent = "Upload VCFDT, then submit VCF Offline Depot through Appliance Apply to generate the software depot ID.";
      softwareDepotMessage.classList.remove("error-text");
    }
  }
}

function updateVcfDepotHttpsPreview(payload = {}) {
  const httpsPreview = document.querySelector("[data-vcf-depot-https-preview]");
  if (!(httpsPreview instanceof HTMLElement)) {
    return;
  }
  if (payload.https_config_preview !== undefined) {
    httpsPreview.textContent = payload.https_config_preview;
    highlightConfigPreviewElement(httpsPreview);
    return;
  }
  const hostname = payload.hostname || "depot.labfoundry.internal";
  const endpoint = payload.endpoint || hostname;
  const port = payload.port || "443";
  const listenAddresses = Array.isArray(payload.listen_addresses)
    ? payload.listen_addresses
    : String(payload.listen_address || "")
        .split(/[\n,]+/)
        .map((value) => value.trim())
        .filter(Boolean);
  const listenLines = (listenAddresses.length ? listenAddresses : ["0.0.0.0"]).map((listenAddress) => `  listen ${listenAddress}:${port} ssl;`);
  const depotStorePath = payload.depot_store_path || document.querySelector("[data-vcf-depot-store]")?.textContent || "/mnt/labfoundry-vcf-offline-depot";
  const certificateName = payload.server_certificate || hostname;
  const username = payload.http_username || "vcf-depot";
  const authLines = payload.allow_unauthenticated_access
    ? []
    : [
        "    satisfy any;",
        '    auth_basic "VCF Offline Depot";',
        "    auth_basic_user_file /etc/labfoundry/nginx/htpasswd/vcf-offline-depot.htpasswd;",
        "    auth_request /_labfoundry_depot_auth;",
        "    error_page 401 = /_labfoundry_depot_login;",
      ];
  httpsPreview.textContent = [
    "# Managed by LabFoundry. Local changes may be overwritten.",
    "# Dry-run preview of desired HTTPS endpoint for the VCF Offline Depot.",
    `# Depot store: ${depotStorePath}`,
    `# VCF endpoint: https://${endpoint}/PROD/`,
    `# LabFoundry VCF Offline Depot unauthenticated access: ${payload.allow_unauthenticated_access ? "true" : "false"}`,
    `# LabFoundry VCF Offline Depot user: ${payload.allow_unauthenticated_access ? "none" : username}`,
    "",
    "server {",
    ...listenLines,
    `  server_name ${hostname};`,
    `  ssl_certificate /etc/labfoundry/vcf-offline-depot/certs/${certificateName}.crt;`,
    `  ssl_certificate_key /etc/labfoundry/vcf-offline-depot/certs/${certificateName}.key;`,
    "",
    "  location = / {",
    "    proxy_pass http://127.0.0.1:8000;",
    "    proxy_set_header Host $host;",
    "    proxy_set_header X-Real-IP $remote_addr;",
    "    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;",
    "    proxy_set_header X-Forwarded-Proto https;",
    "  }",
    "",
    "  location ^~ /static/ {",
    "    proxy_pass http://127.0.0.1:8000;",
    "    proxy_set_header Host $host;",
    "    proxy_set_header X-Real-IP $remote_addr;",
    "    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;",
    "    proxy_set_header X-Forwarded-Proto https;",
    "  }",
    "",
    "  location = /favicon.ico {",
    "    proxy_pass http://127.0.0.1:8000;",
    "    proxy_set_header Host $host;",
    "    proxy_set_header X-Real-IP $remote_addr;",
    "    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;",
    "    proxy_set_header X-Forwarded-Proto https;",
    "  }",
    "",
    "  location = /manifest.webmanifest {",
    "    proxy_pass http://127.0.0.1:8000;",
    "    proxy_set_header Host $host;",
    "    proxy_set_header X-Real-IP $remote_addr;",
    "    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;",
    "    proxy_set_header X-Forwarded-Proto https;",
    "  }",
    "",
    "  location = /service-worker.js {",
    "    proxy_pass http://127.0.0.1:8000;",
    "    proxy_set_header Host $host;",
    "    proxy_set_header X-Real-IP $remote_addr;",
    "    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;",
    "    proxy_set_header X-Forwarded-Proto https;",
    "  }",
    "",
    "  location = /ca {",
    "    proxy_pass http://127.0.0.1:8000;",
    "    proxy_set_header Host $host;",
    "    proxy_set_header X-Real-IP $remote_addr;",
    "    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;",
    "    proxy_set_header X-Forwarded-Proto https;",
    "  }",
    "",
    "  location ^~ /ca/ {",
    "    proxy_pass http://127.0.0.1:8000;",
    "    proxy_set_header Host $host;",
    "    proxy_set_header X-Real-IP $remote_addr;",
    "    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;",
    "    proxy_set_header X-Forwarded-Proto https;",
    "  }",
    "",
    "  location = /requests {",
    "    proxy_pass http://127.0.0.1:8000;",
    "    proxy_set_header Host $host;",
    "    proxy_set_header X-Real-IP $remote_addr;",
    "    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;",
    "    proxy_set_header X-Forwarded-Proto https;",
    "  }",
    "",
    "  location ^~ /requests/ {",
    "    proxy_pass http://127.0.0.1:8000;",
    "    proxy_set_header Host $host;",
    "    proxy_set_header X-Real-IP $remote_addr;",
    "    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;",
    "    proxy_set_header X-Forwarded-Proto https;",
    "  }",
    "",
    "  location = /PROD {",
    "    return 301 /PROD/;",
    "  }",
    "",
    "  location = /PROD/login {",
    "    proxy_pass http://127.0.0.1:8000;",
    "    proxy_set_header Host $host;",
    "    proxy_set_header X-Real-IP $remote_addr;",
    "    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;",
    "    proxy_set_header X-Forwarded-Proto https;",
    "  }",
    "",
    "  location = /PROD/logout {",
    "    proxy_pass http://127.0.0.1:8000;",
    "    proxy_set_header Host $host;",
    "    proxy_set_header X-Real-IP $remote_addr;",
    "    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;",
    "    proxy_set_header X-Forwarded-Proto https;",
    "  }",
    "",
    "  location = /_labfoundry_depot_auth {",
    "    internal;",
    "    proxy_pass http://127.0.0.1:8000/PROD/auth-check;",
    "    proxy_pass_request_body off;",
    "    proxy_set_header Content-Length \"\";",
    "    proxy_set_header Host $host;",
    "    proxy_set_header X-Original-URI $request_uri;",
    "    proxy_set_header X-Real-IP $remote_addr;",
    "    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;",
    "    proxy_set_header X-Forwarded-Proto https;",
    "  }",
    "",
    "  location = /_labfoundry_depot_login {",
    "    internal;",
    "    proxy_pass http://127.0.0.1:8000/PROD/auth-failure;",
    "    proxy_pass_request_body off;",
    '    proxy_set_header Content-Length "";',
    "    proxy_set_header Host $host;",
    "    proxy_set_header X-Original-URI $request_uri;",
    "    proxy_set_header X-Forwarded-Proto https;",
    "  }",
    "",
    "  location = /PROD/ {",
    ...(payload.allow_unauthenticated_access
      ? []
      : [
          "    satisfy any;",
          '    auth_basic "VCF Offline Depot";',
          "    auth_basic_user_file /etc/labfoundry/nginx/htpasswd/vcf-offline-depot.htpasswd;",
          "    auth_request /_labfoundry_depot_auth;",
          "    error_page 401 = /_labfoundry_depot_login;",
        ]),
    "    proxy_pass http://127.0.0.1:8000;",
    "    proxy_set_header Host $host;",
    "    proxy_set_header X-Real-IP $remote_addr;",
    "    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;",
    "    proxy_set_header X-Forwarded-Proto https;",
    "    proxy_set_header X-LabFoundry-Depot-Basic-User $remote_user;",
    "  }",
    "",
    "  location ~ ^/PROD/.*/$ {",
    ...(payload.allow_unauthenticated_access
      ? []
      : [
          "    satisfy any;",
          '    auth_basic "VCF Offline Depot";',
          "    auth_basic_user_file /etc/labfoundry/nginx/htpasswd/vcf-offline-depot.htpasswd;",
          "    auth_request /_labfoundry_depot_auth;",
          "    error_page 401 = /_labfoundry_depot_login;",
        ]),
    "    proxy_pass http://127.0.0.1:8000;",
    "    proxy_set_header Host $host;",
    "    proxy_set_header X-Real-IP $remote_addr;",
    "    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;",
    "    proxy_set_header X-Forwarded-Proto https;",
    "    proxy_set_header X-LabFoundry-Depot-Basic-User $remote_user;",
    "  }",
    "",
    "  location ~ ^/PROD/(?!login$|logout$|auth-check$)(.+[^/])$ {",
    ...authLines,
    `    alias ${depotStorePath.replace(/\/+$/, "")}/PROD/$1;`,
    "    sendfile on;",
    "    tcp_nopush on;",
    "    directio 8m;",
    "    autoindex off;",
    "    types { }",
    "    default_type application/octet-stream;",
    "  }",
    "",
    "  location / {",
    "    return 404;",
    "  }",
    "}",
  ].join("\n") + "\n";
  highlightConfigPreviewElement(httpsPreview);
}

function updateVcfDepotValidation(payload = {}) {
  const status = document.querySelector("[data-vcf-depot-validation-status]");
  const validationPanel = status?.closest(".panel");
  const applyButton = document.querySelector("[data-vcf-depot-apply-button]");
  const configPath = document.querySelector("[data-vcf-depot-config-path]");
  const httpsPreview = document.querySelector("[data-vcf-depot-https-preview]");
  const commandPreview = document.querySelector("[data-vcf-depot-command-preview]");
  const errors = Array.isArray(payload.validation_errors) ? payload.validation_errors : [];
  const warnings = Array.isArray(payload.validation_warnings) ? payload.validation_warnings : [];
  if (status instanceof HTMLElement && payload.valid !== undefined) {
    status.textContent = payload.valid ? "valid" : "needs attention";
    status.classList.toggle("good", Boolean(payload.valid));
    status.classList.toggle("warn", !payload.valid);
  }
  if (applyButton instanceof HTMLButtonElement && payload.valid !== undefined) {
    applyButton.disabled = !payload.valid;
  }
  if (configPath instanceof HTMLElement && payload.config_path) {
    configPath.textContent = payload.config_path;
  }
  updateVcfDepotHttpsPreview(payload);
  if (commandPreview instanceof HTMLElement && payload.command_preview !== undefined) {
    commandPreview.textContent = payload.command_preview;
    highlightConfigPreviewElement(commandPreview);
  }
  if (validationPanel instanceof HTMLElement && payload.valid !== undefined) {
    const previewAnchor = validationPanel.querySelector("[data-config-preview-row]");
    let errorList = validationPanel.querySelector("[data-vcf-depot-validation-errors]");
    let message = validationPanel.querySelector("[data-vcf-depot-validation-message]");
    let warningList = validationPanel.querySelector("[data-vcf-depot-validation-warnings]");
    if (payload.valid) {
      if (errorList) {
        errorList.remove();
      }
      if (!(message instanceof HTMLElement)) {
        message = document.createElement("p");
        message.className = "muted";
        message.setAttribute("data-vcf-depot-validation-message", "");
        validationPanel.insertBefore(message, previewAnchor);
      }
      message.textContent = "The desired VCF Offline Depot state passes LabFoundry validation. Appliance validation still runs through the allowlisted depot helper before apply.";
    } else {
      if (message) {
        message.remove();
      }
      if (!(errorList instanceof HTMLElement)) {
        errorList = document.createElement("ul");
        errorList.className = "error-list";
        errorList.setAttribute("data-vcf-depot-validation-errors", "");
        validationPanel.insertBefore(errorList, previewAnchor);
      }
      errorList.innerHTML = "";
      errors.forEach((error) => {
        const item = document.createElement("li");
        item.textContent = error;
        errorList.appendChild(item);
      });
    }
    if (!(warningList instanceof HTMLElement)) {
      warningList = document.createElement("ul");
      warningList.className = "warning-list";
      warningList.setAttribute("data-vcf-depot-validation-warnings", "");
      validationPanel.insertBefore(warningList, previewAnchor);
    }
    warningList.innerHTML = "";
    warnings.forEach((warning) => {
      const item = document.createElement("li");
      item.textContent = warning;
      warningList.appendChild(item);
    });
    warningList.classList.toggle("hidden", warnings.length === 0);
  }
}

function initializeVcfDepotSettings() {
  document.querySelectorAll("[data-vcf-depot-settings]").forEach((form) => {
    if (!(form instanceof HTMLFormElement)) {
      return;
    }
    const portInput = form.querySelector('input[name="port"]');
    const hostnameInput = form.querySelector('input[name="hostname"]');
    const refresh = () => updateVcfDepotSummary(form);
    [portInput, hostnameInput].forEach((input) => {
      if (input instanceof HTMLElement) {
        input.addEventListener("input", refresh);
        input.addEventListener("change", refresh);
      }
    });
    form.addEventListener("labfoundry:autosave-success", (event) => {
      const payload = event.detail || {};
      updateVcfDepotSummary(form, payload);
      updateVcfDepotValidation(payload);
      if (payload.tool_archive_uploaded) {
        window.location.reload();
      }
    });
    refresh();
  });
}

function initializeVcfDepotSoftwareDepotIdGenerator() {
  const modal = document.getElementById("vcf-depot-generate-id-modal");
  document.querySelectorAll("[data-vcf-depot-generate-id-modal-open]").forEach((button) => {
    if (!(button instanceof HTMLButtonElement)) {
      return;
    }
    button.addEventListener("click", () => {
      if (modal instanceof HTMLDialogElement) {
        modal.showModal();
      }
    });
  });
  document.querySelectorAll("[data-vcf-depot-generate-id-modal-cancel]").forEach((button) => {
    if (!(button instanceof HTMLButtonElement)) {
      return;
    }
    button.addEventListener("click", () => {
      if (modal instanceof HTMLDialogElement) {
        modal.close("cancel");
      }
    });
  });
}

function initializeVcfDepotToolResetModal() {
  const modal = document.getElementById("vcf-depot-tool-reset-modal");
  document.querySelectorAll("[data-vcf-depot-tool-reset-modal-open]").forEach((button) => {
    if (!(button instanceof HTMLButtonElement)) {
      return;
    }
    button.addEventListener("click", () => {
      if (modal instanceof HTMLDialogElement) {
        modal.showModal();
      }
    });
  });
  document.querySelectorAll("[data-vcf-depot-tool-reset-modal-cancel]").forEach((button) => {
    if (!(button instanceof HTMLButtonElement)) {
      return;
    }
    button.addEventListener("click", () => {
      if (modal instanceof HTMLDialogElement) {
        modal.close("cancel");
      }
    });
  });
}

function initializeVcfDepotTokenPaste() {
  const modal = document.getElementById("vcf-depot-token-modal");
  document.querySelectorAll("[data-vcf-depot-token-modal-open]").forEach((button) => {
    if (!(button instanceof HTMLButtonElement)) {
      return;
    }
    button.addEventListener("click", () => {
      if (modal instanceof HTMLDialogElement) {
        modal.showModal();
      }
    });
  });
  document.querySelectorAll("[data-vcf-depot-token-modal-cancel]").forEach((button) => {
    if (!(button instanceof HTMLButtonElement)) {
      return;
    }
    button.addEventListener("click", () => {
      if (modal instanceof HTMLDialogElement) {
        modal.close("cancel");
      }
    });
  });
  document.querySelectorAll("[data-vcf-depot-token-paste]").forEach((form) => {
    if (!(form instanceof HTMLFormElement)) {
      return;
    }
    const button = form.querySelector("button[type='submit']");
    const textarea = form.querySelector('textarea[name="download_token_text"]');
    const fileInput = form.querySelector('input[name="download_token_file"]');
    const status = form.querySelector("[data-vcf-depot-token-paste-status]");
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const hasPastedToken = textarea instanceof HTMLTextAreaElement && Boolean(textarea.value.trim());
      const hasTokenFile = fileInput instanceof HTMLInputElement && Boolean(fileInput.files?.length);
      if (!hasPastedToken && !hasTokenFile) {
        if (status instanceof HTMLElement) {
          status.textContent = "Choose a token file or paste token text.";
          status.classList.add("error-text");
        }
        return;
      }
      if (button instanceof HTMLButtonElement) {
        button.disabled = true;
      }
      if (status instanceof HTMLElement) {
        status.textContent = "Staging token file...";
        status.classList.remove("error-text");
      }
      try {
        const response = await fetch(form.action, {
          method: "POST",
          body: new FormData(form),
          credentials: "same-origin",
          headers: { "X-LabFoundry-Autosave": "1" },
        });
        const payload = await response.json();
        if (!response.ok) {
          throw new Error(payload.detail || "Download token could not be staged.");
        }
        const settingsForm = document.querySelector("[data-vcf-depot-settings]");
        if (settingsForm instanceof HTMLFormElement) {
          updateVcfDepotSummary(settingsForm, payload);
        }
        updateVcfDepotCredentialStatus(payload);
        updateVcfDepotValidation(payload);
        if (textarea instanceof HTMLTextAreaElement) {
          textarea.value = "";
        }
        if (fileInput instanceof HTMLInputElement) {
          fileInput.value = "";
        }
        form.querySelectorAll("[data-file-upload-name]").forEach((label) => {
          if (label instanceof HTMLElement) {
            label.textContent = "no file selected";
          }
        });
        if (status instanceof HTMLElement) {
          status.textContent = "Token file staged. Contents are hidden.";
          status.classList.remove("error-text");
        }
        if (modal instanceof HTMLDialogElement && modal.open) {
          modal.close("saved");
        }
      } catch (error) {
        if (status instanceof HTMLElement) {
          status.textContent = error instanceof Error ? error.message : "Download token could not be staged.";
          status.classList.add("error-text");
        }
      } finally {
        if (button instanceof HTMLButtonElement) {
          button.disabled = false;
        }
      }
    });
  });
}

function initializeVcfDepotActivationPaste() {
  const modal = document.getElementById("vcf-depot-activation-modal");
  document.querySelectorAll("[data-vcf-depot-activation-modal-open]").forEach((button) => {
    if (!(button instanceof HTMLButtonElement)) {
      return;
    }
    button.addEventListener("click", () => {
      if (modal instanceof HTMLDialogElement) {
        modal.showModal();
      }
    });
  });
  document.querySelectorAll("[data-vcf-depot-activation-modal-cancel]").forEach((button) => {
    if (!(button instanceof HTMLButtonElement)) {
      return;
    }
    button.addEventListener("click", () => {
      if (modal instanceof HTMLDialogElement) {
        modal.close("cancel");
      }
    });
  });
  document.querySelectorAll("[data-vcf-depot-activation-paste]").forEach((form) => {
    if (!(form instanceof HTMLFormElement)) {
      return;
    }
    const button = form.querySelector("button[type='submit']");
    const textarea = form.querySelector('textarea[name="activation_code_text"]');
    const fileInput = form.querySelector('input[name="activation_code_file"]');
    const status = form.querySelector("[data-vcf-depot-activation-paste-status]");
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const hasPastedCode = textarea instanceof HTMLTextAreaElement && Boolean(textarea.value.trim());
      const hasCodeFile = fileInput instanceof HTMLInputElement && Boolean(fileInput.files?.length);
      if (!hasPastedCode && !hasCodeFile) {
        if (status instanceof HTMLElement) {
          status.textContent = "Choose an activation file or paste activation-code text.";
          status.classList.add("error-text");
        }
        return;
      }
      if (button instanceof HTMLButtonElement) {
        button.disabled = true;
      }
      if (status instanceof HTMLElement) {
        status.textContent = "Staging activation-code file...";
        status.classList.remove("error-text");
      }
      try {
        const response = await fetch(form.action, {
          method: "POST",
          body: new FormData(form),
          credentials: "same-origin",
          headers: { "X-LabFoundry-Autosave": "1" },
        });
        const payload = await response.json();
        if (!response.ok) {
          throw new Error(payload.detail || "Activation code could not be staged.");
        }
        const settingsForm = document.querySelector("[data-vcf-depot-settings]");
        if (settingsForm instanceof HTMLFormElement) {
          updateVcfDepotSummary(settingsForm, payload);
        }
        updateVcfDepotValidation(payload);
        if (textarea instanceof HTMLTextAreaElement) {
          textarea.value = "";
        }
        if (fileInput instanceof HTMLInputElement) {
          fileInput.value = "";
        }
        if (status instanceof HTMLElement) {
          status.textContent = "Activation-code file staged. Contents are hidden.";
          status.classList.remove("error-text");
        }
        if (modal instanceof HTMLDialogElement && modal.open) {
          modal.close("saved");
        }
      } catch (error) {
        if (status instanceof HTMLElement) {
          status.textContent = error instanceof Error ? error.message : "Activation code could not be staged.";
          status.classList.add("error-text");
        }
      } finally {
        if (button instanceof HTMLButtonElement) {
          button.disabled = false;
        }
      }
    });
  });
}

function initializeVcfDepotCredentialsPaste() {
  const modal = document.getElementById("vcf-depot-credentials-modal");
  document.querySelectorAll("[data-vcf-depot-credentials-modal-open]").forEach((button) => {
    if (!(button instanceof HTMLButtonElement)) {
      return;
    }
    button.addEventListener("click", () => {
      if (modal instanceof HTMLDialogElement) {
        modal.showModal();
      }
    });
  });
  document.querySelectorAll("[data-vcf-depot-credentials-modal-cancel]").forEach((button) => {
    if (!(button instanceof HTMLButtonElement)) {
      return;
    }
    button.addEventListener("click", () => {
      if (modal instanceof HTMLDialogElement) {
        modal.close("cancel");
      }
    });
  });
  document.querySelectorAll("[data-vcf-depot-credentials-paste]").forEach((form) => {
    if (!(form instanceof HTMLFormElement)) {
      return;
    }
    const button = form.querySelector("button[type='submit']");
    const textarea = form.querySelector('textarea[name="credential_text"]');
    const fileInput = form.querySelector('input[name="credential_file"]');
    const status = form.querySelector("[data-vcf-depot-credentials-paste-status]");
    const typeSelect = form.querySelector('select[name="credential_type"]');
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const hasPastedCredential = textarea instanceof HTMLTextAreaElement && Boolean(textarea.value.trim());
      const hasCredentialFile = fileInput instanceof HTMLInputElement && Boolean(fileInput.files?.length);
      if (!hasPastedCredential && !hasCredentialFile) {
        if (status instanceof HTMLElement) {
          status.textContent = "Choose a credential file or paste credential text.";
          status.classList.add("error-text");
        }
        return;
      }
      if (button instanceof HTMLButtonElement) {
        button.disabled = true;
      }
      if (status instanceof HTMLElement) {
        status.textContent = "Staging Broadcom credential...";
        status.classList.remove("error-text");
      }
      try {
        const response = await fetch(form.action, {
          method: "POST",
          body: new FormData(form),
          credentials: "same-origin",
          headers: { "X-LabFoundry-Autosave": "1" },
        });
        const payload = await response.json();
        if (!response.ok) {
          throw new Error(payload.detail || "Broadcom credential could not be staged.");
        }
        const settingsForm = document.querySelector("[data-vcf-depot-settings]");
        if (settingsForm instanceof HTMLFormElement) {
          updateVcfDepotSummary(settingsForm, payload);
        }
        updateVcfDepotCredentialStatus(payload);
        updateVcfDepotValidation(payload);
        if (textarea instanceof HTMLTextAreaElement) {
          textarea.value = "";
        }
        if (fileInput instanceof HTMLInputElement) {
          fileInput.value = "";
        }
        form.querySelectorAll("[data-file-upload-name]").forEach((label) => {
          if (label instanceof HTMLElement) {
            label.textContent = "no file selected";
          }
        });
        if (status instanceof HTMLElement) {
          const credentialLabel = typeSelect instanceof HTMLSelectElement && typeSelect.value === "activation_code" ? "Activation code" : "Download token";
          status.textContent = `${credentialLabel} staged. Contents are hidden.`;
          status.classList.remove("error-text");
        }
        if (modal instanceof HTMLDialogElement && modal.open) {
          modal.close("saved");
        }
      } catch (error) {
        if (status instanceof HTMLElement) {
          status.textContent = error instanceof Error ? error.message : "Broadcom credential could not be staged.";
          status.classList.add("error-text");
        }
      } finally {
        if (button instanceof HTMLButtonElement) {
          button.disabled = false;
        }
      }
    });
  });
}

function initializeVcfDepotPropertiesEditor() {
  const modal = document.getElementById("vcf-depot-properties-modal");
  document.querySelectorAll("[data-vcf-depot-properties-modal-open]").forEach((button) => {
    if (!(button instanceof HTMLButtonElement)) {
      return;
    }
    button.addEventListener("click", () => {
      if (modal instanceof HTMLDialogElement) {
        modal.showModal();
        const textarea = modal.querySelector("[data-vcf-depot-properties-textarea]");
        if (window.LabFoundryCodeMirror && typeof window.LabFoundryCodeMirror.focus === "function" && textarea instanceof HTMLTextAreaElement) {
          window.LabFoundryCodeMirror.focus(textarea);
        }
      }
    });
  });
  document.querySelectorAll("[data-vcf-depot-properties-modal-cancel]").forEach((button) => {
    if (!(button instanceof HTMLButtonElement)) {
      return;
    }
    button.addEventListener("click", () => {
      if (modal instanceof HTMLDialogElement) {
        modal.close("cancel");
      }
    });
  });
  document.querySelectorAll("[data-vcf-depot-properties-editor]").forEach((form) => {
    if (!(form instanceof HTMLFormElement)) {
      return;
    }
    const button = form.querySelector("button[type='submit']");
    const textarea = form.querySelector("[data-vcf-depot-properties-textarea]");
    const status = form.querySelector("[data-vcf-depot-properties-save-status]");
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      if (textarea instanceof HTMLTextAreaElement && textarea.labFoundryCodeMirrorView?.state?.doc) {
        textarea.value = textarea.labFoundryCodeMirrorView.state.doc.toString();
      }
      if (!(textarea instanceof HTMLTextAreaElement) || !textarea.value.trim()) {
        if (status instanceof HTMLElement) {
          status.textContent = "application-prodv2.properties cannot be empty.";
          status.classList.add("error-text");
        }
        return;
      }
      if (button instanceof HTMLButtonElement) {
        button.disabled = true;
      }
      if (status instanceof HTMLElement) {
        status.textContent = "Saving application properties...";
        status.classList.remove("error-text");
      }
      try {
        const response = await fetch(form.action, {
          method: "POST",
          body: new FormData(form),
          credentials: "same-origin",
          headers: { "X-LabFoundry-Autosave": "1" },
        });
        const payload = await response.json();
        if (!response.ok) {
          throw new Error(payload.detail || "Application properties could not be saved.");
        }
        const settingsForm = document.querySelector("[data-vcf-depot-settings]");
        if (settingsForm instanceof HTMLFormElement) {
          updateVcfDepotSummary(settingsForm, payload);
        }
        updateVcfDepotValidation(payload);
        if (status instanceof HTMLElement) {
          status.textContent = "Application properties saved as desired state.";
          status.classList.remove("error-text");
        }
        if (modal instanceof HTMLDialogElement && modal.open) {
          modal.close("saved");
        }
      } catch (error) {
        if (status instanceof HTMLElement) {
          status.textContent = error instanceof Error ? error.message : "Application properties could not be saved.";
          status.classList.add("error-text");
        }
      } finally {
        if (button instanceof HTMLButtonElement) {
          button.disabled = false;
        }
      }
    });
  });
}

function initializeFileUploadControls() {
  document.querySelectorAll("[data-file-upload-input]").forEach((input) => {
    if (!(input instanceof HTMLInputElement)) {
      return;
    }
    const control = input.closest(".file-upload-control");
    const fileName = control?.querySelector("[data-file-upload-name]");
    input.addEventListener("change", () => {
      if (fileName instanceof HTMLElement) {
        fileName.textContent = input.files?.[0]?.name || "PEM, CRT, or CER file";
      }
    });
  });
}

function initializeEsxiIsoUploadForms() {
  document.querySelectorAll("[data-esxi-iso-upload]").forEach((form) => {
    if (!(form instanceof HTMLFormElement) || form.dataset.esxiIsoUploadInitialized === "1") {
      return;
    }
    form.dataset.esxiIsoUploadInitialized = "1";
    const fileInput = form.querySelector('input[type="file"][name="iso_file"]');
    const button = form.querySelector("[data-esxi-iso-upload-button]");
    const progress = form.querySelector("[data-esxi-iso-upload-progress]");
    const status = form.querySelector("[data-esxi-iso-upload-status]");
    const setStatus = (message, state = "idle") => {
      if (status instanceof HTMLElement) {
        status.textContent = message;
        status.dataset.state = state;
      }
    };
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      if (!(fileInput instanceof HTMLInputElement) || !fileInput.files || fileInput.files.length < 1) {
        setStatus("Choose an ESXi installer ISO before uploading.", "error");
        return;
      }
      const file = fileInput.files[0];
      if (!file.name.toLowerCase().endsWith(".iso")) {
        setStatus("Choose a .iso installer file.", "error");
        return;
      }
      const xhr = new XMLHttpRequest();
      xhr.open("POST", form.action);
      xhr.setRequestHeader("X-LabFoundry-Upload", "1");
      xhr.upload.addEventListener("loadstart", () => {
        if (progress instanceof HTMLProgressElement) {
          progress.hidden = false;
          progress.value = 0;
        }
        if (button instanceof HTMLButtonElement) {
          button.disabled = true;
        }
        setStatus(`Uploading ${file.name}...`, "saving");
      });
      xhr.upload.addEventListener("progress", (progressEvent) => {
        if (progress instanceof HTMLProgressElement && progressEvent.lengthComputable) {
          const percent = Math.max(0, Math.min(100, Math.round((progressEvent.loaded / progressEvent.total) * 100)));
          progress.value = percent;
          setStatus(`Uploading ${file.name}: ${percent}%`, "saving");
        }
      });
      xhr.addEventListener("load", () => {
        let payload = {};
        try {
          payload = xhr.responseText ? JSON.parse(xhr.responseText) : {};
        } catch (_error) {
          payload = {};
        }
        if (xhr.status >= 200 && xhr.status < 300) {
          const uploadedName = payload.name || file.name;
          setStatus(`${uploadedName} uploaded. Refreshing ISO choices...`, "saved");
          rememberActiveTab("labfoundry:esxi-pxe:active-tab", "esxi-pxe-isos-panel");
          if (window.location.pathname === "/esxi-pxe") {
            window.location.hash = "esxi-pxe-isos-panel";
            window.location.reload();
          } else {
            window.location.href = "/esxi-pxe#esxi-pxe-isos-panel";
          }
          return;
        }
        const message =
          payload.detail ||
          (xhr.status === 413
            ? "Upload is too large. ESXi installer ISO uploads are limited to 1 GB."
            : `Upload failed with HTTP ${xhr.status}.`);
        setStatus(message, "error");
      });
      xhr.addEventListener("error", () => {
        setStatus("Upload failed before LabFoundry received the file. Check appliance connectivity and upload size.", "error");
      });
      xhr.addEventListener("abort", () => {
        setStatus("Upload canceled.", "error");
      });
      xhr.addEventListener("loadend", () => {
        if (button instanceof HTMLButtonElement) {
          button.disabled = false;
        }
      });
      xhr.send(new FormData(form));
    });
  });
}

function initializeTagEditors() {
  document.querySelectorAll("[data-tag-editor]").forEach((editor) => {
    if (editor instanceof HTMLElement && editor.dataset.tagEditorInitialized === "1") {
      return;
    }
    const input = editor.querySelector("[data-tag-entry]");
    const list = editor.querySelector("[data-tag-list]");
    const toggle = editor.querySelector("[data-tag-menu-toggle]");
    const menu = editor.querySelector("[data-tag-menu]");
    const name = editor.dataset.tagName || "";
    const singleValue = editor.hasAttribute("data-tag-single");
    if (!(editor instanceof HTMLElement) || !(input instanceof HTMLInputElement) || !(list instanceof HTMLElement) || !name) {
      return;
    }
    editor.dataset.tagEditorInitialized = "1";

    const currentValues = () =>
      Array.from(list.querySelectorAll(".tag-token")).map((item) => item.getAttribute("data-value") || "");

    const notifyChanged = () => {
      editor.dispatchEvent(new CustomEvent("tag-editor:change", { bubbles: true }));
    };

    const removeToken = (token) => {
      token.remove();
      refreshMenu();
      input.focus();
      notifyChanged();
    };

    const refreshMenu = () => {
      if (!(menu instanceof HTMLElement)) {
        return;
      }
      const selected = currentValues().map((item) => item.toLowerCase());
      let visibleOptions = 0;
      menu.querySelectorAll("[data-tag-option]").forEach((option) => {
        if (!(option instanceof HTMLElement)) {
          return;
        }
        const value = option.getAttribute("data-tag-option") || "";
        const isHidden = selected.includes(value.toLowerCase());
        option.classList.toggle("hidden", isHidden);
        if (!isHidden) {
          visibleOptions += 1;
        }
      });
      const emptyMessage = menu.dataset.tagEmptyMessage || "No options available.";
      let emptyState = menu.querySelector("[data-tag-empty]");
      if (!(emptyState instanceof HTMLElement)) {
        emptyState = document.createElement("div");
        emptyState.className = "tag-empty-option";
        emptyState.setAttribute("data-tag-empty", "");
        emptyState.setAttribute("role", "note");
        menu.append(emptyState);
      }
      emptyState.textContent = emptyMessage;
      emptyState.classList.toggle("hidden", visibleOptions > 0);
    };

    const displayLabelForValue = (value) => {
      if (!(menu instanceof HTMLElement)) {
        return value;
      }
      const escapedValue = typeof CSS !== "undefined" && CSS.escape ? CSS.escape(value) : value.replace(/"/g, '\\"');
      const option = menu.querySelector(`[data-tag-option="${escapedValue}"]`);
      if (option instanceof HTMLElement) {
        return option.getAttribute("data-tag-label") || option.textContent.trim() || value;
      }
      return value;
    };

    const addValue = (rawValue) => {
      const value = String(rawValue || "").trim().replace(/,$/, "");
      if (!value || currentValues().some((item) => item.toLowerCase() === value.toLowerCase())) {
        return;
      }
      if (singleValue) {
        list.querySelectorAll(".tag-token").forEach((token) => token.remove());
      }

      const token = document.createElement("span");
      token.className = "tag-token";
      token.setAttribute("data-value", value);

      const label = document.createElement("span");
      label.textContent = displayLabelForValue(value);

      const remove = document.createElement("button");
      remove.type = "button";
      remove.setAttribute("data-tag-remove", "");
      remove.setAttribute("aria-label", `Remove ${value}`);
      remove.textContent = "×";
      remove.addEventListener("click", () => removeToken(token));

      const hidden = document.createElement("input");
      hidden.type = "hidden";
      hidden.name = name;
      hidden.value = value;

      token.append(label, remove, hidden);
      list.append(token);
      refreshMenu();
      notifyChanged();
    };

    const addInputValues = () => {
      input.value
        .split(/[\n,]+/)
        .map((value) => value.trim())
        .filter(Boolean)
        .forEach(addValue);
      input.value = "";
    };

    list.querySelectorAll("[data-tag-remove]").forEach((button) => {
      button.addEventListener("click", () => {
        const token = button.closest(".tag-token");
        if (token instanceof HTMLElement) {
          removeToken(token);
        }
      });
    });

    input.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === "," || event.key === "Tab") {
        if (input.value.trim()) {
          event.preventDefault();
          addInputValues();
        }
      } else if (event.key === "Backspace" && !input.value) {
        const lastToken = list.querySelector(".tag-token:last-child");
        if (lastToken instanceof HTMLElement) {
          removeToken(lastToken);
        }
      }
    });

    input.addEventListener("paste", () => {
      window.setTimeout(addInputValues, 0);
    });

    input.addEventListener("blur", addInputValues);
    if (toggle instanceof HTMLButtonElement && menu instanceof HTMLElement) {
      toggle.addEventListener("click", (event) => {
        event.preventDefault();
        event.stopPropagation();
        refreshMenu();
        menu.toggleAttribute("hidden");
      });
      menu.querySelectorAll("[data-tag-option]").forEach((option) => {
        option.addEventListener("click", (event) => {
          event.preventDefault();
          event.stopPropagation();
          addValue(option.getAttribute("data-tag-option") || "");
          menu.setAttribute("hidden", "");
        });
      });
    }
    editor.addEventListener("click", (event) => {
      if (!(event.target instanceof HTMLElement) || !event.target.closest("[data-tag-menu]")) {
        input.focus();
      }
    });
    refreshMenu();
  });
}

function tagEditorValues(editor) {
  if (!(editor instanceof HTMLElement)) {
    return [];
  }
  return Array.from(editor.querySelectorAll(".tag-token")).map((token) => token.getAttribute("data-value") || "").filter(Boolean);
}

function setTagEditorSingleValue(editor, value) {
  if (!(editor instanceof HTMLElement)) {
    return;
  }
  const list = editor.querySelector("[data-tag-list]");
  const name = editor.dataset.tagName || "";
  if (!(list instanceof HTMLElement) || !name) {
    return;
  }
  list.replaceChildren();
  const trimmedValue = String(value || "").trim();
  if (!trimmedValue) {
    return;
  }
  const token = document.createElement("span");
  token.className = "tag-token";
  token.setAttribute("data-value", trimmedValue);

  const label = document.createElement("span");
  label.textContent = trimmedValue;

  const remove = document.createElement("button");
  remove.type = "button";
  remove.setAttribute("data-tag-remove", "");
  remove.setAttribute("aria-label", `Remove ${trimmedValue}`);
  remove.textContent = "×";
  remove.addEventListener("click", () => {
    token.remove();
    editor.dispatchEvent(new CustomEvent("tag-editor:change", { bubbles: true }));
  });

  const hidden = document.createElement("input");
  hidden.type = "hidden";
  hidden.name = name;
  hidden.value = trimmedValue;
  token.append(label, remove, hidden);
  list.append(token);
}

function addTagEditorValue(editor, value) {
  if (!(editor instanceof HTMLElement)) {
    return;
  }
  const list = editor.querySelector("[data-tag-list]");
  const name = editor.dataset.tagName || "";
  if (!(list instanceof HTMLElement) || !name) {
    return;
  }
  const trimmedValue = String(value || "").trim();
  if (!trimmedValue || tagEditorValues(editor).some((item) => item.toLowerCase() === trimmedValue.toLowerCase())) {
    return;
  }
  const token = document.createElement("span");
  token.className = "tag-token";
  token.setAttribute("data-value", trimmedValue);

  const label = document.createElement("span");
  label.textContent = trimmedValue;

  const remove = document.createElement("button");
  remove.type = "button";
  remove.setAttribute("data-tag-remove", "");
  remove.setAttribute("aria-label", `Remove ${trimmedValue}`);
  remove.textContent = "×";
  remove.addEventListener("click", () => {
    token.remove();
    editor.dispatchEvent(new CustomEvent("tag-editor:change", { bubbles: true }));
  });

  const hidden = document.createElement("input");
  hidden.type = "hidden";
  hidden.name = name;
  hidden.value = trimmedValue;
  token.append(label, remove, hidden);
  list.append(token);
}

function initializeServiceBindEditors() {
  document.querySelectorAll("[data-service-bind]").forEach((container) => {
    if (!(container instanceof HTMLElement)) {
      return;
    }
    const interfaceEditor = container.querySelector(".tag-editor[data-service-bind-interface]");
    const addressEditor = container.querySelector(".tag-editor[data-service-bind-address]");
    if (!(interfaceEditor instanceof HTMLElement) || !(addressEditor instanceof HTMLElement)) {
      return;
    }
    let syncing = false;
    const interfaceOptions = () =>
      Array.from(interfaceEditor.querySelectorAll("[data-tag-option]")).map((option) => ({
        name: option.getAttribute("data-tag-option") || "",
        address: option.getAttribute("data-service-bind-address") || "",
      }));
    const addressOptions = () =>
      Array.from(addressEditor.querySelectorAll("[data-tag-option]")).map((option) => ({
        address: option.getAttribute("data-tag-option") || "",
        name: option.getAttribute("data-service-bind-interface") || "",
      }));
    const syncFromInterface = () => {
      if (syncing) {
        return;
      }
      syncing = true;
      tagEditorValues(interfaceEditor).forEach((selectedInterface) => {
        const match = interfaceOptions().find((option) => option.name === selectedInterface);
        if (match?.address) {
          addTagEditorValue(addressEditor, match.address);
        }
      });
      syncing = false;
    };
    const syncFromAddress = () => {
      if (syncing) {
        return;
      }
      syncing = true;
      tagEditorValues(addressEditor).forEach((selectedAddress) => {
        const match = addressOptions().find((option) => option.address === selectedAddress);
        if (match?.name) {
          addTagEditorValue(interfaceEditor, match.name);
        }
      });
      syncing = false;
    };
    interfaceEditor.addEventListener("tag-editor:change", syncFromInterface);
    addressEditor.addEventListener("tag-editor:change", syncFromAddress);
  });
}

function initializeTabs() {
  document.querySelectorAll("[data-tab-target]").forEach((button) => {
    if (!(button instanceof HTMLButtonElement)) {
      return;
    }
    button.addEventListener("click", () => {
      const targetId = button.dataset.tabTarget;
      if (!targetId) {
        return;
      }
      const tabList = button.closest("[role='tablist']");
      const panel = document.getElementById(targetId);
      if (!tabList || !panel) {
        return;
      }
      if (tabList.classList.contains("zone-tabs")) {
        rememberDnsActiveZone(button.dataset.domain || "");
      }
      rememberActiveTab(tabList.dataset.tabStorageKey || "", targetId);
      tabList.querySelectorAll("[data-tab-target]").forEach((item) => {
        item.classList.toggle("active", item === button);
        item.setAttribute("aria-selected", item === button ? "true" : "false");
      });
      const container = panel.parentElement;
      Array.from(container?.children || []).forEach((item) => {
        if (!(item instanceof HTMLElement) || !item.classList.contains("tab-panel")) {
          return;
        }
        item.classList.toggle("active", item === panel);
        if (item === panel) {
          item.removeAttribute("hidden");
        } else {
          item.setAttribute("hidden", "");
        }
      });
      redrawDnsRecordTables(panel);
    });
  });
  const storedDomain = storedDnsActiveZone();
  const storedDomainButton = dnsZoneTabButtonForDomain(storedDomain);
  if (storedDomainButton) {
    storedDomainButton.click();
  }
  const hashTargetId = window.location.hash ? window.location.hash.slice(1) : "";
  const hashTargetPanel = hashTargetId ? document.getElementById(hashTargetId)?.closest(".tab-panel") : null;
  document.querySelectorAll("[data-tab-storage-key]").forEach((tabList) => {
    if (!(tabList instanceof HTMLElement)) {
      return;
    }
    const storageKey = tabList.dataset.tabStorageKey || "";
    let targetId = hashTargetPanel instanceof HTMLElement ? hashTargetPanel.id : hashTargetId;
    try {
      targetId = targetId || window.localStorage.getItem(storageKey) || "";
    } catch {
      targetId = targetId || "";
    }
    if (!targetId) {
      return;
    }
    const button = tabList.querySelector(`[data-tab-target="${CSS.escape(targetId)}"]`);
    if (button instanceof HTMLButtonElement) {
      button.click();
    }
  });
}

function initializeApplianceApplyProgress() {
  const form = document.querySelector("[data-appliance-apply-form]");
  if (!(form instanceof HTMLFormElement)) {
    return;
  }
  const tracker = document.querySelector("[data-apply-submit-tracker]");
  const steps = document.querySelector("[data-apply-submit-steps]");
  const title = document.querySelector("[data-apply-submit-title]");
  const detail = document.querySelector("[data-apply-submit-detail]");
  const submitButtons = Array.from(form.querySelectorAll("[data-apply-submit-button]")).filter((button) => button instanceof HTMLButtonElement);
  if (!(tracker instanceof HTMLElement) || !(steps instanceof HTMLElement)) {
    return;
  }

  const selectedUnits = () =>
    Array.from(form.querySelectorAll("[data-apply-unit-checkbox]:checked")).flatMap((checkbox) => {
      if (!(checkbox instanceof HTMLInputElement) || checkbox.disabled) {
        return [];
      }
      const card = checkbox.closest("[data-apply-unit-card]");
      if (!(card instanceof HTMLElement)) {
        return [];
      }
      return [
        {
          id: card.dataset.applyUnitId || checkbox.value,
          label: card.dataset.applyUnitLabel || checkbox.value,
        },
      ];
    });

  const renderStep = (unit, state, className = "") => {
    const row = document.createElement("div");
    row.className = `apply-step-row ${className}`.trim();

    const name = document.createElement("span");
    name.className = "apply-step-name";
    name.textContent = unit.label;

    const line = document.createElement("span");
    line.className = "apply-step-line";

    const status = document.createElement("span");
    status.className = "apply-step-state";
    status.textContent = state;

    row.append(name, line, status);
    return row;
  };

  form.addEventListener("submit", async (event) => {
    const units = selectedUnits();
    if (!units.length) {
      return;
    }
    event.preventDefault();
    if (title instanceof HTMLElement) {
      title.textContent = "Submitting appliance changes";
    }
    if (detail instanceof HTMLElement) {
      detail.textContent = "The server is validating and applying the selected units. This list stays on the page and refreshes with real per-unit results when the task completes.";
    }
    steps.replaceChildren(...units.map((unit) => renderStep(unit, "Waiting for result", "waiting")));
    tracker.classList.remove("hidden");
    submitButtons.forEach((button) => {
      button.disabled = true;
      button.textContent = "Submitting...";
    });
    try {
      const response = await fetch(form.action || window.location.href, {
        method: (form.method || "POST").toUpperCase(),
        body: new FormData(form),
        credentials: "same-origin",
      });
      const text = await response.text();
      if (!response.ok) {
        throw new Error(text.trim().replace(/<[^>]+>/g, " ").replace(/\s+/g, " ") || "Appliance changes could not be submitted.");
      }
      document.open();
      document.write(text);
      document.close();
    } catch (error) {
      if (title instanceof HTMLElement) {
        title.textContent = "Apply response interrupted";
      }
      if (detail instanceof HTMLElement) {
        detail.textContent =
          error instanceof Error
            ? error.message
            : "The browser did not receive a complete apply response. Reload the page to check the latest appliance apply task.";
      }
      steps.replaceChildren(...units.map((unit) => renderStep(unit, "Check latest task", "waiting")));
      submitButtons.forEach((button) => {
        button.disabled = false;
        button.textContent = "Submit appliance changes";
      });
    }
  });
}

function monitorFinite(value) {
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

function formatMonitorPercent(value) {
  const number = monitorFinite(value);
  return number === null ? "--" : `${number.toFixed(number >= 10 ? 0 : 1)}%`;
}

function formatMonitorBytes(value) {
  const number = monitorFinite(value);
  if (number === null) {
    return "--";
  }
  const units = ["B", "KB", "MB", "GB", "TB"];
  let size = Math.max(0, number);
  let unitIndex = 0;
  while (size >= 1024 && unitIndex < units.length - 1) {
    size /= 1024;
    unitIndex += 1;
  }
  return `${size.toFixed(size >= 10 || unitIndex === 0 ? 0 : 1)} ${units[unitIndex]}`;
}

function formatMonitorRate(value) {
  const number = monitorFinite(value);
  return number === null ? "--" : `${formatMonitorBytes(number)}/s`;
}

function formatMonitorNumber(value) {
  const number = monitorFinite(value);
  return number === null ? "--" : number.toLocaleString();
}

function monitorSetText(root, selector, value) {
  const node = root.querySelector(selector);
  if (node instanceof HTMLElement) {
    node.textContent = value;
  }
}

function formatServerTime(value) {
  if (!value) {
    return "Server --:--:-- UTC";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return "Server --:--:-- UTC";
  }
  const pad = (part) => String(part).padStart(2, "0");
  return `Server ${pad(date.getUTCHours())}:${pad(date.getUTCMinutes())}:${pad(date.getUTCSeconds())} UTC`;
}

function updateServerTime(value) {
  const node = document.querySelector("[data-server-time]");
  if (!(node instanceof HTMLElement)) {
    return;
  }
  node.dataset.serverTimeIso = value || "";
  node.textContent = formatServerTime(value);
}

function initializeServerTime() {
  const node = document.querySelector("[data-server-time]");
  if (!(node instanceof HTMLElement) || node.dataset.serverTimeInitialized === "true") {
    return;
  }
  node.dataset.serverTimeInitialized = "true";

  let serverBaseMs = Date.parse(node.dataset.serverTimeIso || "");
  let clientBaseMs = Date.now();
  if (!Number.isFinite(serverBaseMs)) {
    serverBaseMs = clientBaseMs;
  }

  const render = () => {
    const estimated = new Date(serverBaseMs + (Date.now() - clientBaseMs));
    node.textContent = formatServerTime(estimated.toISOString());
  };

  const sync = async () => {
    try {
      const response = await fetch("/server-time", {
        credentials: "same-origin",
        headers: { Accept: "application/json" },
      });
      if (!response.ok) {
        return;
      }
      const payload = await response.json();
      const nextBaseMs = Date.parse(payload.server_time || payload.iso || "");
      if (Number.isFinite(nextBaseMs)) {
        serverBaseMs = nextBaseMs;
        clientBaseMs = Date.now();
        node.dataset.serverTimeIso = new Date(serverBaseMs).toISOString();
        render();
      }
    } catch (_error) {
      // Keep the local ticking estimate; the next minute sync will retry.
    }
  };

  render();
  window.setInterval(render, 1000);
  window.setInterval(sync, 60000);
}

function monitorSeriesPoints(rows, fields) {
  return (Array.isArray(rows) ? rows : []).map((row) => {
    const point = { time: new Date(row.sampled_at || 0).getTime() };
    fields.forEach((field) => {
      point[field] = monitorFinite(row[field]);
    });
    return point;
  }).filter((point) => Number.isFinite(point.time));
}

function drawMonitorChart(canvas, rows, lines, options = {}) {
  if (!(canvas instanceof HTMLCanvasElement)) {
    return;
  }
  const bounds = canvas.getBoundingClientRect();
  const width = Math.max(320, Math.floor(bounds.width || canvas.clientWidth || 640));
  const height = Math.max(180, Math.floor(bounds.height || canvas.clientHeight || 240));
  const ratio = window.devicePixelRatio || 1;
  canvas.width = Math.floor(width * ratio);
  canvas.height = Math.floor(height * ratio);
  const context = canvas.getContext("2d");
  if (!context) {
    return;
  }
  context.setTransform(ratio, 0, 0, ratio, 0, 0);
  context.clearRect(0, 0, width, height);

  const padding = { top: 18, right: 18, bottom: 30, left: 44 };
  const plotWidth = width - padding.left - padding.right;
  const plotHeight = height - padding.top - padding.bottom;
  const points = monitorSeriesPoints(rows, lines.map((line) => line.field));
  const values = points.flatMap((point) => lines.map((line) => point[line.field])).filter((value) => value !== null);
  if (!points.length || !values.length) {
    context.fillStyle = "#64748b";
    context.font = "13px system-ui, sans-serif";
    context.fillText("Waiting for samples", padding.left, padding.top + 24);
    return;
  }

  const minTime = Math.min(...points.map((point) => point.time));
  const maxTime = Math.max(...points.map((point) => point.time));
  const rawMax = Math.max(...values, options.max || 0);
  const maxValue = options.max ? options.max : Math.max(1, rawMax * 1.12);
  const minValue = options.min || 0;
  const timeSpan = Math.max(1, maxTime - minTime);
  const valueSpan = Math.max(1, maxValue - minValue);
  const xFor = (time) => padding.left + ((time - minTime) / timeSpan) * plotWidth;
  const yFor = (value) => padding.top + plotHeight - ((value - minValue) / valueSpan) * plotHeight;

  context.strokeStyle = "#e2e8f0";
  context.lineWidth = 1;
  context.fillStyle = "#64748b";
  context.font = "11px system-ui, sans-serif";
  for (let index = 0; index <= 4; index += 1) {
    const y = padding.top + (plotHeight * index) / 4;
    context.beginPath();
    context.moveTo(padding.left, y);
    context.lineTo(width - padding.right, y);
    context.stroke();
    const labelValue = maxValue - ((maxValue - minValue) * index) / 4;
    const label = options.formatY ? options.formatY(labelValue) : labelValue.toFixed(0);
    context.fillText(label, 8, y + 4);
  }

  lines.forEach((line) => {
    context.strokeStyle = line.color;
    context.lineWidth = 2;
    context.beginPath();
    let hasPoint = false;
    points.forEach((point) => {
      const value = point[line.field];
      if (value === null) {
        return;
      }
      const x = xFor(point.time);
      const y = yFor(value);
      if (!hasPoint) {
        context.moveTo(x, y);
        hasPoint = true;
      } else {
        context.lineTo(x, y);
      }
    });
    if (hasPoint) {
      context.stroke();
    }
  });

  const start = new Date(minTime).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  const end = new Date(maxTime).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  context.fillStyle = "#64748b";
  context.fillText(start, padding.left, height - 10);
  context.textAlign = "right";
  context.fillText(end, width - padding.right, height - 10);
  context.textAlign = "left";

  let legendX = padding.left;
  lines.forEach((line) => {
    context.fillStyle = line.color;
    context.fillRect(legendX, 8, 10, 3);
    context.fillStyle = "#334155";
    context.fillText(line.label, legendX + 14, 12);
    legendX += context.measureText(line.label).width + 42;
  });
}

function renderMonitorNetworkTable(tbody, rows) {
  if (!(tbody instanceof HTMLElement)) {
    return;
  }
  const networks = Array.isArray(rows) ? rows : [];
  if (!networks.length) {
    tbody.replaceChildren(Object.assign(document.createElement("tr"), { innerHTML: '<td colspan="6" class="muted">No interfaces sampled</td>' }));
    return;
  }
  tbody.replaceChildren(...networks.map((network) => {
    const row = document.createElement("tr");
    const errors = Number(network.rx_errors || 0) + Number(network.tx_errors || 0);
    const drops = Number(network.rx_dropped || 0) + Number(network.tx_dropped || 0);
    [network.name || "--", network.oper_state || "unknown", formatMonitorRate(network.rx_bytes_per_sec), formatMonitorRate(network.tx_bytes_per_sec), formatMonitorNumber(errors), formatMonitorNumber(drops)].forEach((value) => {
      const cell = document.createElement("td");
      cell.textContent = value;
      row.append(cell);
    });
    return row;
  }));
}

function renderMonitorDiskTable(tbody, rows) {
  if (!(tbody instanceof HTMLElement)) {
    return;
  }
  const disks = Array.isArray(rows) ? rows : [];
  if (!disks.length) {
    tbody.replaceChildren(Object.assign(document.createElement("tr"), { innerHTML: '<td colspan="6" class="muted">No disks sampled</td>' }));
    return;
  }
  tbody.replaceChildren(...disks.map((disk) => {
    const row = document.createElement("tr");
    const mountCell = document.createElement("td");
    mountCell.textContent = disk.mount_point || "--";
    const deviceCell = document.createElement("td");
    deviceCell.textContent = disk.device || "--";
    const usedCell = document.createElement("td");
    usedCell.className = "monitor-usage-cell";
    const percent = monitorFinite(disk.used_percent) || 0;
    const label = document.createElement("span");
    label.textContent = formatMonitorPercent(percent);
    const bar = document.createElement("span");
    bar.className = "monitor-usage-bar";
    const fill = document.createElement("span");
    fill.className = `monitor-usage-fill ${percent >= 90 ? "danger" : percent >= 75 ? "warn" : ""}`.trim();
    fill.style.width = `${Math.max(2, Math.min(100, percent))}%`;
    bar.append(fill);
    usedCell.append(label, bar);
    [mountCell, deviceCell, usedCell].forEach((cell) => row.append(cell));
    [formatMonitorBytes(disk.free_bytes), formatMonitorRate(disk.read_bytes_per_sec), formatMonitorRate(disk.write_bytes_per_sec)].forEach((value) => {
      const cell = document.createElement("td");
      cell.textContent = value;
      row.append(cell);
    });
    return row;
  }));
}

function renderMonitorPage(root, payload) {
  const summary = payload.summary || {};
  const cpu = summary.cpu || {};
  const memory = summary.memory || {};
  const network = summary.network || {};
  const disk = summary.disk || {};
  const virt = payload.virtualization || {};
  monitorSetText(root, "[data-monitor-cpu-current]", formatMonitorPercent(cpu.current_percent));
  monitorSetText(root, "[data-monitor-cpu-detail]", `Load ${cpu.load1 ?? "--"} on ${cpu.cpu_count || "--"} vCPU`);
  monitorSetText(root, "[data-monitor-cpu-peak]", `peak ${formatMonitorPercent(cpu.peak_percent)}`);
  monitorSetText(root, "[data-monitor-memory-current]", formatMonitorPercent(memory.current_percent));
  monitorSetText(root, "[data-monitor-memory-detail]", `${formatMonitorBytes(memory.available_bytes)} available`);
  monitorSetText(root, "[data-monitor-memory-peak]", `peak ${formatMonitorPercent(memory.peak_percent)}`);
  monitorSetText(root, "[data-monitor-network-current]", `${formatMonitorRate(network.rx_bytes_per_sec)} down`);
  monitorSetText(root, "[data-monitor-network-detail]", `${formatMonitorRate(network.tx_bytes_per_sec)} up, ${network.interface_count || 0} interfaces`);
  monitorSetText(root, "[data-monitor-disk-current]", formatMonitorPercent(disk.highest_used_percent));
  monitorSetText(root, "[data-monitor-disk-detail]", `${disk.highest_used_mount || "--"} across ${disk.mount_count || 0} mounts`);
  monitorSetText(root, "[data-monitor-virt-detected]", virt.detected || "unknown");
  monitorSetText(root, "[data-monitor-virt-product]", [virt.sys_vendor, virt.product_name].filter(Boolean).join(" / ") || "--");
  monitorSetText(root, "[data-monitor-virt-tools]", virt.vmtools_version || "--");
  monitorSetText(root, "[data-monitor-virt-hostname]", virt.hostname || "--");
  monitorSetText(root, "[data-monitor-sample-count]", `${payload.sample_count || 0} samples`);
  if (payload.enabled === false) {
    monitorSetText(root, "[data-monitor-sample-count]", "disabled");
  }
  updateServerTime(payload.server_time || payload.generated_at);

  drawMonitorChart(root.querySelector('[data-monitor-chart="cpu"]'), payload.cpu, [{ field: "percent", label: "CPU", color: "#2563eb" }], { min: 0, max: 100, formatY: formatMonitorPercent });
  drawMonitorChart(root.querySelector('[data-monitor-chart="memory"]'), payload.memory, [{ field: "used_percent", label: "Memory", color: "#0f766e" }], { min: 0, max: 100, formatY: formatMonitorPercent });
  drawMonitorChart(
    root.querySelector('[data-monitor-chart="network"]'),
    payload.network_totals,
    [
      { field: "rx_bytes_per_sec", label: "RX", color: "#2563eb" },
      { field: "tx_bytes_per_sec", label: "TX", color: "#d97706" },
    ],
    { min: 0, formatY: formatMonitorBytes },
  );
  drawMonitorChart(
    root.querySelector('[data-monitor-chart="disk"]'),
    payload.disk_io,
    [
      { field: "read_bytes_per_sec", label: "Read", color: "#0f766e" },
      { field: "write_bytes_per_sec", label: "Write", color: "#9333ea" },
    ],
    { min: 0, formatY: formatMonitorBytes },
  );
  renderMonitorNetworkTable(root.querySelector("[data-monitor-network-table]"), payload.networks);
  renderMonitorDiskTable(root.querySelector("[data-monitor-disk-table]"), payload.disks);
}

function initializeMonitorPage() {
  const root = document.querySelector("[data-monitor-page]");
  if (!(root instanceof HTMLElement)) {
    return;
  }
  let hours = 6;
  let latestPayload = null;
  let loading = false;
  const status = root.querySelector("[data-monitor-status]");
  const buttons = Array.from(root.querySelectorAll("[data-monitor-range]")).filter((button) => button instanceof HTMLButtonElement);
  const setStatus = (value) => {
    if (status instanceof HTMLElement) {
      status.textContent = value;
    }
  };
  const load = async () => {
    if (loading) {
      return;
    }
    loading = true;
    setStatus("Refreshing metrics");
    try {
      const response = await fetch(`/monitor/data?hours=${hours}`, { credentials: "same-origin" });
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`);
      }
      latestPayload = await response.json();
      renderMonitorPage(root, latestPayload);
      if (latestPayload.enabled === false) {
        setStatus("Monitoring disabled");
        return;
      }
      const sampleTime = latestPayload.last_sample_at ? new Date(latestPayload.last_sample_at) : null;
      setStatus(sampleTime ? `Last sample ${sampleTime.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" })}` : "Waiting for samples");
    } catch (error) {
      setStatus(`Monitor unavailable: ${error.message}`);
    } finally {
      loading = false;
    }
  };
  buttons.forEach((button) => {
    button.addEventListener("click", () => {
      hours = Number(button.dataset.monitorRange || 6);
      buttons.forEach((candidate) => candidate.classList.toggle("active", candidate === button));
      load();
    });
  });
  window.addEventListener("resize", () => {
    if (latestPayload) {
      renderMonitorPage(root, latestPayload);
    }
  });
  load();
  window.setInterval(load, 5000);
}

function initializeHistoryBackButtons() {
  document.querySelectorAll("[data-history-back]").forEach((button) => {
    if (!(button instanceof HTMLButtonElement)) {
      return;
    }
    button.addEventListener("click", () => {
      if (window.history.length > 1) {
        window.history.back();
        return;
      }
      window.location.assign(button.dataset.historyFallback || "/");
    });
  });
}

function initializeVcfTrustForm() {
  const form = document.querySelector("[data-vcf-trust-form]");
  if (!(form instanceof HTMLFormElement)) {
    return;
  }
  const dialog = document.getElementById("vcf-trust-modal");
  const confirmedTls = form.querySelector("[data-vcf-trust-confirmed-tls-fingerprint]");
  const tlsConfirmation = form.querySelector("[data-vcf-trust-tls-confirmation]");
  const tlsCheckbox = form.querySelector("[data-vcf-trust-tls-confirm]");
  const tlsFingerprint = form.querySelector("[data-vcf-trust-tls-fingerprint]");
  const reviewTarget = form.querySelector("[data-vcf-trust-review-target]");
  const reviewPort = form.querySelector("[data-vcf-trust-review-port]");
  const reviewRole = form.querySelector("[data-vcf-trust-review-role]");
  const reviewVersion = form.querySelector("[data-vcf-trust-review-version]");
  const errors = form.querySelector("[data-vcf-trust-form-errors]");
  const submit = form.querySelector("[data-vcf-trust-submit]");
  const next = form.querySelector("[data-vcf-trust-next]");
  const back = form.querySelector("[data-vcf-trust-back]");
  const cancel = form.querySelector("[data-vcf-trust-cancel]");
  const stepPages = [...form.querySelectorAll("[data-vcf-trust-step]")];
  const stepButtons = [...form.querySelectorAll("[data-vcf-trust-step-nav]")];
  const stepKicker = form.querySelector("[data-vcf-trust-step-kicker]");
  const stepTitle = form.querySelector("[data-vcf-trust-step-title]");
  const stepDescription = form.querySelector("[data-vcf-trust-step-description]");
  let currentStep = "target";
  let maxUnlockedStepIndex = 0;
  let inspectedTls = "";
  const steps = [
    { id: "target", title: "Target and root CA", description: "Choose the VCF appliance, confirm snapshot readiness, and review the active LabFoundry root CA." },
    { id: "api", title: "API credentials", description: "Enter the one-time VCF API administrator credentials used to inspect and import the root CA." },
    { id: "review", title: "Review and queue", description: "Confirm the target HTTPS TLS fingerprint, then queue the certificate trust task." },
  ];
  const stepIndex = (step) => Math.max(0, steps.findIndex((item) => item.id === step));
  const stepDefinition = (step) => steps[stepIndex(step)] || steps[0];
  const showError = (message) => {
    if (errors instanceof HTMLElement) {
      errors.textContent = message;
      errors.classList.toggle("hidden", !message);
    }
  };
  const controlsForStep = (step) => {
    const page = form.querySelector(`[data-vcf-trust-step="${CSS.escape(step)}"]`);
    return page ? [...page.querySelectorAll("input, select, textarea")].filter((control) => !control.disabled) : [];
  };
  const validateStep = (step) => {
    const invalid = controlsForStep(step).find((control) => typeof control.checkValidity === "function" && !control.checkValidity());
    if (invalid && typeof invalid.reportValidity === "function") {
      invalid.reportValidity();
      return false;
    }
    return true;
  };
  const showStep = (step, { unlock = false } = {}) => {
    const index = stepIndex(step);
    if (unlock) maxUnlockedStepIndex = Math.max(maxUnlockedStepIndex, index);
    if (index > maxUnlockedStepIndex) return;
    currentStep = step;
    const definition = stepDefinition(step);
    stepPages.forEach((page) => page.classList.toggle("hidden", page.dataset.vcfTrustStep !== step));
    stepButtons.forEach((button) => {
      const buttonIndex = stepIndex(button.dataset.step || "target");
      button.disabled = buttonIndex > maxUnlockedStepIndex;
      button.classList.toggle("active", button.dataset.step === step);
      button.classList.toggle("complete", buttonIndex < index);
    });
    if (stepKicker instanceof HTMLElement) stepKicker.textContent = `Step ${index + 1} of ${steps.length}`;
    if (stepTitle instanceof HTMLElement) stepTitle.textContent = definition.title;
    if (stepDescription instanceof HTMLElement) stepDescription.textContent = definition.description;
    back?.classList.toggle("hidden", index === 0);
    next?.classList.toggle("hidden", index === steps.length - 1);
    submit?.classList.toggle("hidden", index !== steps.length - 1);
    if (submit instanceof HTMLButtonElement) submit.disabled = index !== steps.length - 1;
  };
  const resetConfirmation = () => {
    if (confirmedTls instanceof HTMLInputElement) {
      confirmedTls.value = "";
    }
    inspectedTls = "";
    tlsConfirmation?.classList.add("hidden");
    if (tlsCheckbox instanceof HTMLInputElement) {
      tlsCheckbox.checked = false;
      tlsCheckbox.required = false;
    }
    if (reviewTarget instanceof HTMLElement) reviewTarget.textContent = "Not inspected yet";
    if (reviewPort instanceof HTMLElement) reviewPort.textContent = "443";
    if (reviewRole instanceof HTMLElement) reviewRole.textContent = "Inspect target to verify";
    if (reviewVersion instanceof HTMLElement) reviewVersion.textContent = "Inspect target to verify";
  };
  const applyTargetInspection = (payload) => {
    inspectedTls = payload.tls_fingerprint || payload.fingerprint || "";
    if (reviewTarget instanceof HTMLElement) reviewTarget.textContent = payload.address || form.elements.address.value || "";
    if (reviewPort instanceof HTMLElement) reviewPort.textContent = String(payload.port || "443");
    const pendingTlsConfirmation = inspectedTls && !payload.appliance;
    if (reviewRole instanceof HTMLElement) reviewRole.textContent = payload.appliance?.role || (pendingTlsConfirmation ? "After TLS confirmation" : "Not inspected yet");
    if (reviewVersion instanceof HTMLElement) reviewVersion.textContent = payload.appliance?.version || (pendingTlsConfirmation ? "After TLS confirmation" : "Not inspected yet");
    if (tlsFingerprint instanceof HTMLElement) tlsFingerprint.textContent = inspectedTls;
    tlsConfirmation?.classList.toggle("hidden", !inspectedTls);
    if (tlsCheckbox instanceof HTMLInputElement) {
      tlsCheckbox.checked = false;
      tlsCheckbox.required = Boolean(inspectedTls);
    }
    if (confirmedTls instanceof HTMLInputElement) {
      confirmedTls.value = "";
    }
  };
  const inspectTarget = async () => {
    if (!validateStep("target") || !validateStep("api")) return false;
    showError("");
    if (next instanceof HTMLButtonElement) {
      next.disabled = true;
      next.textContent = "Inspecting…";
    }
    try {
      const response = await fetch("/vcf-helper/trust-root-ca/inspect-target", {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        body: JSON.stringify({
          csrf: form.elements.csrf.value,
          address: form.elements.address.value,
          api_username: form.elements.api_username.value,
          api_password: form.elements.api_password.value,
          confirmed_tls_fingerprint: confirmedTls instanceof HTMLInputElement ? confirmedTls.value : "",
        }),
      });
      const payload = await response.json();
      if (response.status === 409 || payload.status === "tls-confirmation-required") {
        applyTargetInspection(payload);
        showStep("review", { unlock: true });
        return false;
      }
      if (!response.ok) {
        const messages = Array.isArray(payload.errors) ? payload.errors : [payload.detail || payload.error || "Could not inspect the target VCF API."];
        showError(messages.join(" "));
        return false;
      }
      applyTargetInspection(payload);
      maxUnlockedStepIndex = steps.length - 1;
      return true;
    } catch (_error) {
      showError("Could not inspect the target VCF API. Check connectivity and try again.");
      return false;
    } finally {
      if (next instanceof HTMLButtonElement) {
        next.disabled = false;
        next.textContent = "Next";
      }
    }
  };
  cancel?.addEventListener("click", () => {
    form.reset();
    resetConfirmation();
    maxUnlockedStepIndex = 0;
    showStep("target");
    if (dialog instanceof HTMLDialogElement) {
      dialog.close();
    }
  });
  tlsCheckbox?.addEventListener("change", async () => {
    if (!(confirmedTls instanceof HTMLInputElement) || !(tlsCheckbox instanceof HTMLInputElement)) return;
    confirmedTls.value = tlsCheckbox.checked ? inspectedTls : "";
    if (!tlsCheckbox.checked || !inspectedTls) {
      if (reviewRole instanceof HTMLElement) reviewRole.textContent = inspectedTls ? "After TLS confirmation" : "Not inspected yet";
      if (reviewVersion instanceof HTMLElement) reviewVersion.textContent = inspectedTls ? "After TLS confirmation" : "Not inspected yet";
      return;
    }
    if (reviewRole instanceof HTMLElement) reviewRole.textContent = "Inspecting…";
    if (reviewVersion instanceof HTMLElement) reviewVersion.textContent = "Inspecting…";
    const ready = await inspectTarget();
    if (!ready) {
      confirmedTls.value = "";
      tlsCheckbox.checked = false;
      if (reviewRole instanceof HTMLElement) reviewRole.textContent = "After TLS confirmation";
      if (reviewVersion instanceof HTMLElement) reviewVersion.textContent = "After TLS confirmation";
    }
  });
  next?.addEventListener("click", async () => {
    if (currentStep === "target") {
      if (!validateStep("target")) return;
      showStep("api", { unlock: true });
      return;
    }
    if (currentStep === "api") {
      const ready = await inspectTarget();
      if (!ready) return;
      showStep("review", { unlock: true });
      return;
    }
    if (!validateStep(currentStep)) return;
    const index = stepIndex(currentStep);
    showStep(steps[Math.min(index + 1, steps.length - 1)].id, { unlock: true });
  });
  back?.addEventListener("click", () => {
    const index = stepIndex(currentStep);
    showStep(steps[Math.max(index - 1, 0)].id);
  });
  stepButtons.forEach((button) => {
    button.addEventListener("click", () => {
      const targetStep = button.dataset.step || "target";
      if (stepIndex(targetStep) > stepIndex(currentStep) && !validateStep(currentStep)) return;
      showStep(targetStep);
    });
  });
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (currentStep !== "review") {
      next?.click();
      return;
    }
    if (!validateStep(currentStep)) return;
    errors?.classList.add("hidden");
    if (submit instanceof HTMLButtonElement) {
      submit.disabled = true;
      submit.textContent = "Queueing task…";
    }
    try {
      const response = await fetch(form.action, {
        method: "POST",
        body: new FormData(form),
        credentials: "same-origin",
        headers: { "X-LabFoundry-VCF-Trust": "1" },
      });
      const payload = await response.json();
      if (response.status === 409 && payload.status === "tls-confirmation-required") {
        applyTargetInspection(payload);
        showStep("review", { unlock: true });
        if (submit instanceof HTMLButtonElement) {
          submit.disabled = false;
          submit.textContent = "Run trust task";
        }
        return;
      }
      if (!response.ok) {
        const messages = Array.isArray(payload.errors) ? payload.errors : [payload.error || "The VCF trust task could not be queued."];
        if (errors instanceof HTMLElement) {
          errors.textContent = messages.join(" ");
          errors.classList.remove("hidden");
        }
        if (submit instanceof HTMLButtonElement) {
          submit.disabled = false;
          submit.textContent = "Run trust task";
        }
        return;
      }
      if (submit instanceof HTMLButtonElement) {
        submit.textContent = "Task queued";
      }
      window.location.assign(payload.redirect || `/tasks?job_id=${encodeURIComponent(payload.job_id || "")}`);
    } catch (_error) {
      if (errors instanceof HTMLElement) {
        errors.textContent = "The request could not be completed. Check connectivity and try again.";
        errors.classList.remove("hidden");
      }
      if (submit instanceof HTMLButtonElement) {
        submit.disabled = false;
        submit.textContent = "Run trust task";
      }
    }
  });
  if (dialog instanceof HTMLDialogElement && dialog.hasAttribute("data-vcf-trust-auto-open") && !dialog.open) {
    dialog.showModal();
  }
  showStep("target");
}

async function vcfHelperJson(url, method, payload) {
  const response = await fetch(url, {
    method,
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: method === "GET" ? undefined : JSON.stringify(payload),
  });
  let data = {};
  try { data = await response.json(); } catch (_error) { data = {}; }
  if (!response.ok && ![409].includes(response.status)) {
    const detail = typeof data.detail === "string" ? data.detail : "The VCF Helper request failed.";
    throw new Error(detail);
  }
  return { response, data };
}

function fillVcfInventorySelect(select, rows, emptyLabel = "") {
  if (!(select instanceof HTMLSelectElement)) return;
  select.replaceChildren();
  if (emptyLabel) select.append(new Option(emptyLabel, ""));
  (Array.isArray(rows) ? rows : []).forEach((row) => select.append(new Option(row.name || row.id, row.id)));
}

function initializeVcfSddcDeployment() {
  const form = document.querySelector("[data-vcf-sddc-deploy-form]");
  const dialog = document.getElementById("vcf-sddc-deploy-modal");
  if (!(form instanceof HTMLFormElement) || !(dialog instanceof HTMLDialogElement)) return;
  const open = document.querySelector("[data-vcf-sddc-deploy-open]");
  const close = form.querySelector("[data-vcf-sddc-close]");
  const next = form.querySelector("[data-vcf-sddc-next]");
  const submit = form.querySelector("[data-vcf-sddc-submit]");
  const back = form.querySelector("[data-vcf-sddc-back]");
  const errors = form.querySelector("[data-vcf-sddc-errors]");
  const confirmation = form.querySelector("[data-vcf-sddc-confirmation]");
  const stepPages = [...form.querySelectorAll("[data-vcf-sddc-step]")];
  const stepNavButtons = [...form.querySelectorAll("[data-vcf-sddc-step-nav]")];
  const stepKicker = form.querySelector("[data-vcf-sddc-step-kicker]");
  const stepTitle = form.querySelector("[data-vcf-sddc-step-title]");
  const stepDescription = form.querySelector("[data-vcf-sddc-step-description]");
  const destination = form.querySelector("[data-vcf-sddc-destination]");
  const propertyContainer = form.querySelector("[data-vcf-sddc-properties]");
  const networkContainer = form.querySelector("[data-vcf-sddc-networks]");
  const ovaSelect = form.querySelector("[data-vcf-sddc-ova]");
  const vmName = form.querySelector("[data-vcf-sddc-vm-name]");
  const tlsFingerprint = form.querySelector("[data-vcf-sddc-tls-fingerprint]");
  const tlsConfirmation = form.querySelector("[data-vcf-sddc-tls-confirmation]");
  const tlsConfirmationFingerprint = form.querySelector("[data-vcf-sddc-tls-confirmation-fingerprint]");
  const tlsConfirmInput = form.querySelector("[data-vcf-sddc-tls-confirm]");
  const configureDepot = form.querySelector("[data-vcf-sddc-configure-depot]");
  const applyTrust = form.querySelector("[data-vcf-sddc-apply-trust]");
  const powerOn = form.querySelector("[data-vcf-sddc-power-on]");
  const postPowerOptions = [...form.querySelectorAll("[data-vcf-sddc-post-power-option]")];
  const depotPasswordRow = form.querySelector("[data-vcf-sddc-depot-password]");
  const assignmentMode = form.querySelector("[data-vcf-sddc-assignment-mode]");
  const dhcpZoneRow = form.querySelector("[data-vcf-sddc-dhcp-zone-row]");
  const dhcpZoneSelect = form.querySelector("[data-vcf-sddc-dhcp-zone]");
  const autoHostnameRow = form.querySelector("[data-vcf-sddc-auto-hostname-row]");
  const autoHostname = form.querySelector("[data-vcf-sddc-auto-hostname]");
  const autoIpRow = form.querySelector("[data-vcf-sddc-auto-ip-row]");
  const autoIp = form.querySelector("[data-vcf-sddc-auto-ip]");
  const taskPanel = form.querySelector("[data-vcf-sddc-task]");
  let activeJob = "";
  let currentOva = null;
  let pollTimer = 0;
  let ovas = [];
  let dhcpAssignment = { available: false, scopes: [] };
  let autoHostnameTouched = false;
  let pendingTlsAction = null;
  let pendingTlsFingerprint = "";
  let currentStep = "source";
  let maxUnlockedStepIndex = 0;
  const steps = [
    { id: "source", title: "vCenter / ESXi information", description: "Choose the SDDC Manager OVA and the vSphere endpoint used for discovery and import." },
    { id: "resources", title: "Resources and VM name", description: "Select the destination placement, datastore, network mapping, disk mode, and VM name." },
    { id: "address", title: "Address assignment and hostname", description: "Use manual OVF networking values or pre-fill address details from a LabFoundry DHCP zone." },
    { id: "properties", title: "OVF properties", description: "Review and complete the appliance OVF properties before deployment." },
    { id: "followup", title: "Post-deployment options", description: "Choose power-on behavior and optional DNS, trust, and offline-depot follow-up actions." },
  ];
  try { ovas = JSON.parse(form.dataset.ovas || "[]"); } catch (_error) { ovas = []; }
  try { dhcpAssignment = JSON.parse(form.dataset.dhcpAssignment || "{}"); } catch (_error) { dhcpAssignment = { available: false, scopes: [] }; }

  const showError = (message) => {
    if (errors instanceof HTMLElement) { errors.textContent = message; errors.classList.toggle("hidden", !message); }
  };
  const showConfirmation = (message) => {
    if (confirmation instanceof HTMLElement) { confirmation.textContent = message; confirmation.classList.toggle("hidden", !message); }
  };
  const showTaskError = (message) => {
    const taskError = form.querySelector("[data-vcf-sddc-task-error]");
    if (taskError instanceof HTMLElement) { taskError.textContent = message; taskError.classList.toggle("hidden", !message); }
  };
  const showTlsConfirmation = (fingerprint, action) => {
    pendingTlsFingerprint = fingerprint || "";
    pendingTlsAction = action;
    if (tlsConfirmationFingerprint instanceof HTMLElement) tlsConfirmationFingerprint.textContent = pendingTlsFingerprint;
    if (tlsConfirmInput instanceof HTMLInputElement) tlsConfirmInput.checked = false;
    tlsConfirmation?.classList.remove("hidden");
  };
  const hideTlsConfirmation = () => {
    tlsConfirmation?.classList.add("hidden");
    pendingTlsAction = null;
    pendingTlsFingerprint = "";
  };
  const stepIndex = (step) => Math.max(0, steps.findIndex((item) => item.id === step));
  const stepDefinition = (step) => steps[stepIndex(step)] || steps[0];
  const controlsForStep = (step) => {
    const page = form.querySelector(`[data-vcf-sddc-step="${CSS.escape(step)}"]`);
    return page ? [...page.querySelectorAll("input, select, textarea")].filter((control) => !control.disabled) : [];
  };
  const validateStep = (step) => {
    const invalid = controlsForStep(step).find((control) => typeof control.checkValidity === "function" && !control.checkValidity());
    if (invalid && typeof invalid.reportValidity === "function") {
      invalid.reportValidity();
      return false;
    }
    return true;
  };
  const showStep = (step, { unlock = false } = {}) => {
    const nextIndex = stepIndex(step);
    if (unlock) maxUnlockedStepIndex = Math.max(maxUnlockedStepIndex, nextIndex);
    if (nextIndex > maxUnlockedStepIndex) return;
    currentStep = step;
    const definition = stepDefinition(step);
    stepPages.forEach((page) => page.classList.toggle("hidden", page.dataset.vcfSddcStep !== step));
    stepNavButtons.forEach((button) => {
      const index = stepIndex(button.dataset.step || "source");
      button.disabled = index > maxUnlockedStepIndex;
      button.classList.toggle("active", button.dataset.step === step);
      button.classList.toggle("complete", index < nextIndex);
    });
    if (stepKicker instanceof HTMLElement) stepKicker.textContent = `Step ${nextIndex + 1} of ${steps.length}`;
    if (stepTitle instanceof HTMLElement) stepTitle.textContent = definition.title;
    if (stepDescription instanceof HTMLElement) stepDescription.textContent = definition.description;
    back?.classList.toggle("hidden", nextIndex === 0);
    if (next instanceof HTMLButtonElement) {
      next.classList.toggle("hidden", nextIndex === steps.length - 1);
      next.textContent = "Next";
      next.disabled = false;
    }
    if (submit instanceof HTMLButtonElement) {
      submit.classList.toggle("hidden", nextIndex !== steps.length - 1);
      submit.disabled = nextIndex !== steps.length - 1;
    }
  };
  const selectedOva = () => ovas.find((row) => row.path === ovaSelect?.value) || null;
  const propertyControl = (key) => propertyContainer instanceof HTMLElement ? propertyContainer.querySelector(`[data-ovf-key="${CSS.escape(key)}"]`) : null;
  const selectedDhcpScope = () => (dhcpAssignment.scopes || []).find((row) => String(row.id) === String(dhcpZoneSelect?.value)) || null;
  const hostLabelFromVmName = (scope) => {
    const rawName = String(vmName?.value || "").trim();
    const domain = String(scope?.domain_name || "").trim().toLowerCase();
    if (domain && rawName.toLowerCase().endsWith(`.${domain}`)) return rawName.slice(0, -(domain.length + 1));
    return rawName.split(".")[0] || rawName || "sddcm";
  };
  const fqdnForAutoHost = (scope) => {
    const host = String(autoHostname?.value || "").trim().replace(/\.$/, "");
    if (!host) return "";
    if (host.includes(".")) return host;
    return scope?.domain_name ? `${host}.${scope.domain_name}` : host;
  };
  const syncOva = () => {
    currentOva = selectedOva();
    if (vmName instanceof HTMLInputElement && currentOva) vmName.value = currentOva.vm_name || "";
  };
  const setPropertyValue = (key, value) => {
    const control = propertyControl(key);
    if (control instanceof HTMLInputElement || control instanceof HTMLSelectElement || control instanceof HTMLTextAreaElement) {
      control.value = value || "";
      control.dispatchEvent(new Event("change", { bubbles: true }));
    }
  };
  const syncPostPowerOptions = () => {
    const enabled = !(powerOn instanceof HTMLInputElement) || powerOn.checked;
    postPowerOptions.forEach((row) => {
      const control = row.querySelector("input, select, textarea");
      if (!(control instanceof HTMLInputElement || control instanceof HTMLSelectElement || control instanceof HTMLTextAreaElement)) return;
      if (!control.dataset.baseDisabled) control.dataset.baseDisabled = control.disabled ? "true" : "false";
      if (!enabled && control instanceof HTMLInputElement && control.type === "checkbox") control.checked = false;
      control.disabled = !enabled || control.dataset.baseDisabled === "true";
    });
    depotPasswordRow?.classList.toggle("hidden", !enabled || !(configureDepot instanceof HTMLInputElement && configureDepot.checked));
  };
  const applyDhcpAssignment = (options = {}) => {
    const automatic = assignmentMode?.value === "automatic";
    [dhcpZoneRow, autoHostnameRow, autoIpRow].forEach((row) => row?.classList.toggle("hidden", !automatic));
    if (!automatic) return;
    const scope = selectedDhcpScope();
    if (!scope) return;
    if (!autoHostnameTouched || options.refreshHostname) {
      if (autoHostname instanceof HTMLInputElement) autoHostname.value = hostLabelFromVmName(scope);
    }
    if (autoIp instanceof HTMLInputElement && options.refreshIp !== false) autoIp.value = scope.suggested_ipv4 || "";
    setPropertyValue("ip_address_version", "IPv4");
    setPropertyValue("vami.hostname", fqdnForAutoHost(scope));
    setPropertyValue("ip0", autoIp instanceof HTMLInputElement ? autoIp.value : scope.suggested_ipv4 || "");
    setPropertyValue("netmask0", scope.netmask || "");
    setPropertyValue("gateway", scope.gateway || "");
    setPropertyValue("DNS", scope.dns_server || "");
    setPropertyValue("domain", scope.domain_name || "");
    setPropertyValue("searchpath", scope.domain_name || "");
    if (scope.ntp_server) setPropertyValue("guestinfo.ntp", scope.ntp_server);
  };
  const renderProperties = (properties) => {
    if (!(propertyContainer instanceof HTMLElement)) return;
    propertyContainer.replaceChildren();
    (properties || []).forEach((property) => {
      const label = document.createElement("label");
      const heading = document.createElement("span"); heading.className = "field-label";
      const title = document.createElement("span"); title.textContent = property.label || property.key;
      const help = document.createElement("button"); help.type = "button"; help.className = "help-icon"; help.textContent = "i";
      help.dataset.help = [property.description, property.qualifiers].filter(Boolean).join(" ");
      heading.append(title, help); label.append(heading);
      const values = [...String(property.qualifiers || "").matchAll(/"([^"]+)"/g)].map((match) => match[1]);
      let control;
      if (values.length) {
        control = document.createElement("select"); values.forEach((value) => control.append(new Option(value, value)));
      } else {
        control = document.createElement("input"); control.type = property.password ? "password" : "text";
        if (property.password) control.autocomplete = "new-password";
        const minLen = String(property.qualifiers || "").match(/MinLen\((\d+)\)/);
        const maxLen = String(property.qualifiers || "").match(/MaxLen\((\d+)\)/);
        if (minLen) control.minLength = Number(minLen[1]);
        if (maxLen) control.maxLength = Number(maxLen[1]);
      }
      control.dataset.ovfKey = property.key; control.value = property.default || "";
      if (["ROOT_PASSWORD", "LOCAL_USER_PASSWORD", "vami.hostname"].includes(property.key)) control.required = true;
      label.append(control); propertyContainer.append(label);
    });
    const updatePropertyRequirements = () => {
      const version = propertyControl("ip_address_version")?.value || "IPv4";
      const ipv4Required = version.includes("IPv4");
      const ipv6Required = version.includes("IPv6");
      ["ip0", "netmask0", "gateway", "DNS"].forEach((key) => { const control = propertyControl(key); if (control) control.required = ipv4Required; });
      ["ipv6", "ipv6_prefix", "ipv6_gateway"].forEach((key) => { const control = propertyControl(key); if (control) control.required = ipv6Required; });
    };
    propertyContainer.querySelector('[data-ovf-key="ip_address_version"]')?.addEventListener("change", updatePropertyRequirements);
    updatePropertyRequirements();
    applyDhcpAssignment({ refreshIp: true });
  };
  const renderNetworks = (networks, inventoryNetworks) => {
    if (!(networkContainer instanceof HTMLElement)) return;
    networkContainer.replaceChildren();
    (networks || []).forEach((networkName) => {
      const label = document.createElement("label");
      const heading = document.createElement("span"); heading.className = "field-label";
      const title = document.createElement("span"); title.textContent = `${networkName} mapping`;
      const help = document.createElement("button"); help.type = "button"; help.className = "help-icon"; help.textContent = "i"; help.dataset.help = "Maps the source OVA network to a vSphere network.";
      heading.append(title, help); label.append(heading);
      const select = document.createElement("select"); select.dataset.ovaNetwork = networkName; select.required = true;
      fillVcfInventorySelect(select, inventoryNetworks); label.append(select); networkContainer.append(label);
    });
  };
  const parseEndpoint = () => {
    const raw = String(form.elements.address.value || "").trim();
    const endpoint = raw.replace(/^https?:\/\//i, "");
    if (endpoint.startsWith("[") && endpoint.includes("]")) {
      const closing = endpoint.indexOf("]");
      const host = endpoint.slice(1, closing);
      const rest = endpoint.slice(closing + 1);
      const parsedPort = rest.startsWith(":") ? Number(rest.slice(1)) : 443;
      return { address: host, port: Number.isInteger(parsedPort) && parsedPort > 0 ? parsedPort : 443 };
    }
    const slashless = endpoint.split("/")[0];
    const colonParts = slashless.split(":");
    if (colonParts.length === 2 && /^\d+$/.test(colonParts[1])) {
      return { address: colonParts[0], port: Number(colonParts[1]) };
    }
    return { address: slashless, port: 443 };
  };
  const basePayload = () => {
    const endpoint = parseEndpoint();
    return {
      csrf: form.elements.csrf.value,
      ova_path: form.elements.ova_path.value,
      address: endpoint.address,
      port: endpoint.port,
      username: form.elements.username.value.trim(),
      password: form.elements.password.value,
      confirmed_tls_fingerprint: form.elements.confirmed_tls_fingerprint.value,
    };
  };
  const poll = async () => {
    if (!activeJob) return;
    try {
      const { data } = await vcfHelperJson(`/vcf-helper/sddc-manager/tasks/${encodeURIComponent(activeJob)}`, "GET", {});
      form.querySelector("[data-vcf-sddc-task-status]").textContent = data.status;
      form.querySelector("[data-vcf-sddc-state]").textContent = data.result?.state || data.status;
      form.querySelector("[data-vcf-sddc-progress]").textContent = `${data.progress_percent}%`;
      showTaskError(data.error || "");
      if (["pending", "running"].includes(data.status)) pollTimer = window.setTimeout(poll, 2000);
    } catch (error) { showTaskError(error.message); }
  };

  open?.addEventListener("click", () => { syncOva(); dialog.showModal(); });
  close?.addEventListener("click", () => dialog.close());
  ovaSelect?.addEventListener("change", syncOva);
  vmName?.addEventListener("input", () => {
    if (assignmentMode?.value === "automatic" && !autoHostnameTouched) applyDhcpAssignment({ refreshHostname: true, refreshIp: false });
  });
  assignmentMode?.addEventListener("change", () => applyDhcpAssignment({ refreshHostname: true, refreshIp: true }));
  dhcpZoneSelect?.addEventListener("change", () => applyDhcpAssignment({ refreshHostname: true, refreshIp: true }));
  autoHostname?.addEventListener("input", () => { autoHostnameTouched = true; applyDhcpAssignment({ refreshIp: false }); });
  autoIp?.addEventListener("input", () => applyDhcpAssignment({ refreshIp: false }));
  powerOn?.addEventListener("change", syncPostPowerOptions);
  applyTrust?.addEventListener("change", syncPostPowerOptions);
  configureDepot?.addEventListener("change", syncPostPowerOptions);
  syncPostPowerOptions();
  const handleDiscover = async () => {
    if (!validateStep("source")) return;
    showError(""); showConfirmation("");
    if (next instanceof HTMLButtonElement) {
      next.disabled = true;
      next.textContent = "Discovering…";
    }
    try {
      const { response, data } = await vcfHelperJson("/vcf-helper/sddc-manager/inventory", "POST", basePayload());
      if (response.status === 409 && data.status === "tls-confirmation-required") {
        showTlsConfirmation(data.fingerprint || "", handleDiscover);
        return;
      }
      currentOva = data.ova;
      fillVcfInventorySelect(form.elements.resource_pool_id, data.inventory?.resource_pools);
      fillVcfInventorySelect(form.elements.datastore_id, data.inventory?.datastores);
      fillVcfInventorySelect(form.elements.folder_id, data.inventory?.folders, "Default VM folder");
      fillVcfInventorySelect(form.elements.host_id, data.inventory?.hosts, "Automatic placement");
      renderNetworks(data.ova?.networks, data.inventory?.networks);
      renderProperties(data.ova?.properties);
      maxUnlockedStepIndex = steps.length - 1;
      showStep("resources");
    } catch (error) {
      showError(error.message);
    } finally {
      if (next instanceof HTMLButtonElement) {
        next.disabled = false;
        next.textContent = "Next";
      }
    }
  };
  const handleNext = async () => {
    if (currentStep === "source") {
      await handleDiscover();
      return;
    }
    if (!validateStep(currentStep)) return;
    const index = stepIndex(currentStep);
    showStep(steps[Math.min(index + 1, steps.length - 1)].id);
  };
  const handleSubmit = async () => {
    showError(""); showTaskError("");
    const properties = {}; form.querySelectorAll("[data-ovf-key]").forEach((control) => { properties[control.dataset.ovfKey] = control.value; });
    const networkIds = {}; form.querySelectorAll("[data-ova-network]").forEach((control) => { networkIds[control.dataset.ovaNetwork] = control.value; });
    const shouldPowerOn = !(form.elements.power_on instanceof HTMLInputElement) || form.elements.power_on.checked;
    const payload = {
      ...basePayload(), vm_name: form.elements.vm_name.value, properties,
      destination: { resource_pool_id: form.elements.resource_pool_id.value, datastore_id: form.elements.datastore_id.value, folder_id: form.elements.folder_id.value, host_id: form.elements.host_id.value, network_ids: networkIds },
      options: { power_on: shouldPowerOn, add_dns: form.elements.add_dns.checked, apply_trust: shouldPowerOn && form.elements.apply_trust.checked, configure_offline_depot: shouldPowerOn && form.elements.configure_offline_depot.checked, disk_provisioning: form.elements.disk_provisioning.value },
      depot_password: form.elements.depot_password.value,
    };
    submit.disabled = true;
    try {
      const { response, data } = await vcfHelperJson("/vcf-helper/sddc-manager/deploy", "POST", payload);
      if (response.status === 409 && data.status === "tls-confirmation-required") { showTlsConfirmation(data.fingerprint || "", handleSubmit); submit.disabled = false; return; }
      activeJob = data.job_id;
      window.location.assign(`/tasks?job_id=${encodeURIComponent(activeJob)}`);
    } catch (error) { showError(error.message); submit.disabled = false; }
  };
  next?.addEventListener("click", handleNext);
  back?.addEventListener("click", () => {
    const index = stepIndex(currentStep);
    showStep(steps[Math.max(index - 1, 0)].id);
  });
  stepNavButtons.forEach((button) => {
    button.addEventListener("click", () => {
      const targetStep = button.dataset.step || "source";
      if (stepIndex(targetStep) > stepIndex(currentStep) && !validateStep(currentStep)) return;
      showStep(targetStep);
    });
  });
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (currentStep !== "followup") {
      await handleNext();
      return;
    }
    if (!validateStep(currentStep)) return;
    await handleSubmit();
  });
  tlsConfirmInput?.addEventListener("change", async () => {
    if (!(tlsConfirmInput instanceof HTMLInputElement) || !tlsConfirmInput.checked) return;
    if (tlsFingerprint instanceof HTMLInputElement) tlsFingerprint.value = pendingTlsFingerprint;
    const action = pendingTlsAction;
    hideTlsConfirmation();
    if (typeof action === "function") await action();
  });
  syncOva();
  showStep("source");
}

function initializeVcfTargetDepotHelper() {
  const form = document.querySelector("[data-vcf-target-depot-form]");
  const dialog = document.getElementById("vcf-target-depot-modal");
  if (!(form instanceof HTMLFormElement) || !(dialog instanceof HTMLDialogElement)) return;
  const errors = form.querySelector("[data-vcf-target-depot-errors]");
  const tls = form.querySelector("[data-vcf-target-depot-tls-fingerprint]");
  const submit = form.querySelector("[data-vcf-target-depot-submit]");
  const next = form.querySelector("[data-vcf-target-depot-next]");
  const back = form.querySelector("[data-vcf-target-depot-back]");
  const stepPages = [...form.querySelectorAll("[data-vcf-target-depot-step]")];
  const stepButtons = [...form.querySelectorAll("[data-vcf-target-depot-step-nav]")];
  const stepKicker = form.querySelector("[data-vcf-target-depot-step-kicker]");
  const stepTitle = form.querySelector("[data-vcf-target-depot-step-title]");
  const stepDescription = form.querySelector("[data-vcf-target-depot-step-description]");
  const tlsConfirmation = form.querySelector("[data-vcf-target-depot-tls-confirmation]");
  const tlsConfirm = form.querySelector("[data-vcf-target-depot-tls-confirm]");
  const tlsConfirmationFingerprint = form.querySelector("[data-vcf-target-depot-tls-confirmation-fingerprint]");
  const reviewTarget = form.querySelector("[data-vcf-target-depot-review-target]");
  const reviewPort = form.querySelector("[data-vcf-target-depot-review-port]");
  const reviewRole = form.querySelector("[data-vcf-target-depot-review-role]");
  const reviewVersion = form.querySelector("[data-vcf-target-depot-review-version]");
  const reviewTls = form.querySelector("[data-vcf-target-depot-review-tls]");
  const queueTarget = form.querySelector("[data-vcf-target-depot-queue-target]");
  const queueAction = form.querySelector("[data-vcf-target-depot-queue-action]");
  let currentStep = "target";
  let maxUnlockedStepIndex = 0;
  let inspected = null;
  let inspectedTls = "";
  const steps = [
    { id: "target", title: "Target and local depot", description: "Choose the remote VCF appliance and review the local LabFoundry depot endpoint." },
    { id: "api", title: "API credentials", description: "Enter the one-time VCF API administrator credentials." },
    { id: "depot", title: "Depot credentials", description: "Enter the one-time password for the configured LabFoundry depot HTTP user." },
    { id: "review", title: "Review current settings", description: "Confirm TLS and review the sanitized current target depot configuration." },
    { id: "queue", title: "Queue configuration", description: "Start the background task and continue monitoring from Operations → Tasks." },
  ];
  const showError = (message) => { errors.textContent = message; errors.classList.toggle("hidden", !message); };
  const payload = () => ({ csrf: form.elements.csrf.value, address: form.elements.address.value.trim(), api_username: form.elements.api_username.value.trim(), api_password: form.elements.api_password.value, depot_password: form.elements.depot_password.value, confirmed_tls_fingerprint: form.elements.confirmed_tls_fingerprint.value, replace_existing: form.elements.replace_existing.checked });
  const stepIndex = (step) => Math.max(0, steps.findIndex((item) => item.id === step));
  const controlsForStep = (step) => {
    const page = form.querySelector(`[data-vcf-target-depot-step="${CSS.escape(step)}"]`);
    return page ? [...page.querySelectorAll("input, select, textarea")].filter((control) => !control.disabled) : [];
  };
  const validateStep = (step) => {
    const invalid = controlsForStep(step).find((control) => typeof control.checkValidity === "function" && !control.checkValidity());
    if (invalid && typeof invalid.reportValidity === "function") {
      invalid.reportValidity();
      return false;
    }
    return true;
  };
  const showStep = (step, { unlock = false } = {}) => {
    const index = stepIndex(step);
    if (unlock) maxUnlockedStepIndex = Math.max(maxUnlockedStepIndex, index);
    if (index > maxUnlockedStepIndex) return;
    currentStep = step;
    const definition = steps[index] || steps[0];
    stepPages.forEach((page) => page.classList.toggle("hidden", page.dataset.vcfTargetDepotStep !== step));
    stepButtons.forEach((button) => {
      const buttonIndex = stepIndex(button.dataset.step || "target");
      button.disabled = buttonIndex > maxUnlockedStepIndex;
      button.classList.toggle("active", button.dataset.step === step);
      button.classList.toggle("complete", buttonIndex < index);
    });
    if (stepKicker instanceof HTMLElement) stepKicker.textContent = `Step ${index + 1} of ${steps.length}`;
    if (stepTitle instanceof HTMLElement) stepTitle.textContent = definition.title;
    if (stepDescription instanceof HTMLElement) stepDescription.textContent = definition.description;
    back?.classList.toggle("hidden", index === 0);
    next?.classList.toggle("hidden", index === steps.length - 1);
    submit?.classList.toggle("hidden", index !== steps.length - 1);
    if (submit instanceof HTMLButtonElement) submit.disabled = index !== steps.length - 1;
  };
  const reset = () => {
    form.reset();
    if (tls instanceof HTMLInputElement) tls.value = "";
    inspected = null;
    inspectedTls = "";
    maxUnlockedStepIndex = 0;
    tlsConfirmation?.classList.add("hidden");
    if (tlsConfirm instanceof HTMLInputElement) {
      tlsConfirm.checked = false;
      tlsConfirm.required = false;
    }
    showError("");
    showStep("target");
  };
  const renderCurrentDepot = (data) => {
    inspected = data;
    inspectedTls = data.tls_fingerprint || data.fingerprint || "";
    if (tls instanceof HTMLInputElement) tls.value = "";
    if (reviewTarget instanceof HTMLElement) reviewTarget.textContent = data.target?.address || data.address || form.elements.address.value || "";
    if (reviewPort instanceof HTMLElement) reviewPort.textContent = String(data.port || "443");
    if (reviewRole instanceof HTMLElement) reviewRole.textContent = data.target?.appliance?.role || "unknown";
    if (reviewVersion instanceof HTMLElement) reviewVersion.textContent = data.target?.appliance?.version || "unknown";
    if (reviewTls instanceof HTMLElement) reviewTls.textContent = inspectedTls || "not available";
    if (queueTarget instanceof HTMLElement) queueTarget.textContent = reviewTarget?.textContent || "";
    if (queueAction instanceof HTMLElement) queueAction.textContent = data.replacement_required ? "Replace existing depot and sync metadata" : "Configure or verify LabFoundry depot and sync metadata";
    if (tlsConfirmationFingerprint instanceof HTMLElement) tlsConfirmationFingerprint.textContent = inspectedTls;
    tlsConfirmation?.classList.toggle("hidden", !inspectedTls);
    if (tlsConfirm instanceof HTMLInputElement) {
      tlsConfirm.checked = false;
      tlsConfirm.required = Boolean(inspectedTls);
    }
    const current = data.target?.depot || {};
    const values = form.querySelector("[data-vcf-target-depot-current-values]");
    if (values instanceof HTMLElement) {
      values.replaceChildren();
      [["Hostname", current.hostname || "not configured"], ["Port", current.port || ""], ["URL", current.url || ""], ["User", current.username || ""], ["Status", current.status || ""]].forEach(([label, value]) => {
        const row = document.createElement("div");
        const key = document.createElement("span"); key.textContent = label;
        const strong = document.createElement("strong"); strong.textContent = value;
        row.append(key, strong); values.append(row);
      });
    }
    form.querySelector("[data-vcf-target-depot-current]")?.classList.remove("hidden");
    const replaceRow = form.querySelector("[data-vcf-target-depot-replace-row]");
    replaceRow?.classList.toggle("hidden", !data.replacement_required);
    if (form.elements.replace_existing instanceof HTMLInputElement) {
      form.elements.replace_existing.required = Boolean(data.replacement_required);
      form.elements.replace_existing.checked = false;
    }
  };
  const inspect = async () => {
    showError("");
    if (next instanceof HTMLButtonElement) {
      next.disabled = true;
      next.textContent = "Inspecting…";
    }
    try {
      const { response, data } = await vcfHelperJson("/vcf-helper/offline-depot/inspect-target", "POST", payload());
      if (response.status === 409 && data.status === "tls-confirmation-required") {
        renderCurrentDepot(data);
        showStep("review", { unlock: true });
        return false;
      }
      renderCurrentDepot(data);
      return true;
    } catch (error) {
      showError(error.message);
      return false;
    } finally {
      if (next instanceof HTMLButtonElement) {
        next.disabled = false;
        next.textContent = "Next";
      }
    }
  };
  document.querySelector("[data-vcf-target-depot-open]")?.addEventListener("click", () => dialog.showModal());
  form.querySelector("[data-vcf-target-depot-close]")?.addEventListener("click", () => { reset(); dialog.close(); });
  tlsConfirm?.addEventListener("change", () => {
    if (!(tlsConfirm instanceof HTMLInputElement) || !(tls instanceof HTMLInputElement)) return;
    tls.value = tlsConfirm.checked ? inspectedTls : "";
  });
  next?.addEventListener("click", async () => {
    if (!validateStep(currentStep)) return;
    if (currentStep === "depot") {
      const ready = await inspect();
      if (!ready) return;
      showStep("review", { unlock: true });
      return;
    }
    if (currentStep === "review") {
      if (!inspected && !(tls instanceof HTMLInputElement && tls.value)) {
        const ready = await inspect();
        if (!ready) return;
      }
      showStep("queue", { unlock: true });
      return;
    }
    const index = stepIndex(currentStep);
    showStep(steps[Math.min(index + 1, steps.length - 1)].id, { unlock: true });
  });
  back?.addEventListener("click", () => {
    const index = stepIndex(currentStep);
    showStep(steps[Math.max(index - 1, 0)].id);
  });
  stepButtons.forEach((button) => {
    button.addEventListener("click", () => {
      const targetStep = button.dataset.step || "target";
      if (stepIndex(targetStep) > stepIndex(currentStep) && !validateStep(currentStep)) return;
      showStep(targetStep);
    });
  });
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    showError("");
    if (currentStep !== "queue") {
      next?.click();
      return;
    }
    if (!validateStep("queue")) return;
    if (submit instanceof HTMLButtonElement) {
      submit.disabled = true;
      submit.textContent = "Queueing task…";
    }
    try {
      const { response, data } = await vcfHelperJson("/vcf-helper/offline-depot/configure", "POST", payload());
      if (response.status === 409 && data.status === "replacement-confirmation-required") {
        form.querySelector("[data-vcf-target-depot-replace-row]")?.classList.remove("hidden");
        showStep("review", { unlock: true });
        showError("Confirm replacement of the existing target depot, then queue again.");
        if (submit instanceof HTMLButtonElement) { submit.disabled = false; submit.textContent = "Configure and sync"; }
        return;
      }
      if (response.status === 409 && data.status === "tls-confirmation-required") {
        renderCurrentDepot(data);
        showStep("review", { unlock: true });
        if (submit instanceof HTMLButtonElement) { submit.disabled = false; submit.textContent = "Configure and sync"; }
        return;
      }
      window.location.assign(`/tasks?job_id=${encodeURIComponent(data.job_id || "")}`);
    } catch (error) {
      showError(error.message);
      if (submit instanceof HTMLButtonElement) { submit.disabled = false; submit.textContent = "Configure and sync"; }
    }
  });
  showStep("target");
}

document.addEventListener("DOMContentLoaded", initializeDnsRecordsTable);
document.addEventListener("DOMContentLoaded", initializeDhcpScopesTable);
document.addEventListener("DOMContentLoaded", initializeDhcpOptionsTable);
document.addEventListener("DOMContentLoaded", initializeDhcpReservationsTable);
document.addEventListener("DOMContentLoaded", initializeDhcpLeasesTable);
document.addEventListener("DOMContentLoaded", initializeDhcpLeaseReservationActions);
document.addEventListener("DOMContentLoaded", initializeEsxiPxeHostsTable);
document.addEventListener("DOMContentLoaded", initializeCaProfilesTable);
document.addEventListener("DOMContentLoaded", initializeCaCertificatesTable);
document.addEventListener("DOMContentLoaded", initializeCaSettings);
document.addEventListener("DOMContentLoaded", initializeKmsClientsTable);
document.addEventListener("DOMContentLoaded", initializeKmsKeysTable);
document.addEventListener("DOMContentLoaded", initializeKmsSettings);
document.addEventListener("DOMContentLoaded", initializeChronySettings);
document.addEventListener("DOMContentLoaded", initializeChronyUpstreamsTable);
document.addEventListener("DOMContentLoaded", initializeChronySourceHealthModal);
document.addEventListener("DOMContentLoaded", initializeVcfRegistryBundlesTable);
document.addEventListener("DOMContentLoaded", initializeVcfDepotProfilesTable);
document.addEventListener("DOMContentLoaded", initializeVcfDepotTasksTable);
document.addEventListener("DOMContentLoaded", initializeVcfDepotTaskLogModal);
document.addEventListener("DOMContentLoaded", initializeTasksPage);
document.addEventListener("DOMContentLoaded", initializeServerTime);
document.addEventListener("DOMContentLoaded", initializeFirewallRulesTable);
document.addEventListener("DOMContentLoaded", initializeManagedFirewallRulesTable);
document.addEventListener("DOMContentLoaded", initializeFirewallSourceGroupManager);
document.addEventListener("DOMContentLoaded", initializeServicesTable);
document.addEventListener("DOMContentLoaded", initializeUsersTable);
document.addEventListener("DOMContentLoaded", initializeUserPasswordForm);
document.addEventListener("DOMContentLoaded", initializeRoutesWanRoutesTable);
document.addEventListener("DOMContentLoaded", initializeRoutesWanRoutingTable);
document.addEventListener("DOMContentLoaded", initializeRoutesWanNatTable);
document.addEventListener("DOMContentLoaded", initializeRoutesWanPoliciesTable);
document.addEventListener("DOMContentLoaded", initializePhysicalInterfacesTable);
document.addEventListener("DOMContentLoaded", initializeVlanInterfacesTable);
document.addEventListener("DOMContentLoaded", initializeCodeMirrorEditors);
document.addEventListener("DOMContentLoaded", initializeKickstartEditorDirtyState);
document.addEventListener("DOMContentLoaded", initializeHostsFileEditor);
document.addEventListener("DOMContentLoaded", initializeZoneEditors);
document.addEventListener("DOMContentLoaded", initializeConfirmationModals);
document.addEventListener("DOMContentLoaded", initializePreviewModalControls);
document.addEventListener("DOMContentLoaded", initializeCopyValueButtons);
document.addEventListener("DOMContentLoaded", initializeNonTabbableHelperControls);
document.addEventListener("DOMContentLoaded", initializeSecretToggles);
document.addEventListener("DOMContentLoaded", initializeSwitchFields);
document.addEventListener("DOMContentLoaded", initializeAutosaveForms);
document.addEventListener("DOMContentLoaded", initializeApplianceSettings);
document.addEventListener("DOMContentLoaded", initializeFirewallSettings);
document.addEventListener("DOMContentLoaded", initializeDnsSettings);
document.addEventListener("DOMContentLoaded", initializeVcfFqdnGenerator);
document.addEventListener("DOMContentLoaded", initializeVcfTrustForm);
document.addEventListener("DOMContentLoaded", initializeVcfSddcDeployment);
document.addEventListener("DOMContentLoaded", initializeVcfTargetDepotHelper);
document.addEventListener("DOMContentLoaded", initializeVcfBackupSettings);
document.addEventListener("DOMContentLoaded", initializeVcfRegistrySettings);
document.addEventListener("DOMContentLoaded", initializeVcfDepotSettings);
document.addEventListener("DOMContentLoaded", initializeVcfDepotSoftwareDepotIdGenerator);
document.addEventListener("DOMContentLoaded", initializeVcfDepotToolResetModal);
document.addEventListener("DOMContentLoaded", initializeVcfDepotTokenPaste);
document.addEventListener("DOMContentLoaded", initializeVcfDepotActivationPaste);
document.addEventListener("DOMContentLoaded", initializeVcfDepotCredentialsPaste);
document.addEventListener("DOMContentLoaded", initializeVcfDepotPropertiesEditor);
document.addEventListener("DOMContentLoaded", initializeFileUploadControls);
document.addEventListener("DOMContentLoaded", initializeEsxiIsoUploadForms);
document.addEventListener("DOMContentLoaded", initializeTagEditors);
document.addEventListener("DOMContentLoaded", initializeServiceBindEditors);
document.addEventListener("DOMContentLoaded", initializeTabs);
document.addEventListener("DOMContentLoaded", initializeApplianceApplyProgress);
document.addEventListener("DOMContentLoaded", initializeMonitorPage);
document.addEventListener("DOMContentLoaded", initializeHistoryBackButtons);
document.addEventListener("DOMContentLoaded", initializePublicAddressModeToggle);
document.addEventListener("DOMContentLoaded", () => {
  registerLabFoundryPrismLanguages();
  highlightConfigPreviews();
});

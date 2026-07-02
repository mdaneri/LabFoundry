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
const LABFOUNDRY_MUTATING_METHODS = new Set(["POST", "PUT", "PATCH", "DELETE"]);
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
      await copyTextToClipboard(code?.textContent || "");
    } catch {
      showTransientGridStatus("Copy failed");
    }
  });
  closeButton?.addEventListener("click", () => {
    modal.close();
  });
  modal.addEventListener("click", (event) => {
    if (event.target === modal) {
      modal.close();
    }
  });
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
    copyButton.textContent = "⧉";
    copyButton.setAttribute("aria-label", "Copy preview");
    copyButton.setAttribute("title", "Copy preview");
    const openButton = document.createElement("button");
    openButton.className = "button secondary icon-button";
    openButton.type = "button";
    openButton.textContent = "↗";
    openButton.setAttribute("aria-label", "Open preview");
    openButton.setAttribute("title", "Open preview");
    actions.append(copyButton, openButton);
    note.prepend(actions);
    copyButton.addEventListener("click", async () => {
      try {
        await copyTextToClipboard(code.textContent || "");
      } catch {
        showTransientGridStatus("Copy failed");
      }
    });
    openButton.addEventListener("click", () => openPreviewModal(terminalNoteTitle(note), code.textContent || "", code));
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

function dnsRecordTypeLabel(value) {
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

function copyTextWithTextareaFallback(value) {
  const textarea = document.createElement("textarea");
  textarea.value = value;
  textarea.setAttribute("readonly", "");
  textarea.style.position = "fixed";
  textarea.style.left = "-9999px";
  textarea.style.top = "0";
  document.body.appendChild(textarea);
  textarea.select();
  const copied = document.execCommand("copy");
  textarea.remove();
  if (!copied) {
    throw new Error("Copy command failed.");
  }
}

async function copyTextToClipboard(text, successMessage = "Copied") {
  const value = String(text || "");
  if (!value) {
    return;
  }
  if (window.isSecureContext && navigator.clipboard && typeof navigator.clipboard.writeText === "function") {
    try {
      await navigator.clipboard.writeText(value);
    } catch {
      copyTextWithTextareaFallback(value);
    }
  } else {
    copyTextWithTextareaFallback(value);
  }
  showTransientGridStatus(successMessage);
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
    button.addEventListener("click", async () => {
      try {
        await copyTextToClipboard(button.dataset.copyValue || button.textContent || "");
      } catch {
        showTransientGridStatus("Copy failed");
      }
    });
  });
}

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
    throw new Error(text.match(/DHCP IP zone .* already exists[^<]*/)?.[0] || "The DHCP IP zone could not be saved.");
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

function applyDhcpScopeInterfaceDefaults(rowData, defaults, options = {}) {
  const overwrite = options.overwrite ?? false;
  const interfaceDefaults = dhcpInterfaceDefaults(defaults, rowData.interface_name);
  const gateway = rowData.address_family === "ipv6" ? interfaceDefaults.ipv6_address || interfaceDefaults.address : interfaceDefaults.ipv4_address || interfaceDefaults.address;
  const prefix = rowData.address_family === "ipv6" ? interfaceDefaults.ipv6_prefix : interfaceDefaults.ipv4_prefix;
  if ((overwrite || !rowData.site_address) && gateway) {
    rowData.site_address = gateway;
  }
  if ((overwrite || !rowData.prefix_length) && Number.isInteger(prefix)) {
    rowData.prefix_length = prefix;
  }
  if (overwrite) {
    rowData.dns_server = interfaceDefaults.dns_default || "";
  } else if (!rowData.dns_server && interfaceDefaults.dns_default) {
    rowData.dns_server = interfaceDefaults.dns_default;
  }
  if (overwrite) {
    rowData.ntp_server = interfaceDefaults.ntp_default || "";
  } else if (!rowData.ntp_server && interfaceDefaults.ntp_default) {
    rowData.ntp_server = interfaceDefaults.ntp_default;
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

function newDhcpScopeRow(defaultInterface = "eth2", defaults = {}) {
  const row = {
    id: "__new__",
    name: "",
    address_family: "ipv4",
    interface_name: defaultInterface,
    site_address: "",
    prefix_length: 24,
    range_start: "",
    range_end: "",
    lease_time: "12h",
    domain_name: "labfoundry.internal",
    dns_server: "",
    ntp_server: "",
    enabled: true,
    description: "",
    is_new: true,
  };
  return applyDhcpScopeInterfaceDefaults(row, defaults);
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
      (data.range_start || "").trim() &&
      (data.range_end || "").trim() &&
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
    if (key === "id" || key === "is_new" || key === "is_default" || key.endsWith("_name")) {
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
      columns: [
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
          formatter: "tickCross",
          editor: "tickCross",
          hozAlign: "center",
          width: 80,
          headerSort: false,
          cellEdited: (cell) => autoSaveCaProfile(cell, csrf),
        },
        {
          title: "Enabled",
          field: "enabled",
          formatter: "tickCross",
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
      ],
      rowFormatter: (row) => {
        row.getElement().classList.toggle("new-record-row", Boolean(row.getData().is_new));
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
      columns: [
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
          formatter: "tickCross",
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
      ],
      rowFormatter: (row) => {
        row.getElement().classList.toggle("new-record-row", Boolean(row.getData().is_new));
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
  const tableHeight = `${Math.min(Math.max(rows.length * 42 + 42, 90), 240)}px`;
  try {
    new Tabulator(tableElement, {
      data: rows,
      index: "id",
      layout: "fitColumns",
      height: tableHeight,
      rowHeight: 42,
      placeholder: "No firewall rules configured.",
      reactiveData: false,
      rowContextMenu: [
        {
          label: "Delete rule",
          action: (_event, row) => deleteFirewallRuleFromMenu(row, csrf),
          disabled: (_component) => _component.getData().is_new,
        },
      ],
      columns: [
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
          title: "Family",
          field: "address_family",
          editor: "list",
          editorParams: { values: { ipv4: "IPv4", ipv6: "IPv6" } },
          formatter: (cell) => (cell.getValue() === "ipv6" ? "IPv6" : "IPv4"),
          width: 95,
          cellEdited: (cell) => autoSaveDhcpScope(cell, csrf),
        },
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
          formatter: "tickCross",
          editor: true,
          hozAlign: "center",
          width: 95,
          cellEdited: (cell) => autoSaveFirewallRule(cell, csrf),
        },
        { title: "Description", field: "description", editor: "input", cellEdited: (cell) => autoSaveFirewallRule(cell, csrf) },
      ],
      rowFormatter: (row) => {
        row.getElement().classList.toggle("new-record-row", Boolean(row.getData().is_new));
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
      rowHeight: 42,
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
        { title: "Enabled", field: "enabled", formatter: "tickCross", hozAlign: "center", width: 95 },
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
          title: "Enabled",
          field: "enabled",
          formatter: "tickCross",
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
  const roles = roleValues(JSON.parse(tableElement.dataset.roles || "[]"));
  const shells = JSON.parse(tableElement.dataset.shells || '["/sbin/nologin","/bin/bash","/bin/sh"]');
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
      columns: [
        {
          title: "Username",
          field: "username",
          editor: "input",
          formatter: (cell) => dnsAddRowHintFormatter(cell, "+ Add user here"),
          cellEdited: (cell) => autoSaveUser(cell, csrf),
        },
        {
          title: "Role",
          field: "role",
          editor: "list",
          editorParams: { values: roles },
          cellEdited: (cell) => autoSaveUser(cell, csrf),
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
          formatter: "tickCross",
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
      ],
      rowFormatter: (row) => {
        row.getElement().classList.toggle("new-record-row", Boolean(row.getData().is_new));
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
      columns: [
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
          formatter: "tickCross",
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
      ],
      rowFormatter: (row) => {
        row.getElement().classList.toggle("new-record-row", Boolean(row.getData().is_new));
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
      columns: [
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
          formatter: "tickCross",
          editor: "tickCross",
          hozAlign: "center",
          width: 110,
          headerSort: false,
          cellEdited: (cell) => autoSaveKmsKey(cell, csrf),
        },
        {
          title: "Enabled",
          field: "enabled",
          formatter: "tickCross",
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
      ],
      rowFormatter: (row) => {
        row.getElement().classList.toggle("new-record-row", Boolean(row.getData().is_new));
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
  const terminalNote = validationPanel.querySelector(".terminal-note");
  let errorBox = validationPanel.querySelector("[data-kms-validation-errors]");
  const validMessage = validationPanel.querySelector("[data-kms-validation-message]");
  if (errors.length === 0) {
    errorBox?.remove();
    if (!(validMessage instanceof HTMLElement) && terminalNote instanceof HTMLElement) {
      const message = document.createElement("p");
      message.className = "muted";
      message.setAttribute("data-kms-validation-message", "");
      message.textContent = "The desired KMS state passes LabFoundry validation. Appliance validation still runs through the allowlisted KMS helper before PyKMIP changes are applied.";
      validationPanel.insertBefore(message, terminalNote);
    }
    return;
  }
  validMessage?.remove();
  if (!(errorBox instanceof HTMLElement) && terminalNote instanceof HTMLElement) {
    errorBox = document.createElement("div");
    errorBox.className = "alert error";
    errorBox.setAttribute("data-kms-validation-errors", "");
    validationPanel.insertBefore(errorBox, terminalNote);
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
  const terminalNote = validationPanel.querySelector(".terminal-note");
  let errorBox = validationPanel.querySelector("[data-ntp-validation-errors]");
  const validMessage = validationPanel.querySelector("[data-ntp-validation-message]");
  if (errors.length === 0) {
    errorBox?.remove();
    if (!(validMessage instanceof HTMLElement) && terminalNote instanceof HTMLElement) {
      const message = document.createElement("p");
      message.className = "muted";
      message.setAttribute("data-ntp-validation-message", "");
      message.textContent = "The desired Chrony state passes LabFoundry validation. Appliance validation still runs through the allowlisted Chrony helper before apply.";
      validationPanel.insertBefore(message, terminalNote);
    }
    return;
  }
  validMessage?.remove();
  if (!(errorBox instanceof HTMLElement) && terminalNote instanceof HTMLElement) {
    errorBox = document.createElement("ul");
    errorBox.className = "error-list";
    errorBox.setAttribute("data-ntp-validation-errors", "");
    validationPanel.insertBefore(errorBox, terminalNote);
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

function hasRequiredWanRouteFields(data) {
  return Boolean((data.destination_cidr || "").trim() && (data.interface_name || "").trim());
}

function hasRequiredWanNatFields(data) {
  return Boolean((data.name || "").trim() && (data.outbound_interface || "").trim());
}

function hasRequiredWanPolicyFields(data) {
  return Boolean((data.name || "").trim());
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
      columns: [
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
          formatter: "tickCross",
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
          formatter: "tickCross",
          editor: "tickCross",
          hozAlign: "center",
          width: 100,
          headerSort: false,
          cellEdited: (cell) => autoSaveWanNatRule(cell, csrf),
        },
        { title: "Description", field: "description", editor: "input", minWidth: 180, cellEdited: (cell) => autoSaveWanNatRule(cell, csrf) },
      ],
      rowFormatter: (row) => {
        row.getElement().classList.toggle("new-record-row", Boolean(row.getData().is_new));
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
      columns: [
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
          formatter: "tickCross",
          editor: "tickCross",
          hozAlign: "center",
          width: 100,
          headerSort: false,
          cellEdited: (cell) => autoSaveWanRoute(cell, csrf),
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
      columns: [
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
          formatter: "tickCross",
          editor: "tickCross",
          hozAlign: "center",
          width: 100,
          headerSort: false,
          cellEdited: (cell) => autoSaveWanPolicy(cell, csrf),
        },
        { title: "Description", field: "description", editor: "input", minWidth: 180, cellEdited: (cell) => autoSaveWanPolicy(cell, csrf) },
      ],
      rowFormatter: (row) => {
        row.getElement().classList.toggle("new-record-row", Boolean(row.getData().is_new));
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

function newVlanInterfaceRow(defaultParent = "eth1") {
  return {
    id: "__new__",
    name: "",
    parent_interface: defaultParent,
    vlan_id: "",
    ip_cidr: "",
    ipv6_cidr: "",
    mtu: 1500,
    role: "access",
    enabled: true,
    is_new: true,
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
  }
}

async function autoSavePhysicalInterface(cell, csrf) {
  clearCaMessage("physical-interface-error");
  const row = cell.getRow();
  const data = row.getData();
  data.admin_state = data.admin_up ? "up" : "down";
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
          title: "IPv4 CIDR",
          field: "ip_cidr",
          editor: cidrInputEditor,
          editorParams: { family: "ipv4", placeholder: "192.168.50.1/24" },
          formatter: (cell) => dnsAddRowHintFormatter(cell, "192.168.50.1/24"),
          minWidth: 160,
          cellEdited: (cell) => autoSavePhysicalInterface(cell, csrf),
        },
        {
          title: "IPv6 CIDR",
          field: "ipv6_cidr",
          editor: cidrInputEditor,
          editorParams: { family: "ipv6", placeholder: "fd00:50::1/64" },
          formatter: (cell) => dnsAddRowHintFormatter(cell, "fd00:50::1/64"),
          minWidth: 180,
          cellEdited: (cell) => autoSavePhysicalInterface(cell, csrf),
        },
        {
          title: "Role",
          field: "role",
          editor: "list",
          editorParams: { values: roleOptions },
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
          cellEdited: (cell) => autoSavePhysicalInterface(cell, csrf),
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
  const defaultParent = parentOptions[0]?.name || "";
  const rows = [...JSON.parse(tableElement.dataset.vlans || "[]"), newVlanInterfaceRow(defaultParent)];
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
      columns: [
        {
          title: "Name",
          field: "name",
          formatter: (cell) => {
            const data = cell.getRow().getData();
            if (data.is_new) {
              return '<span class="add-row-hint">+ Add VLAN here</span>';
            }
            return escapeHtml(cell.getValue());
          },
          minWidth: 140,
          headerSort: false,
          cellClick: (event, cell) => editNewRowCell(cell, "vlan_id"),
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
          cellEdited: (cell) => autoSaveVlanInterface(cell, csrf),
        },
        {
          title: "VLAN ID",
          field: "vlan_id",
          editor: "number",
          width: 100,
          cellEdited: (cell) => autoSaveVlanInterface(cell, csrf),
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
      ],
      rowFormatter: (row) => {
        row.getElement().classList.toggle("new-record-row", Boolean(row.getData().is_new));
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
    new Tabulator(tableElement, {
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
          formatter: (cell) => dnsAddRowHintFormatter(cell, "+ Add record here"),
          minWidth: 180,
          cellEdited: (cell) => autoSaveDnsRecord(cell, csrf),
        },
        { title: "Domain", field: "domain", minWidth: 190, headerSort: false },
        {
          title: "Family",
          field: "record_type",
          editor: "list",
          editorParams: { values: { A: "A (IPv4)", AAAA: "AAAA (IPv6)", CNAME: "CNAME (alias)" } },
          formatter: (cell) => dnsRecordTypeLabel(cell.getValue()),
          width: 130,
          headerSort: false,
          cellEdited: (cell) => autoSaveDnsRecord(cell, csrf),
        },
        {
          title: "Value",
          field: "address",
          editor: "input",
          formatter: (cell) => dnsAddRowHintFormatter(cell, "enter value..."),
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
          formatter: "tickCross",
          editor: "tickCross",
          hozAlign: "center",
          width: 110,
          headerSort: false,
          cellEdited: (cell) => autoSaveDnsRecord(cell, csrf),
        },
        {
          title: "Description",
          field: "description",
          editor: "input",
          formatter: (cell) => dnsAddRowHintFormatter(cell, "optional note..."),
          minWidth: 220,
          cellEdited: (cell) => autoSaveDnsRecord(cell, csrf),
        },
      ],
      rowFormatter: (row) => {
        const element = row.getElement();
        element.classList.toggle("new-record-row", Boolean(row.getData().is_new));
      },
    });
    if (fallback) {
      fallback.classList.add("hidden");
    }
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
          title: "Range start",
          field: "range_start",
          editor: "input",
          editable: (cell) => dhcpScopeCellEditable(cell, existingScopeNames),
          formatter: (cell) => dnsAddRowHintFormatter(cell, "start IP..."),
          minWidth: 140,
          cellEdited: handleDhcpScopeEdited,
        },
        {
          title: "Range end",
          field: "range_end",
          editor: "input",
          editable: (cell) => dhcpScopeCellEditable(cell, existingScopeNames),
          formatter: (cell) => dnsAddRowHintFormatter(cell, "end IP..."),
          minWidth: 140,
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
          formatter: "tickCross",
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
          formatter: "tickCross",
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
          cellEdited: (cell) => autoSaveDhcpReservation(cell, csrf),
        },
        {
          title: "MAC address",
          field: "mac_address",
          editor: "input",
          formatter: (cell) => dnsAddRowHintFormatter(cell, "enter MAC..."),
          minWidth: 180,
          cellEdited: (cell) => autoSaveDhcpReservation(cell, csrf),
        },
        {
          title: "IP address",
          field: "ip_address",
          editor: "input",
          formatter: (cell) => dnsAddRowHintFormatter(cell, "enter IP..."),
          minWidth: 150,
          cellEdited: (cell) => autoSaveDhcpReservation(cell, csrf),
        },
        {
          title: "Enabled",
          field: "enabled",
          formatter: "tickCross",
          editor: "tickCross",
          hozAlign: "center",
          width: 110,
          headerSort: false,
          cellEdited: (cell) => autoSaveDhcpReservation(cell, csrf),
        },
        {
          title: "Description",
          field: "description",
          editor: "input",
          formatter: (cell) => dnsAddRowHintFormatter(cell, "optional note..."),
          minWidth: 220,
          cellEdited: (cell) => autoSaveDhcpReservation(cell, csrf),
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
      columns: [
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
          title: "Enabled",
          field: "enabled",
          formatter: "tickCross",
          editor: canWrite ? "tickCross" : false,
          hozAlign: "center",
          width: 100,
          headerSort: false,
          cellEdited: (cell) => autoSaveEsxiHost(cell, csrf),
        },
      ],
      rowFormatter: (row) => {
        const data = row.getData();
        row.getElement().classList.toggle("new-record-row", Boolean(data.is_new));
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
  const terminalNote = validationPanel.querySelector(".terminal-note");
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
      validationPanel.insertBefore(message, terminalNote);
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
      validationPanel.insertBefore(errorList, terminalNote);
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
  items.forEach((message) => {
    const item = document.createElement("li");
    item.textContent = message;
    list.append(item);
  });
  list.classList.toggle("hidden", items.length === 0);
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
    const terminalNote = validationPanel.querySelector(".terminal-note");
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
        validationPanel.insertBefore(message, terminalNote);
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
        validationPanel.insertBefore(errorList, terminalNote);
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
      columns: [
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
          formatter: "tickCross",
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
      ],
      rowFormatter: (row) => {
        row.getElement().classList.toggle("new-record-row", Boolean(row.getData().is_new));
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
    const firstTerminalNote = validationPanel.querySelector(".terminal-note");
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
        validationPanel.insertBefore(message, firstTerminalNote);
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
        validationPanel.insertBefore(errorList, firstTerminalNote);
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
      validationPanel.insertBefore(warningList, firstTerminalNote);
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
    component: "",
    component_version: "",
    disabled_platforms: [],
    enabled: true,
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

function vcfDepotRememberActiveTab() {
  const tabList = document.querySelector("[data-tab-storage-key='labfoundry:vcf-offline-depot:active-tab']");
  if (!(tabList instanceof HTMLElement)) {
    return;
  }
  const activeButton = tabList.querySelector(".tab-button.active[data-tab-target]");
  if (activeButton instanceof HTMLElement) {
    rememberActiveTab(tabList.dataset.tabStorageKey || "", activeButton.dataset.tabTarget || "");
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
  for (const [key, value] of Object.entries(data)) {
    if (["id", "is_new", "created_at", "updated_at"].includes(key)) {
      continue;
    }
    if (["enabled", "automated_install", "upgrades_only", "patches_only"].includes(key)) {
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
  vcfDepotRememberActiveTab();
  window.location.reload();
}

async function autoSaveVcfDepotProfile(cell, csrf) {
  const row = cell.getRow();
  const data = row.getData();
  if (data.is_new && !hasRequiredVcfDepotProfileFields(data)) {
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
    row.update({ status: payload.profile_status || data.status });
    showVcfDepotMessage(`Download job ${payload.job_id} ${payload.dry_run ? "recorded" : "started"} for ${payload.profile_name}.`, "success");
  } catch (error) {
    showVcfDepotMessage(error instanceof Error ? error.message : "The VCFDT download job could not be started.");
  }
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
    new Tabulator(tableElement, {
      data: rows,
      index: "id",
      layout: "fitColumns",
      height: "380px",
      rowHeight: 28,
      placeholder: "No VCFDT download profiles configured.",
      reactiveData: false,
      rowContextMenu: [
        {
          label: "Start download",
          action: (_event, row) => startVcfDepotProfileDownload(row, csrf),
          disabled: (component) => {
            const data = component.getData();
            return data.is_new || !data.enabled;
          },
        },
        {
          label: "Delete profile",
          action: (_event, row) => deleteVcfDepotProfileFromMenu(row, csrf),
        },
      ],
      columns: [
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
            const disabled = data.is_new || !data.enabled ? " disabled" : "";
            return `<button class="button tiny secondary" type="button" data-vcf-depot-start-download${disabled}>Start</button>`;
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
          title: "Automated",
          field: "automated_install",
          formatter: "tickCross",
          editor: "tickCross",
          hozAlign: "center",
          width: 105,
          headerSort: false,
          cellEdited: (cell) => autoSaveVcfDepotProfile(cell, csrf),
        },
        {
          title: "Upgrades only",
          field: "upgrades_only",
          formatter: "tickCross",
          editor: "tickCross",
          hozAlign: "center",
          width: 120,
          headerSort: false,
          cellEdited: (cell) => autoSaveVcfDepotProfile(cell, csrf),
        },
        {
          title: "Patches only",
          field: "patches_only",
          formatter: "tickCross",
          editor: "tickCross",
          hozAlign: "center",
          width: 115,
          headerSort: false,
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
          formatter: (cell) => formatVcfDepotChoiceList(cell, esxPlatformValues, "none"),
          minWidth: 190,
          cellEdited: (cell) => autoSaveVcfDepotProfile(cell, csrf),
        },
        {
          title: "Enabled",
          field: "enabled",
          formatter: "tickCross",
          editor: "tickCross",
          hozAlign: "center",
          width: 95,
          headerSort: false,
          cellEdited: (cell) => autoSaveVcfDepotProfile(cell, csrf),
        },
        {
          title: "Status",
          field: "status",
          editor: "list",
          editorParams: { values: { planned: "planned", ready: "ready", synced: "synced", blocked: "blocked" } },
          width: 110,
          cellEdited: (cell) => autoSaveVcfDepotProfile(cell, csrf),
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
    showVcfDepotMessage(error instanceof Error ? error.message : "Tabulator could not render. Showing the fallback table.");
  }
}

function updateVcfDepotSummary(form, payload = {}) {
  const portInput = form.querySelector('input[name="port"]');
  const hostnameInput = form.querySelector('input[name="hostname"]');
  const { interfaceLabel: bindInterfaceLabel, address, addressLabel, addresses } = serviceBindSelection(form, payload);
  const port = payload.port || portInput?.value || "443";
  const hostname = payload.hostname || hostnameInput?.value || "";
  const endpointValue = payload.endpoint || (port === "443" || port === 443 ? hostname : `${hostname}:${port}`);
  const endpoint = document.querySelector("[data-vcf-depot-endpoint]");
  const interfaceLabel = document.querySelector("[data-vcf-depot-interface]");
  const storePaths = document.querySelectorAll("[data-vcf-depot-store]");
  const toolVersions = document.querySelectorAll("[data-vcf-depot-tool-version]");
  const toolStatuses = document.querySelectorAll("[data-vcf-depot-tool-status]");
  const dnsStatus = document.querySelector("[data-vcf-depot-dns-status]");
  const tokenStatus = document.querySelector("[data-vcf-depot-token-status]");
  const activationStatus = document.querySelector("[data-vcf-depot-activation-status]");
  const softwareDepotGenerateButtons = document.querySelectorAll("[data-vcf-depot-generate-id] button[type='submit']");
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
  if (payload.tool_archive_name !== undefined) {
    toolStatuses.forEach((toolStatus) => {
      if (toolStatus instanceof HTMLElement) {
        toolStatus.textContent = payload.tool_archive_name ? "tool staged" : "upload required";
      }
    });
    softwareDepotGenerateButtons.forEach((softwareDepotGenerateButton) => {
      if (softwareDepotGenerateButton instanceof HTMLButtonElement) {
        softwareDepotGenerateButton.disabled = !payload.tool_archive_name;
      }
    });
    setVcfDepotToolDependentActions(Boolean(payload.tool_archive_name));
  }
  if (payload.tool_version !== undefined) {
    toolVersions.forEach((toolVersion) => {
      if (toolVersion instanceof HTMLElement) {
        toolVersion.textContent = payload.tool_version || "not uploaded";
      }
    });
  }
  if (tokenStatus instanceof HTMLElement && payload.download_token_present !== undefined) {
    tokenStatus.textContent = payload.download_token_present ? payload.download_token_name || "uploaded" : "not uploaded";
  }
  if (activationStatus instanceof HTMLElement && payload.activation_code_present !== undefined) {
    activationStatus.textContent = payload.activation_code_present ? payload.activation_code_name || "uploaded" : "not uploaded";
  }
  updateVcfDepotSoftwareDepotId(payload);
  if (dnsStatus instanceof HTMLElement && payload.dns_record_action !== undefined) {
    const dnsMessages = {
      created: "DNS record created for this endpoint.",
      updated: "DNS record updated for this endpoint.",
      unchanged: "DNS record already matches this endpoint.",
      "created+removed-old": "DNS record created and old endpoint record removed.",
      "updated+removed-old": "DNS record updated and old endpoint record removed.",
      "unchanged+removed-old": "Old endpoint DNS record removed.",
      "removed-old": "Old endpoint DNS record removed.",
    };
    dnsStatus.textContent = dnsMessages[payload.dns_record_action] || "DNS record follows the selected listen address.";
  }
  const livePreviewPayload = {
    ...payload,
    hostname,
    endpoint: endpointValue,
    listen_address: address,
    listen_addresses: addresses,
    port,
    server_certificate: payload.server_certificate || hostname,
  };
  updateVcfDepotHttpsPreview(livePreviewPayload);
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
  if (softwareDepotId instanceof HTMLElement && payload.software_depot_id !== undefined) {
    if (softwareDepotId instanceof HTMLInputElement) {
      softwareDepotId.value = payload.software_depot_id || "";
    } else {
      softwareDepotId.textContent = payload.software_depot_id || "";
    }
    softwareDepotId.classList.toggle("hidden", !payload.software_depot_id);
    const button = softwareDepotCell?.querySelector("[data-vcf-depot-generate-id] button[type='submit']");
    if (button instanceof HTMLButtonElement) {
      if (payload.software_depot_id) {
        button.textContent = "↻";
        button.classList.add("icon-button");
        button.classList.remove("compact-button");
        button.setAttribute("aria-label", "Refresh software depot ID");
        button.setAttribute("title", "Refresh software depot ID");
      } else {
        button.textContent = "Generate software depot ID";
        button.classList.remove("icon-button");
        button.classList.add("compact-button");
        button.removeAttribute("aria-label");
        button.removeAttribute("title");
      }
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
      softwareDepotMessage.textContent = "Upload VCFDT to generate the software depot ID.";
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
  httpsPreview.textContent = [
    "# Managed by LabFoundry. Local changes may be overwritten.",
    "# Dry-run preview of desired HTTPS endpoint for the VCF Offline Depot.",
    `# Depot store: ${depotStorePath}`,
    `# VCF endpoint: https://${endpoint}/`,
    "",
    "server {",
    ...listenLines,
    `  server_name ${hostname};`,
    `  root ${depotStorePath};`,
    "  sendfile on;",
    "  tcp_nopush on;",
    "  directio 8m;",
    "  autoindex on;",
    "  types { }",
    "  default_type application/octet-stream;",
    `  ssl_certificate /etc/labfoundry/vcf-offline-depot/certs/${certificateName}.crt;`,
    `  ssl_certificate_key /etc/labfoundry/vcf-offline-depot/certs/${certificateName}.key;`,
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
    const terminalNote = validationPanel.querySelector(".terminal-note");
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
        validationPanel.insertBefore(message, terminalNote);
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
        validationPanel.insertBefore(errorList, terminalNote);
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
      validationPanel.insertBefore(warningList, terminalNote);
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
    });
    refresh();
  });
}

function initializeVcfDepotSoftwareDepotIdGenerator() {
  document.querySelectorAll("[data-vcf-depot-generate-id]").forEach((form) => {
    if (!(form instanceof HTMLFormElement)) {
      return;
    }
    const button = form.querySelector("button[type='submit']");
    const message = form.querySelector("[data-vcf-depot-software-depot-message]");
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      if (button instanceof HTMLButtonElement) {
        button.disabled = true;
      }
      if (message instanceof HTMLElement) {
        message.textContent = "Generating software depot ID...";
        message.classList.remove("error-text");
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
          throw new Error(payload.software_depot_id_error || "Software depot ID generation failed.");
        }
        updateVcfDepotSoftwareDepotId(payload);
      } catch (error) {
        if (message instanceof HTMLElement) {
          message.textContent = error instanceof Error ? error.message : "Software depot ID generation failed.";
          message.classList.add("error-text");
        }
      } finally {
        if (button instanceof HTMLButtonElement) {
          button.disabled = false;
        }
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
        updateVcfDepotValidation(payload);
        if (textarea instanceof HTMLTextAreaElement) {
          textarea.value = "";
        }
        if (fileInput instanceof HTMLInputElement) {
          fileInput.value = "";
        }
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
      menu.querySelectorAll("[data-tag-option]").forEach((option) => {
        if (!(option instanceof HTMLElement)) {
          return;
        }
        const value = option.getAttribute("data-tag-option") || "";
        option.classList.toggle("hidden", selected.includes(value.toLowerCase()));
      });
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

  form.addEventListener("submit", (event) => {
    const units = selectedUnits();
    if (!units.length) {
      return;
    }
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
  });
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
document.addEventListener("DOMContentLoaded", initializeVcfRegistryBundlesTable);
document.addEventListener("DOMContentLoaded", initializeVcfDepotProfilesTable);
document.addEventListener("DOMContentLoaded", initializeFirewallRulesTable);
document.addEventListener("DOMContentLoaded", initializeManagedFirewallRulesTable);
document.addEventListener("DOMContentLoaded", initializeFirewallSourceGroupManager);
document.addEventListener("DOMContentLoaded", initializeServicesTable);
document.addEventListener("DOMContentLoaded", initializeUsersTable);
document.addEventListener("DOMContentLoaded", initializeUserPasswordForm);
document.addEventListener("DOMContentLoaded", initializeRoutesWanRoutesTable);
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
document.addEventListener("DOMContentLoaded", initializeVcfBackupSettings);
document.addEventListener("DOMContentLoaded", initializeVcfRegistrySettings);
document.addEventListener("DOMContentLoaded", initializeVcfDepotSettings);
document.addEventListener("DOMContentLoaded", initializeVcfDepotSoftwareDepotIdGenerator);
document.addEventListener("DOMContentLoaded", initializeVcfDepotTokenPaste);
document.addEventListener("DOMContentLoaded", initializeFileUploadControls);
document.addEventListener("DOMContentLoaded", initializeEsxiIsoUploadForms);
document.addEventListener("DOMContentLoaded", initializeTagEditors);
document.addEventListener("DOMContentLoaded", initializeServiceBindEditors);
document.addEventListener("DOMContentLoaded", initializeTabs);
document.addEventListener("DOMContentLoaded", initializeApplianceApplyProgress);
document.addEventListener("DOMContentLoaded", () => {
  registerLabFoundryPrismLanguages();
  highlightConfigPreviews();
});

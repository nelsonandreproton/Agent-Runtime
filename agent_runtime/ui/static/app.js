// Toggles a masked secret value (used for MCP server env vars) between
// a fixed mask and its real value, without a round trip to the server.
function toggleMask(button) {
  const target = document.getElementById(button.dataset.target);
  if (!target) return;
  const isMasked = target.dataset.masked !== "false";
  target.textContent = isMasked ? target.dataset.real : "*".repeat(8);
  target.dataset.masked = isMasked ? "false" : "true";
  button.textContent = isMasked ? "Hide" : "Show";
}

// Adds a new empty key/value row to a key-value editor block (env vars,
// tool_aliases) right before the "Add" button that triggered this.
function addKvRow(button, namePrefix) {
  const container = button.parentElement;
  const row = document.createElement("div");
  row.className = "kv-row";
  row.innerHTML =
    `<input type="text" name="${namePrefix}_key" placeholder="key">` +
    `<input type="text" name="${namePrefix}_value" placeholder="value">` +
    `<button type="button" class="btn" onclick="this.parentElement.remove()">Remove</button>`;
  container.insertBefore(row, button);
}

// Reads the confirmation message from a data-* attribute (set via Jinja2's
// normal HTML-attribute escaping) instead of interpolating a name straight
// into an inline event handler's JS string literal. Building the message as
// `onsubmit="return confirmDelete('...{{ name }}...')"` is unsafe even with
// autoescaping on: the browser HTML-decodes the attribute value BEFORE the
// JS parser sees it, so an escaped quote becomes a real one again exactly
// where it could close the string early (e.g. an agent literally named
// "x');alert(1);('" would break out of the string and execute). Reading the
// name via .dataset avoids that second decoding step entirely.
function confirmDeleteForm(form) {
  const message = form.dataset.confirmMessage || "Are you sure you want to delete this?";
  return window.confirm(message);
}

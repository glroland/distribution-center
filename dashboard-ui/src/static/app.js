"use strict";

const state = {
  dc: null,
  pos: [],
  selectedFilename: null,
  map: null,
  shelfStock: {},        // "x,y" -> {sku: qty}
  visited: new Set(),     // "x,y"
  inventory: {},          // sku -> item
  maxOnHand: 1,
  robot: { x: 0, y: 0, carrying: {} },
  run: null,              // {id, source: EventSource}
  poNumber: null,
  lineItems: [],          // [{sku, description, requested_qty, fulfilled_qty, status}]
  helpRequests: {},       // id -> request
  imageIdToSku: {},       // image_id -> sku, correlates get_item_photo captures to infer_sku reads
};

const $ = (sel) => document.querySelector(sel);

function fmtTime(ts) {
  const d = new Date((ts || Date.now() / 1000) * 1000);
  return d.toLocaleTimeString([], { hour12: false });
}

async function api(path, opts) {
  const res = await fetch(path, opts);
  if (!res.ok) {
    const detail = await res.text().catch(() => "");
    throw new Error(`${path} -> ${res.status} ${detail}`);
  }
  const contentType = res.headers.get("content-type") || "";
  return contentType.includes("application/json") ? res.json() : res.text();
}

// ---------------------------------------------------------------------------
// Bootstrap
// ---------------------------------------------------------------------------

function syncTopRowHeights() {
  const po = $("#po-panel");
  const pipeline = $("#pipeline-panel");
  const map = $("#map-panel");
  if (window.matchMedia("(max-width: 1100px)").matches) {
    pipeline.style.height = "";
    map.style.height = "";
    return;
  }
  const height = `${po.getBoundingClientRect().height}px`;
  pipeline.style.height = height;
  map.style.height = height;
}

async function init() {
  new ResizeObserver(syncTopRowHeights).observe($("#po-panel"));

  $("#reset-btn").addEventListener("click", onReset);
  $("#agentic-safety-toggle").addEventListener("change", onToggleAgenticSafety);
  $("#boost-toggle").addEventListener("change", onToggleBoost);
  $("#po-select").addEventListener("change", onSelectPo);
  $("#send-btn").addEventListener("click", onSendPo);
  $("#docling-modal-close").addEventListener("click", () => $("#docling-modal").close());
  $("#docling-modal").addEventListener("click", (e) => {
    if (e.target.id === "docling-modal") $("#docling-modal").close();
  });
  $("#extracted-modal-close").addEventListener("click", () => $("#extracted-modal").close());
  $("#extracted-modal").addEventListener("click", (e) => {
    if (e.target.id === "extracted-modal") $("#extracted-modal").close();
  });
  $("#sticker-modal-close").addEventListener("click", () => $("#sticker-modal").close());
  $("#sticker-modal").addEventListener("click", (e) => {
    if (e.target.id === "sticker-modal") $("#sticker-modal").close();
  });
  $("#po-preview-expand").addEventListener("click", onExpandPoPreview);
  $("#pdf-modal-close").addEventListener("click", () => $("#pdf-modal").close());
  $("#pdf-modal").addEventListener("click", (e) => {
    if (e.target.id === "pdf-modal") $("#pdf-modal").close();
  });

  state.dc = await api("/api/dc");
  await loadDc();
  await loadAgenticSafety();

  refreshHelpRequests();
  setInterval(refreshHelpRequests, 6000);
  setInterval(() => {
    if (!state.run) refreshIdleSnapshots();
  }, 8000);
}

async function loadDc() {
  $("#dc-subtitle").textContent = `${state.dc.display_name} · ${state.dc.location_name}`;
  $("#boost-toggle").checked = false;

  const [pos, mapData, inventory] = await Promise.all([
    api("/api/pos"),
    api("/api/map"),
    api("/api/inventory"),
  ]);

  state.pos = pos;
  renderPoList();

  state.map = mapData;
  state.shelfStock = {};
  for (const cell of mapData.cells) state.shelfStock[`${cell.x},${cell.y}`] = cell.stock;
  state.robot = mapData.robot;
  state.visited = new Set();
  buildMap();

  applyInventory(inventory);

  const shipments = await api("/api/shipments");
  renderShipments(shipments);
}

async function refreshIdleSnapshots() {
  if (!state.dc) return;
  try {
    const [mapData, inventory, shipments] = await Promise.all([
      api("/api/map"),
      api("/api/inventory"),
      api("/api/shipments"),
    ]);
    state.map = mapData;
    for (const cell of mapData.cells) state.shelfStock[`${cell.x},${cell.y}`] = cell.stock;
    state.robot = mapData.robot;
    buildMap();
    applyInventory(inventory);
    renderShipments(shipments);
  } catch (err) {
    console.warn("idle refresh failed", err);
  }
}

// ---------------------------------------------------------------------------
// PO picker
// ---------------------------------------------------------------------------

function renderPoList() {
  const select = $("#po-select");
  select.innerHTML = "";
  for (const po of state.pos) {
    const opt = document.createElement("option");
    opt.value = po.filename;
    opt.textContent = `${po.po_number}  (${Math.round(po.size_bytes / 1024)} KB)`;
    select.appendChild(opt);
  }
}

function onSelectPo(e) {
  state.selectedFilename = e.target.value;
  const frame = $("#po-preview-frame");
  const empty = $("#po-preview-empty");
  const expandBtn = $("#po-preview-expand");
  if (state.selectedFilename) {
    frame.src = `/api/pos/${encodeURIComponent(state.selectedFilename)}/file`;
    frame.style.display = "block";
    empty.style.display = "none";
    expandBtn.hidden = false;
    $("#send-btn").disabled = false;
  } else {
    frame.style.display = "none";
    empty.style.display = "flex";
    expandBtn.hidden = true;
    $("#send-btn").disabled = true;
  }
}

function onExpandPoPreview() {
  if (!state.selectedFilename) return;
  $("#pdf-modal-title").textContent = `Purchase order preview — ${state.selectedFilename}`;
  $("#pdf-modal-frame").src = `/api/pos/${encodeURIComponent(state.selectedFilename)}/file`;
  $("#pdf-modal").showModal();
}

// ---------------------------------------------------------------------------
// Sending a PO / SSE run stream
// ---------------------------------------------------------------------------

async function onSendPo() {
  if (!state.selectedFilename) return;
  if (state.run) state.run.source.close();

  resetRunUi();
  setStep("sent", "active");
  setBadge("run-status-badge", "Sending…", "badge-live");
  $("#send-btn").disabled = true;

  let runId;
  try {
    const res = await api("/api/runs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ filename: state.selectedFilename }),
    });
    runId = res.run_id;
  } catch (err) {
    setBadge("run-status-badge", "Failed to start", "badge-error");
    addActivity("❌", "Could not start the run", String(err), "error");
    $("#send-btn").disabled = false;
    return;
  }

  const source = new EventSource(`/api/runs/${runId}/stream`);
  state.run = { id: runId, source };
  source.onmessage = (evt) => handleRunEvent(JSON.parse(evt.data));
  source.onerror = () => {
    source.close();
    if (state.run && state.run.id === runId) state.run = null;
    $("#send-btn").disabled = false;
  };
}

function resetRunUi() {
  state.poNumber = null;
  state.lineItems = [];
  state.visited = new Set();
  document.querySelectorAll("#stepper li").forEach((li) => li.classList.remove("active", "complete", "error"));
  $("#activity-feed").innerHTML = "";
  $("#po-header").innerHTML = "";
  $("#lines-body").innerHTML = '<tr><td colspan="5" class="empty-row">Waiting for extraction…</td></tr>';
}

function setStep(step, cls) {
  const li = document.querySelector(`#stepper li[data-step="${step}"]`);
  if (!li) return;
  li.classList.remove("active", "complete", "error");
  li.classList.add(cls);
}

function completeStep(step) { setStep(step, "complete"); }

function setBadge(id, text, cls) {
  const el = document.getElementById(id);
  el.textContent = text;
  el.className = `badge ${cls}`;
}

function handleRunEvent(event) {
  const { type, data, ts } = event;
  switch (type) {
    case "run_started":
      addActivity("📨", "PO handed to the distribution center agent", data.po_filename, "", ts);
      break;
    case "ingested":
      completeStep("sent");
      setStep("reading", "active");
      addActivity(
        "📄",
        "PDF converted to text",
        `${data.markdown_length} characters extracted — click to preview`,
        "",
        ts,
        () => openDoclingPreview(data.filename, data.markdown)
      );
      break;
    case "extracted":
      onExtracted(data, ts);
      break;
    case "processed":
      onProcessed(data, ts);
      break;
    case "tool_call":
      onToolCall(data, ts);
      break;
    case "fulfillment_result":
      onFulfillmentResult(data, ts);
      break;
    case "run_complete":
      onRunComplete(data, ts);
      break;
    case "run_failed":
      setBadge("run-status-badge", "Failed", "badge-error");
      const active = document.querySelector("#stepper li.active");
      if (active) active.classList.replace("active", "error");
      addActivity("❌", "Run failed", data.error, "error", ts);
      $("#send-btn").disabled = false;
      break;
  }
}

function onExtracted(data, ts) {
  state.poNumber = data.po_number;
  addActivity(
    "🧠",
    "LLM extracted structured fields from the PO",
    `PO ${data.po_number} · ${data.line_items.length} line item(s) — click to view`,
    "ok",
    ts,
    () => openExtractedPreview(data)
  );

  const header = $("#po-header");
  header.innerHTML = "";
  const bits = [
    ["PO #", data.po_number],
    ["Vendor", data.vendor_name],
    ["Buyer", data.buyer_name],
    ["Ship To", data.ship_to],
  ];
  for (const [label, value] of bits) {
    if (!value) continue;
    const span = document.createElement("span");
    const b = document.createElement("b");
    b.textContent = value;
    span.append(`${label}: `, b);
    header.appendChild(span);
  }

  state.lineItems = data.line_items.map((item) => ({
    sku: item.sku,
    description: item.description,
    requested_qty: item.quantity,
    fulfilled_qty: 0,
    status: "pending",
  }));
  renderLines();
}

function onProcessed(data, ts) {
  completeStep("reading");
  setStep("fulfilling", "active");
  const mismatch = data.totals_mismatch ? " ⚠️ totals mismatch vs. stated total" : "";
  addActivity(
    "🧾",
    `Order ${data.dc_order_id} processed`,
    `Subtotal $${data.computed_subtotal.toFixed(2)}${mismatch}`,
    data.totals_mismatch ? "warn" : "",
    ts
  );
}

const TOOL_LABELS = {
  wms__get_location: ["🏭", () => "Checked warehouse location"],
  wms__get_inventory_status: ["🔍", (a) => `Checked stock for ${a.sku || "all SKUs"}`],
  wms__adjust_inventory: ["📦", (a) => `Adjusted ${a.sku} by ${a.delta > 0 ? "+" : ""}${a.delta}`],
  wms__reset_inventory: ["🔄", () => "Reset inventory ledger"],
  robot__get_robot_status: ["🤖", () => "Checked robot status"],
  robot__get_warehouse_map: ["🗺️", () => "Scanned the full warehouse map"],
  robot__find_item: ["📍", (a) => `Located shelves stocking ${a.sku}`],
  robot__get_shelf_inventory: ["🗺️", (a) => `Inspected shelf (${a.x ?? "cur"}, ${a.y ?? "cur"})`],
  robot__plan_and_fetch_items: ["🚚", (a) => `Picking ${a.items?.length ?? 0} item(s)`],
  robot__move_robot: ["🚚", (a) => `Robot moving to (${a.x}, ${a.y})`],
  robot__fetch_item: ["📥", (a) => `Picked ${a.qty}× ${a.sku} off the shelf`],
  robot__deliver_items: ["🏗️", () => "Delivered carried items to the dock"],
  robot__reset_robot: ["🔄", () => "Reset robot & shelf stock"],
  robot__get_item_photo: ["📸", (a) => `Captured shelf sticker photo for ${a.sku}`],
  label__infer_sku: [
    "🔬",
    (a, r) => {
      if (!r) return "Reading shelf sticker with vision model";
      const pct = Math.round((r.confidence ?? 0) * 100);
      return r.sku
        ? `Vision model read label as ${r.sku} (${pct}% confidence)`
        : `Vision model couldn't read a SKU off the label (${pct}% confidence)`;
    },
  ],
  shipping__ship_order: ["🚀", (a) => `Handed PO ${a.po_number} to a carrier`],
  shipping__get_shipment: ["📬", () => "Looked up shipment"],
  shipping__track_shipment: ["📬", (a) => `Tracked ${a.tracking_number}`],
  shipping__list_shipments: ["📋", () => "Listed shipments"],
  shipping__reset_shipments: ["🔄", () => "Cleared shipments"],
  supervisor__request_help: ["🆘", (a) => `Escalated to a human supervisor: ${a.question}`],
  supervisor__request_transfer: [
    "🔁",
    (a, r) => {
      if (!r) return `Requesting transfer of ${a.quantity}× ${a.sku} from another DC`;
      return r.status === "available"
        ? `Transfer approved: ${a.quantity}× ${a.sku} from ${r.source_location}`
        : `Transfer unavailable: no other DC has ${a.sku} in stock`;
    },
  ],
  robot__restock_shelf: [
    "📤",
    (a, r) =>
      r
        ? `Restocked ${a.qty}× ${a.sku} onto shelf (${r.location_x}, ${r.location_y})`
        : `Restocking ${a.qty}× ${a.sku}`,
  ],
};

function onToolCall(data, ts) {
  const parsed = data.ok ? safeJson(data.result) : null;
  const [icon, labelFn] = TOOL_LABELS[data.name] || ["🔧", () => data.name];
  const title = labelFn(data.arguments || {}, parsed);
  let tone = data.ok ? "" : "error";
  let detail = data.ok ? "" : `Error: ${data.result}`;

  let onClick;
  if (data.name === "robot__fetch_item" && parsed?.sticker_available) {
    onClick = () => openStickerPreview(data.arguments.sku, `${data.arguments.qty}× picked`);
  } else if (data.ok && data.name === "robot__get_item_photo" && parsed?.image_id) {
    // Correlate this capture's image_id -> sku so the later label__infer_sku
    // call (which only carries the image_id, not the sku) can look it up.
    state.imageIdToSku[parsed.image_id] = parsed.sku;
    onClick = () => openStickerPreview(parsed.sku, "captured for verification");
  } else if (data.ok && data.name === "label__infer_sku" && parsed) {
    const pct = Math.round((parsed.confidence ?? 0) * 100);
    if (!parsed.sku || parsed.confidence < 0.7) tone = "warn";
    detail = `${pct}% confidence (${Math.round(parsed.inference_ms ?? 0)} ms)`;
    const requestedSku = state.imageIdToSku[data.arguments?.image_id];
    const readAs = parsed.sku
      ? `read as ${parsed.sku} (${pct}% confidence)`
      : `unreadable (${pct}% confidence)`;
    if (requestedSku) onClick = () => openStickerPreview(requestedSku, readAs);
  }

  addActivity(icon, title, detail, tone, ts, onClick);

  if (!data.ok) return;
  if (parsed) applyToolResult(data.name, data.arguments || {}, parsed);
}

function safeJson(text) {
  try { return JSON.parse(text); } catch { return null; }
}

function applyToolResult(name, args, result) {
  switch (name) {
    case "wms__adjust_inventory":
      upsertInventoryItem(result, true);
      break;
    case "wms__get_inventory_status":
      if (result.items) for (const item of result.items) upsertInventoryItem(item, false);
      else if (result.sku) upsertInventoryItem(result, false);
      break;

    case "robot__move_robot":
    case "robot__fetch_item":
    case "robot__get_robot_status":
      updateRobotState(result);
      break;
    case "robot__deliver_items":
      if (result.status) updateRobotState(result.status);
      break;
    case "robot__plan_and_fetch_items":
      animateRobotTrace(result.trace || []);
      break;
    case "robot__get_shelf_inventory":
      state.shelfStock[`${result.location_x},${result.location_y}`] = result.stock;
      updateShelfCellDisplay(result.location_x, result.location_y);
      break;
    case "robot__get_warehouse_map":
      for (const cell of result.shelves || []) {
        state.shelfStock[`${cell.x},${cell.y}`] = cell.stock;
        updateShelfCellDisplay(cell.x, cell.y);
      }
      if (result.robot) updateRobotState(result.robot);
      break;

    case "shipping__ship_order":
    case "shipping__get_shipment":
    case "shipping__track_shipment":
      addShipmentCard(result);
      break;

    case "supervisor__request_help":
      upsertHelpRequest(result);
      break;
  }
}

function updateRobotState(status) {
  state.robot = status;
  markVisited(status.x, status.y);
  moveRobotMarker(status.x, status.y);
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

// robot__plan_and_fetch_items resolves as a single tool call, but its trace
// covers a whole multi-stop pick run - replay it step by step so the grid
// still animates instead of the marker jumping straight to its final spot.
// Runs detached from onToolCall/applyToolResult (not awaited there) so it
// doesn't block handling of subsequent SSE events.
//
// Every "pick" step carries sticker_available: true unconditionally (one per
// shelf visit, which can be more than one per SKU) - it does NOT mean a
// photo was actually captured/verified for that step. The real capture +
// verification are separate robot__get_item_photo / label__infer_sku tool
// calls the LLM makes once per distinct SKU, which get their own activity
// entries via onToolCall. So this loop only drives the marker animation and
// intentionally does not add its own activity-feed entries, to avoid
// spamming one fabricated "captured sticker photo" line per shelf visit.
let robotTraceToken = 0;
async function animateRobotTrace(trace) {
  const token = ++robotTraceToken;
  for (const step of trace) {
    if (token !== robotTraceToken) return; // a newer trace/tool call has taken over
    if (step.status) updateRobotState(step.status);
    await sleep(650);
  }
}

function onFulfillmentResult(data, ts) {
  completeStep("fulfilling");
  applyFulfillmentItems(data.items || []);
  if (data.shipment) addShipmentCard(data.shipment);
  addActivity("✅", "Fulfillment agent finished", data.summary, data.order_status === "escalated" ? "warn" : "ok", ts);
}

function onRunComplete(data, ts) {
  completeStep("done");
  setBadge("run-status-badge", "Complete", "badge-done");
  const record = data.result || {};
  if (record.fulfillment) applyFulfillmentItems(record.fulfillment.items || []);
  addActivity("🏁", "Run complete", data.summary || "Done", "ok", ts);
  $("#send-btn").disabled = false;
  if (state.run) { state.run.source.close(); state.run = null; }
}

// ---------------------------------------------------------------------------
// PO line items
// ---------------------------------------------------------------------------

function applyFulfillmentItems(items) {
  for (const incoming of items) {
    let existing = state.lineItems.find((li) => li.sku && li.sku === incoming.sku);
    if (!existing) existing = state.lineItems.find((li) => li.description === incoming.description);
    if (existing) Object.assign(existing, incoming);
    else state.lineItems.push({ ...incoming });
  }
  renderLines();
}

function renderLines() {
  const body = $("#lines-body");
  if (state.lineItems.length === 0) {
    body.innerHTML = '<tr><td colspan="5" class="empty-row">No PO in flight</td></tr>';
    return;
  }
  body.innerHTML = "";
  for (const item of state.lineItems) {
    const tr = document.createElement("tr");
    const status = item.status || "pending";
    tr.innerHTML = `
      <td class="mono">${item.sku || "—"}</td>
      <td>${escapeHtml(item.description || "")}</td>
      <td>${item.requested_qty ?? "—"}</td>
      <td>${item.fulfilled_qty ?? "—"}</td>
      <td><span class="pill pill-${status}">${status.replace("_", " ")}</span></td>
    `;
    body.appendChild(tr);
  }
}

// ---------------------------------------------------------------------------
// Inventory ledger
// ---------------------------------------------------------------------------

function applyInventory(items) {
  state.inventory = {};
  for (const item of items) state.inventory[item.sku] = item;
  renderLedger();
}

function upsertInventoryItem(item, flash) {
  if (!item || !item.sku) return;
  state.inventory[item.sku] = { ...state.inventory[item.sku], ...item };
  renderLedger(flash ? item.sku : null);
}

function renderLedger(flashSku) {
  const values = Object.values(state.inventory);
  state.maxOnHand = Math.max(1, ...values.map((i) => i.on_hand_qty));
  const body = $("#ledger-body");
  body.innerHTML = "";
  const sorted = values.sort((a, b) => a.sku.localeCompare(b.sku));
  for (const item of sorted) {
    const row = document.createElement("div");
    row.className = "ledger-row" + (item.on_hand_qty === 0 ? " zero" : "") + (item.sku === flashSku ? " flash" : "");
    const pct = Math.min(100, (item.on_hand_qty / state.maxOnHand) * 100);
    row.innerHTML = `
      <span class="sku">${item.sku}</span>
      <span class="ledger-bar-track"><span class="ledger-bar-fill" style="width:${pct}%"></span></span>
      <span class="qty">${item.on_hand_qty}</span>
    `;
    body.appendChild(row);
  }
}

// ---------------------------------------------------------------------------
// Shipments
// ---------------------------------------------------------------------------

function renderShipments(shipments) {
  const body = $("#shipments-body");
  if (!shipments || shipments.length === 0) {
    body.innerHTML = '<div class="empty-row">No shipments yet</div>';
    return;
  }
  body.innerHTML = "";
  for (const shipment of shipments.slice().reverse()) buildShipmentCard(shipment, body);
}

function addShipmentCard(shipment) {
  const body = $("#shipments-body");
  if (body.querySelector(".empty-row")) body.innerHTML = "";
  if (document.getElementById(`shipment-${shipment.tracking_number}`)) return;
  buildShipmentCard(shipment, body, true);
}

function buildShipmentCard(shipment, container, prepend) {
  const card = document.createElement("div");
  card.className = "shipment-card";
  card.id = `shipment-${shipment.tracking_number}`;
  const items = (shipment.items || []).map((i) => `${i.qty}× ${i.sku}`).join(", ");
  card.innerHTML = `
    <div class="carrier">${shipment.carrier}</div>
    <div class="tracking">${shipment.tracking_number}</div>
    <div class="meta">PO ${shipment.po_number || "—"} · ${items || "—"}</div>
    <div class="meta">ETA ${shipment.estimated_delivery ? new Date(shipment.estimated_delivery).toLocaleDateString() : "—"}</div>
  `;
  if (prepend) container.prepend(card);
  else container.appendChild(card);
}

// ---------------------------------------------------------------------------
// Human-in-the-loop
// ---------------------------------------------------------------------------

async function refreshHelpRequests() {
  try {
    const requests = await api("/api/help-requests");
    for (const req of requests) {
      const existing = state.helpRequests[req.id];
      if (!existing && req.status === "open") setBadge("hitl-badge", "Agent needs help", "badge-warn");
      state.helpRequests[req.id] = req;
    }
    renderHelpRequests();
  } catch (err) {
    console.warn("help-requests refresh failed", err);
  }
}

function upsertHelpRequest(request, isNew = true) {
  const wasKnown = !!state.helpRequests[request.id];
  state.helpRequests[request.id] = request;
  renderHelpRequests();
  if (isNew && !wasKnown && request.status === "open") {
    setBadge("hitl-badge", "Agent needs help", "badge-warn");
  }
}

// Patches the DOM incrementally rather than rebuilding it wholesale: a card
// whose status hasn't changed since the last render is left completely
// untouched, so an in-progress (unsubmitted) resolution the human is
// mid-typing into an *other* card's form is never clobbered by a background
// poll or a live event for a different request.
function renderHelpRequests() {
  const body = $("#hitl-body");
  const requests = Object.values(state.helpRequests).sort((a, b) => b.id - a.id);

  if (requests.length === 0) {
    body.innerHTML = '<div class="empty-row">The agent hasn\'t asked for help yet.</div>';
    setBadge("hitl-badge", "No escalations", "badge-idle");
    return;
  }
  if (body.querySelector(".empty-row")) body.innerHTML = "";

  const openCount = requests.filter((r) => r.status === "open").length;
  setBadge(
    "hitl-badge",
    openCount > 0 ? `${openCount} open` : "All resolved",
    openCount > 0 ? "badge-warn" : "badge-done"
  );

  const tpl = $("#tpl-help-request");
  let previousCard = null;
  for (const req of requests) {
    const existingCard = body.querySelector(`.help-card[data-id="${req.id}"]`);
    if (existingCard && existingCard.dataset.status === req.status) {
      previousCard = existingCard;
      continue;
    }

    const node = tpl.content.cloneNode(true);
    const card = node.querySelector(".help-card");
    card.dataset.id = req.id;
    card.dataset.status = req.status;
    if (req.status === "resolved") card.classList.add("resolved");
    node.querySelector(".help-question").textContent = req.question;
    node.querySelector(".help-context").textContent = req.context ? `Context: ${req.context}` : `Request #${req.id}`;

    const form = node.querySelector(".help-form");
    const resolutionEl = node.querySelector(".help-resolution");
    if (req.status === "resolved") {
      form.remove();
      resolutionEl.textContent = `✔ Resolved: ${req.resolution}`;
    } else {
      form.addEventListener("submit", (e) => onResolveHelp(e, req.id));
    }

    if (existingCard) existingCard.replaceWith(node);
    else if (previousCard) previousCard.after(node);
    else body.prepend(node);
    previousCard = card;
  }
}

async function onResolveHelp(e, id) {
  e.preventDefault();
  const input = e.target.querySelector("input");
  const resolution = input.value.trim();
  if (!resolution) return;
  try {
    const updated = await api(`/api/help-requests/${id}/resolve`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ resolution }),
    });
    upsertHelpRequest(updated, false);
  } catch (err) {
    alert(`Could not resolve request: ${err}`);
  }
}

// ---------------------------------------------------------------------------
// Warehouse map (SVG)
// ---------------------------------------------------------------------------

function buildMap() {
  const svg = $("#warehouse-svg");
  svg.innerHTML = "";
  if (!state.map) return;

  const { width, height, dock } = state.map;
  svg.setAttribute("viewBox", `0 0 ${width * 10} ${height * 10}`);
  const cellW = 10, cellH = 10;
  const pad = 0.8;

  const ns = "http://www.w3.org/2000/svg";
  const cellsGroup = document.createElementNS(ns, "g");
  cellsGroup.setAttribute("id", "cells-group");
  svg.appendChild(cellsGroup);

  for (let x = 0; x < width; x++) {
    for (let y = 0; y < height; y++) {
      const rect = document.createElementNS(ns, "rect");
      rect.setAttribute("id", `cell-${x}-${y}`);
      rect.setAttribute("x", x * cellW + pad / 2);
      rect.setAttribute("y", y * cellH + pad / 2);
      rect.setAttribute("width", cellW - pad);
      rect.setAttribute("height", cellH - pad);
      rect.setAttribute("rx", 1.2);
      rect.classList.add("map-cell");
      const title = document.createElementNS(ns, "title");
      rect.appendChild(title);
      cellsGroup.appendChild(rect);
    }
  }

  const dockRect = document.createElementNS(ns, "rect");
  dockRect.setAttribute("id", "dock-cell");
  dockRect.setAttribute("x", dock.x * cellW + pad / 2);
  dockRect.setAttribute("y", dock.y * cellH + pad / 2);
  dockRect.setAttribute("width", cellW - pad);
  dockRect.setAttribute("height", cellH - pad);
  dockRect.setAttribute("rx", 1.2);
  dockRect.setAttribute("fill", "none");
  dockRect.setAttribute("stroke", "var(--warn)");
  dockRect.setAttribute("stroke-width", "0.6");
  const dockTitle = document.createElementNS(ns, "title");
  dockTitle.textContent = "Dock";
  dockRect.appendChild(dockTitle);
  svg.appendChild(dockRect);

  const visitedGroup = document.createElementNS(ns, "g");
  visitedGroup.setAttribute("id", "visited-group");
  svg.appendChild(visitedGroup);

  const robot = document.createElementNS(ns, "circle");
  robot.setAttribute("id", "robot-marker");
  robot.setAttribute("r", Math.min(cellW, cellH) * 0.28);
  robot.setAttribute("fill", "var(--robot)");
  robot.style.filter = "drop-shadow(0 0 3px var(--robot))";
  robot.style.transition = "cx 0.6s ease, cy 0.6s ease";
  svg.appendChild(robot);

  for (const [key, stock] of Object.entries(state.shelfStock)) {
    const [x, y] = key.split(",").map(Number);
    updateShelfCellDisplay(x, y, stock);
  }
  for (const key of state.visited) {
    const [x, y] = key.split(",").map(Number);
    drawVisitedMark(x, y);
  }
  moveRobotMarker(state.robot.x, state.robot.y, true);
}

function updateShelfCellDisplay(x, y, stockOverride) {
  const rect = document.getElementById(`cell-${x}-${y}`);
  if (!rect) return;
  const stock = stockOverride || state.shelfStock[`${x},${y}`] || {};
  const total = Object.values(stock).reduce((a, b) => a + b, 0);
  if (total > 0) {
    const intensity = Math.min(1, total / 60);
    rect.style.fill = `color-mix(in srgb, var(--accent) ${20 + intensity * 60}%, var(--bg-elevated))`;
    rect.style.stroke = "var(--accent-dim)";
  } else {
    rect.style.fill = "var(--bg-elevated)";
    rect.style.stroke = "var(--border)";
  }
  rect.style.strokeWidth = "0.4";
  const title = rect.querySelector("title");
  const entries = Object.entries(stock).map(([sku, qty]) => `${sku}: ${qty}`).join("\n");
  title.textContent = entries ? `(${x}, ${y})\n${entries}` : `(${x}, ${y}) empty`;
}

function moveRobotMarker(x, y, instant) {
  const robot = document.getElementById("robot-marker");
  if (!robot || !state.map) return;
  const cellW = 10, cellH = 10;
  if (instant) robot.style.transition = "none";
  robot.setAttribute("cx", x * cellW + cellW / 2);
  robot.setAttribute("cy", y * cellH + cellH / 2);
  if (instant) requestAnimationFrame(() => { robot.style.transition = "cx 0.6s ease, cy 0.6s ease"; });
}

function markVisited(x, y) {
  const key = `${x},${y}`;
  if (state.visited.has(key)) return;
  state.visited.add(key);
  drawVisitedMark(x, y);
}

function drawVisitedMark(x, y) {
  const group = document.getElementById("visited-group");
  if (!group) return;
  const ns = "http://www.w3.org/2000/svg";
  const cellW = 10, cellH = 10, pad = 0.8;
  const rect = document.createElementNS(ns, "rect");
  rect.setAttribute("x", x * cellW + pad / 2);
  rect.setAttribute("y", y * cellH + pad / 2);
  rect.setAttribute("width", cellW - pad);
  rect.setAttribute("height", cellH - pad);
  rect.setAttribute("rx", 1.2);
  rect.setAttribute("fill", "none");
  rect.setAttribute("stroke", "var(--accent)");
  rect.setAttribute("stroke-width", "0.5");
  rect.setAttribute("stroke-dasharray", "1.5,1");
  group.appendChild(rect);
}

// ---------------------------------------------------------------------------
// Reset
// ---------------------------------------------------------------------------

async function onReset() {
  if (!confirm(`Reset inventory, robot position, and shipments for ${state.dc.display_name}?`)) return;
  await api("/api/reset", { method: "POST" });

  if (state.run) state.run.source.close();
  state.run = null;

  $("#boost-toggle").checked = false;
  await loadDc();

  resetRunUi();
  setBadge("run-status-badge", "Idle", "badge-idle");

  state.selectedFilename = null;
  $("#po-select").value = "";
  $("#po-preview-frame").style.display = "none";
  $("#po-preview-frame").src = "";
  $("#po-preview-empty").style.display = "flex";
  $("#po-preview-expand").hidden = true;
  $("#send-btn").disabled = true;

  state.helpRequests = {};
  renderHelpRequests();

  state.imageIdToSku = {};
}

async function loadAgenticSafety() {
  try {
    const { enabled } = await api("/api/agentic-safety");
    $("#agentic-safety-toggle").checked = enabled;
  } catch (err) {
    console.warn("could not load agentic safety state", err);
  }
}

async function onToggleAgenticSafety(e) {
  const checkbox = e.target;
  const enabled = checkbox.checked;
  checkbox.disabled = true;
  try {
    await api("/api/agentic-safety", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ enabled }),
    });
  } catch (err) {
    console.warn("agentic safety toggle failed", err);
    checkbox.checked = !enabled;
  } finally {
    checkbox.disabled = false;
  }
}

async function onToggleBoost(e) {
  const checkbox = e.target;
  const enabled = checkbox.checked;
  checkbox.disabled = true;
  try {
    await api("/api/inventory-boost", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ enabled }),
    });
    await refreshIdleSnapshots();
  } catch (err) {
    console.warn("inventory boost toggle failed", err);
    checkbox.checked = !enabled;
  } finally {
    checkbox.disabled = false;
  }
}

// ---------------------------------------------------------------------------
// Activity feed
// ---------------------------------------------------------------------------

function addActivity(icon, title, detail, tone, ts, onClick) {
  const feed = $("#activity-feed");
  if (feed.querySelector(".activity-empty")) feed.innerHTML = "";
  const tpl = $("#tpl-activity-item");
  const node = tpl.content.cloneNode(true);
  const item = node.querySelector(".activity-item");
  if (tone) item.classList.add(`tone-${tone}`);
  node.querySelector(".activity-icon").textContent = icon;
  node.querySelector(".activity-title").textContent = title;
  node.querySelector(".activity-detail").textContent = detail || "";
  node.querySelector(".activity-time").textContent = fmtTime(ts);
  if (onClick) {
    item.classList.add("activity-clickable");
    item.title = "Click to preview";
    item.addEventListener("click", onClick);
  }
  feed.appendChild(node);
  feed.scrollTop = feed.scrollHeight;
}

// ---------------------------------------------------------------------------
// Docling conversion preview
// ---------------------------------------------------------------------------

function openDoclingPreview(filename, markdown) {
  $("#docling-modal-title").textContent = `PDF conversion result${filename ? ` — ${filename}` : ""}`;
  $("#docling-modal-meta").textContent = markdown
    ? `${markdown.length.toLocaleString()} characters · this is the text the LLM used for field extraction`
    : "No markdown was included with this event";
  $("#docling-modal-body").textContent = markdown || "(preview unavailable)";
  $("#docling-modal").showModal();
}

// ---------------------------------------------------------------------------
// LLM data-extraction preview
// ---------------------------------------------------------------------------

function openExtractedPreview(data) {
  $("#extracted-modal-title").textContent = `LLM extracted data elements${data.po_number ? ` — PO ${data.po_number}` : ""}`;
  $("#extracted-modal-meta").textContent = `${data.line_items.length} line item(s) · structured fields the LLM pulled from the converted text`;
  $("#extracted-modal-body").textContent = JSON.stringify(data, null, 2);
  $("#extracted-modal").showModal();
}

// ---------------------------------------------------------------------------
// Sticker photo preview
// ---------------------------------------------------------------------------

// The actual photo bytes a given get_item_photo/infer_sku call used are
// never retrievable after the fact - label-api's image store is single-read
// (infer_sku pops it) precisely so raw image bytes never have to round-trip
// through the LLM's tool-call context (see image_store.py). This preview is
// therefore an illustrative regeneration in the same synthetic style, not
// literally the bytes that were inferred against.
function openStickerPreview(sku, subtitle) {
  $("#sticker-modal-title").textContent = `Sticker photo — ${sku}`;
  $("#sticker-modal-meta").textContent =
    `${subtitle ? `${subtitle} · ` : ""}illustrative reproduction (simulated camera capture, not the exact frame that was inferred against)`;
  $("#sticker-modal-img").src = `/api/stickers/${encodeURIComponent(sku)}?color_mode=random&t=${Date.now()}`;
  $("#sticker-modal").showModal();
}

function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str;
  return div.innerHTML;
}

init().catch((err) => {
  console.error(err);
  $("#dc-subtitle").textContent = `Failed to load: ${err}`;
});

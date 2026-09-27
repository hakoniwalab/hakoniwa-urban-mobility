// Urban Studio frontend (tools/urban_studio.py serves it and the /api it calls).

const $ = (selector, root = document) => root.querySelector(selector);
const el = (tag, attributes = {}, ...children) => {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attributes)) {
    if (key === "class") node.className = value;
    else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else if (value !== undefined && value !== null && value !== false) node.setAttribute(key, value === true ? "" : value);
  }
  node.append(...children.filter((child) => child !== null && child !== undefined));
  return node;
};

async function api(method, path, body) {
  const response = await fetch(`/api/${path}`, {
    method,
    headers: body === undefined ? {} : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || `${response.status}`);
  return data;
}

const state = {
  assets: [],
  compositions: [],
  current: null, // { id, editable, composition }
  job: null,
  pollTimer: null,
};

const assetById = (id) => state.assets.find((asset) => asset.id === id);
const vehicles = () => state.assets.filter((asset) => asset.kind === "vehicle");
const worlds = () => state.assets.filter((asset) => asset.kind === "city" || asset.kind === "plain");

// --- Tabs ---------------------------------------------------------------------------

function showTab(name) {
  for (const button of document.querySelectorAll(".tabs button")) {
    button.setAttribute("aria-selected", String(button.dataset.tab === name));
  }
  for (const section of document.querySelectorAll(".tab")) section.hidden = section.id !== `tab-${name}`;
  try { localStorage.setItem("urban-studio-tab", name); } catch { /* storage may be unavailable */ }
  if (name === "simulation") refreshRunPlan();
}

// --- Assets and City ----------------------------------------------------------------

function renderAssets() {
  const worldBody = $("#world-table tbody");
  worldBody.replaceChildren(...worlds().map((world) => el("tr", {},
    el("td", {}, el("code", {}, world.id)), el("td", {}, world.kind === "city" ? "City" : "プレーン"), el("td", {}, world.title))));
  const vehicleBody = $("#vehicle-table tbody");
  vehicleBody.replaceChildren(...vehicles().map((vehicle) => el("tr", {},
    el("td", {}, el("code", {}, vehicle.id)),
    el("td", {}, vehicle.category),
    el("td", {}, vehicle.title),
    el("td", {}, vehicle.simulator),
    el("td", {}, Object.keys(vehicle.controls).join(" / ")),
    el("td", {}, String(vehicle.ground_clearance_m)))));
  const cityBody = $("#city-table tbody");
  const cities = state.assets.filter((asset) => asset.kind === "city");
  cityBody.replaceChildren(...(cities.length ? cities.map((city) => el("tr", {},
    el("td", {}, el("code", {}, city.id)), el("td", {}, el("code", {}, city.path)))) :
    [el("tr", {}, el("td", { colspan: 2, class: "hint" }, "登録済みの City はありません"))]));

  const worldSelect = $("#world-select");
  const groups = [["City", "city"], ["プレーン", "plain"]].map(([label, kind]) => el("optgroup", { label },
    ...worlds().filter((world) => world.kind === kind).map((world) => el("option", { value: world.id }, `${world.title} (${world.id})`))));
  worldSelect.replaceChildren(...groups);
  $("#add-asset").replaceChildren(...vehicles().map((vehicle) => el("option", { value: vehicle.id }, vehicle.title)));
}

// --- Composition list and editor -------------------------------------------------------

function renderCompositionList() {
  const list = $("#composition-list");
  list.replaceChildren(...state.compositions.map((item) => el("li", {},
    el("button", {
      "aria-current": String(state.current?.id === item.id),
      onclick: () => openComposition(item.id),
    }, item.id, el("span", { class: "meta" }, `${item.vehicles} 台${item.editable ? "" : "・例"}`)))));
  const runSelect = $("#run-composition");
  const selected = runSelect.value;
  runSelect.replaceChildren(...state.compositions.map((item) => el("option", { value: item.id }, item.id)));
  if (selected) runSelect.value = selected;
}

async function loadCompositions() {
  state.compositions = await api("GET", "compositions");
  renderCompositionList();
}

async function openComposition(id) {
  state.current = await api("GET", `compositions/${id}`);
  renderEditor();
  renderCompositionList();
  setStatus($("#compose-status"), state.current.editable ? "" : "例の Composition です。保存するとコピーが作られます。");
}

function newComposition() {
  const world = worlds().find((item) => item.kind === "plain") || worlds()[0];
  state.current = {
    id: "", editable: true,
    composition: { schema: "hakoniwa.composition/v1", id: "", world: world?.id, vehicles: [] },
  };
  renderEditor();
  renderCompositionList();
  setStatus($("#compose-status"), "");
  $("#composition-id").focus();
}

function defaultControl(asset) {
  return Object.keys(asset.controls)[0];
}

function addVehicle() {
  const asset = assetById($("#add-asset").value);
  if (!asset || !state.current) return;
  const list = state.current.composition.vehicles;
  const prefix = asset.category === "car" ? "Car" : "Drone";
  let index = 1;
  while (list.some((vehicle) => vehicle.name === `${prefix}-${index}`)) index += 1;
  list.push({
    name: `${prefix}-${index}`, asset: asset.id, control: defaultControl(asset),
    spawn: { east_m: 0, north_m: 0, yaw_deg: 0 },
  });
  renderEditor();
}

function numberInput(label, value, onchange, step = "0.1") {
  return el("label", { class: "field" }, label,
    el("input", { type: "number", step, value: String(value ?? 0), onchange: (event) => onchange(Number(event.target.value)) }));
}

function renderVehicle(vehicle, index) {
  const composition = state.current.composition;
  const asset = assetById(vehicle.asset);
  const controls = asset ? Object.keys(asset.controls) : [vehicle.control];
  const params = asset?.controls[vehicle.control]?.params || {};
  vehicle.params = vehicle.params || {};

  const controlSelect = el("select", {
    onchange: (event) => { vehicle.control = event.target.value; vehicle.params = {}; renderEditor(); },
  }, ...controls.map((name) => el("option", { value: name, selected: name === vehicle.control }, name === "rc" ? "RC（コントローラ）" : "API（プログラム）")));

  const paramFields = Object.entries(params).map(([name, definition]) => el("label", { class: "field" },
    `${name}${definition.required ? " *" : ""}`,
    el("input", {
      value: vehicle.params[name] ?? "",
      placeholder: definition.default !== undefined ? String(definition.default) : definition.type || "",
      onchange: (event) => {
        const raw = event.target.value.trim();
        if (raw === "") delete vehicle.params[name];
        else vehicle.params[name] = definition.type === "number" ? Number(raw) : raw;
      },
    })));

  const hasCars = composition.vehicles.some((item) => assetById(item.asset)?.category === "car");
  const mirrorable = asset?.category === "drone" && asset.interactions.includes("drone-mirror") && hasCars;
  composition.interactions = composition.interactions || [];
  const mirrored = composition.interactions.some((item) => item.type === "drone-mirror" && item.drone === vehicle.name);
  const mirror = mirrorable ? el("label", { class: "field" }, "Car 世界に反映",
    el("input", {
      type: "checkbox", checked: mirrored,
      onchange: (event) => {
        composition.interactions = composition.interactions.filter((item) => item.drone !== vehicle.name);
        if (event.target.checked) composition.interactions.push({ type: "drone-mirror", drone: vehicle.name });
      },
    })) : null;

  return el("div", { class: "vehicle" },
    el("div", { class: "row" },
      el("label", { class: "field" }, "名前", el("input", {
        value: vehicle.name,
        onchange: (event) => {
          const previous = vehicle.name;
          vehicle.name = event.target.value.trim();
          for (const item of composition.interactions || []) if (item.drone === previous) item.drone = vehicle.name;
        },
      })),
      el("label", { class: "field grow" }, "Asset", el("span", {}, asset ? `${asset.title} (${asset.id})` : vehicle.asset)),
      el("label", { class: "field" }, "制御", controlSelect),
      numberInput("east (m)", vehicle.spawn?.east_m, (value) => { vehicle.spawn.east_m = value; }),
      numberInput("north (m)", vehicle.spawn?.north_m, (value) => { vehicle.spawn.north_m = value; }),
      numberInput("yaw (°)", vehicle.spawn?.yaw_deg, (value) => { vehicle.spawn.yaw_deg = value; }, "1"),
      mirror,
      el("button", {
        class: "icon", title: "削除", "aria-label": `${vehicle.name} を削除`,
        onclick: () => {
          composition.vehicles.splice(index, 1);
          composition.interactions = (composition.interactions || []).filter((item) => item.drone !== vehicle.name);
          renderEditor();
        },
      }, "✕")),
    paramFields.length ? el("div", { class: "row params" }, ...paramFields) : null,
    vehicle.program ? el("p", { class: "hint" }, `プログラム差し替え: ${vehicle.program}`) : null);
}

function renderEditor() {
  const current = state.current;
  const editor = $(".editor");
  editor.hidden = !current;
  if (!current) return;
  const composition = current.composition;
  $("#composition-id").value = current.id;
  $("#world-select").value = composition.world;
  $("#vehicles").replaceChildren(...(composition.vehicles.length
    ? composition.vehicles.map(renderVehicle)
    : [el("p", { class: "hint" }, "車両を追加してください")]));
}

function setStatus(node, message, kind = "") {
  node.textContent = message;
  node.className = `status ${kind}`;
}

async function saveComposition() {
  const current = state.current;
  if (!current) return;
  const id = current.id;
  const composition = { ...current.composition };
  if (!composition.interactions?.length) delete composition.interactions;
  for (const vehicle of composition.vehicles) if (vehicle.params && !Object.keys(vehicle.params).length) delete vehicle.params;
  const status = $("#compose-status");
  setStatus(status, "検証中…");
  try {
    const plan = await api("PUT", `compositions/${id}`, composition);
    state.current = { id, editable: true, composition: { ...composition, id } };
    await loadCompositions();
    setStatus(status, `保存しました（経路: ${plan.route}${plan.managed_recipe ? "・managed Recipe あり" : ""}）`, "ok");
  } catch (error) {
    setStatus(status, error.message, "error");
  }
}

// --- Simulation ---------------------------------------------------------------------

async function refreshRunPlan() {
  const id = $("#run-composition").value;
  const node = $("#run-plan");
  if (!id) { node.textContent = ""; return; }
  try {
    const plan = await api("GET", `compositions/${id}/plan`);
    node.textContent = `経路: ${plan.route} ／ World: ${plan.world.id} (${plan.world.kind}) ／ 車両: ${plan.vehicles.map((item) => `${item.name}=${item.control}`).join(", ")}`;
    node.classList.remove("error");
  } catch (error) {
    node.textContent = error.message;
    node.classList.add("error");
  }
  refreshViewer(false);
}

async function refreshViewer(show) {
  const id = $("#run-composition").value;
  const link = $("#viewer-link");
  try {
    const { url } = await api("GET", `compositions/${id}/viewer`);
    link.hidden = !url;
    if (url) link.href = url;
    if (url && show) {
      $("#viewer-panel").hidden = false;
      $("#viewer-frame").src = url;
    }
  } catch {
    link.hidden = true;
  }
}

function renderProgress(job) {
  const bar = $("#progress-bar");
  const text = $("#progress-text");
  const progress = job.progress;
  if (job.state !== "running") {
    bar.classList.remove("indeterminate");
    bar.style.width = job.state === "succeeded" ? "100%" : bar.style.width;
    setStatus(text, `${job.command}: ${job.state === "succeeded" ? "完了" : `失敗 (exit ${job.exit_code})`}`,
      job.state === "succeeded" ? "ok" : "error");
    return;
  }
  if (progress?.percent !== undefined) {
    bar.classList.remove("indeterminate");
    bar.style.width = `${progress.percent}%`;
  } else {
    bar.classList.add("indeterminate");
  }
  const detail = progress ? [progress.phase, progress.model,
    progress.total ? `${progress.current}/${progress.total}` : null,
    progress.elapsed_sec !== undefined ? `${progress.elapsed_sec}s` : null].filter(Boolean).join(" ") : "";
  setStatus(text, `${job.command} 実行中… ${detail}`);
}

async function pollJob() {
  const job = state.job;
  if (!job) return;
  try {
    const snapshot = await api("GET", `jobs/${job.id}?since=${job.lineCount}`);
    job.lineCount = snapshot.line_count;
    const log = $("#log");
    if (snapshot.lines.length) {
      const atBottom = log.scrollTop + log.clientHeight >= log.scrollHeight - 20;
      log.textContent += snapshot.lines.join("\n") + "\n";
      if (atBottom) log.scrollTop = log.scrollHeight;
    }
    renderProgress(snapshot);
    if (snapshot.state === "running") {
      state.pollTimer = setTimeout(pollJob, 700);
      return;
    }
    setRunning(false);
    if (snapshot.state === "succeeded" && ["configure", "start"].includes(snapshot.command)) {
      refreshViewer(snapshot.command === "start");
    }
  } catch (error) {
    setStatus($("#progress-text"), error.message, "error");
    setRunning(false);
  }
}

function setRunning(running) {
  for (const button of document.querySelectorAll("[data-command]")) button.disabled = running;
}

async function runCommand(command) {
  const id = $("#run-composition").value;
  if (!id) return;
  clearTimeout(state.pollTimer);
  $("#log").textContent = `$ ${command} --composition ${id}\n`;
  $("#progress-bar").style.width = "0";
  setRunning(true);
  try {
    const job = await api("POST", `compositions/${id}/${command}`);
    state.job = { id: job.id, lineCount: 0 };
    pollJob();
  } catch (error) {
    setStatus($("#progress-text"), error.message, "error");
    setRunning(false);
  }
}

// --- Start ----------------------------------------------------------------------------

async function main() {
  for (const button of document.querySelectorAll(".tabs button")) button.addEventListener("click", () => showTab(button.dataset.tab));
  $("#new-composition").addEventListener("click", newComposition);
  // Keep the model current so re-rendering the editor never loses an edit.
  $("#composition-id").addEventListener("input", (event) => {
    if (state.current) state.current.id = event.target.value.trim();
  });
  $("#world-select").addEventListener("change", (event) => {
    if (state.current) state.current.composition.world = event.target.value;
  });
  $("#add-vehicle").addEventListener("click", addVehicle);
  $("#save-composition").addEventListener("click", saveComposition);
  $("#run-composition").addEventListener("change", refreshRunPlan);
  for (const button of document.querySelectorAll("[data-command]")) button.addEventListener("click", () => runCommand(button.dataset.command));

  state.assets = await api("GET", "assets");
  renderAssets();
  await loadCompositions();
  if (state.compositions.length) await openComposition(state.compositions[0].id);
  else newComposition();
  let tab = "compose";
  try { tab = localStorage.getItem("urban-studio-tab") || tab; } catch { /* storage may be unavailable */ }
  showTab(tab);
}

main().catch((error) => {
  document.body.prepend(el("p", { class: "status error", style: "padding: 12px 20px" }, `Urban Studio を読み込めませんでした: ${error.message}`));
});

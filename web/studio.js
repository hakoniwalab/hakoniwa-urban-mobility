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
  selected: 0,   // index of the selected vehicle in the placement views
  world: null,   // /api/worlds/<id> of the current Composition
  placement: null,
  map: null,
  view: "three",
  job: null,
  pollTimer: null,
  cities: null,      // /api/cities
  cityTimer: null,
  rtfTimer: null,
};

// Placement-only vehicle fields (the ground height under the vehicle) are
// prefixed with "_" and never saved.
const withoutTransient = (vehicle) =>
  Object.fromEntries(Object.entries(vehicle).filter(([key]) => !key.startsWith("_")));
const round2 = (value) => Math.round(value * 100) / 100;

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
  if (name === "city") pollCities();
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
  // Fleet-only Assets are placed as fleets, not one vehicle at a time.
  $("#add-asset").replaceChildren(...vehicles().filter((vehicle) => !vehicle.fleet).map((vehicle) => el("option", { value: vehicle.id }, vehicle.title)));
}

// --- City World Web UI -------------------------------------------------------------------

function registrationText(job) {
  const registration = job.registration;
  if (!job.finished) return ["生成中（または未完了）", ""];
  if (job.registered && (!registration || registration.state === "succeeded")) return ["登録済み", "ok"];
  if (!registration) return ["未登録", ""];
  if (registration.state === "running") {
    const progress = registration.progress;
    const detail = progress?.percent !== undefined ? ` ${Math.round(progress.percent)}%`
      : progress?.elapsed_sec !== undefined ? ` ${progress.elapsed_sec} 秒経過` : "";
    return [`登録中（高さモデルを準備中${detail}）`, ""];
  }
  return registration.state === "succeeded" ? ["登録済み", "ok"] : [`登録に失敗しました（終了コード ${registration.exit_code}）`, "error"];
}

function renderCities() {
  const cities = state.cities;
  if (!cities) return;
  const body = $("#city-job-table tbody");
  body.replaceChildren(...(cities.jobs.length ? cities.jobs.map((job) => {
    const [text, kind] = registrationText(job);
    const cell = el("td", { class: `city-state ${kind}` }, text);
    if (kind === "error") {
      cell.append(el("details", {}, el("summary", {}, "出力"), el("pre", {}, job.registration.lines.slice(-20).join("\n"))));
    }
    return el("tr", {}, el("td", {}, el("code", {}, job.id)), cell);
  }) : [el("tr", {}, el("td", { colspan: 2, class: "hint" }, "City World Web UI のジョブはまだありません"))]));

  const web = cities.web_ui;
  const starting = web.job?.state === "running";
  $("#city-new").disabled = starting;
  $("#city-new").textContent = web.running ? "City World Web UI を開く" : "新規作成";
  $("#city-stop").hidden = !web.running;
  const status = $("#city-web-status");
  if (starting) setStatus(status, web.job.command === "start" ? "City World Web UI を起動しています…" : "City World Web UI を停止しています…");
  else if (web.running) setStatus(status, `City World Web UI は起動中です: ${web.url}`, "ok");
  else if (web.job?.state === "failed") setStatus(status, `City World Web UI の${web.job.command === "start" ? "起動" : "停止"}に失敗しました（終了コード ${web.job.exit_code}）`, "error");
  else setStatus(status, "");
  const log = $("#city-web-log");
  log.hidden = !web.job || (web.job.state === "succeeded" && !starting);
  if (web.job) log.textContent = web.job.lines.join("\n");
}

async function pollCities() {
  clearTimeout(state.cityTimer);
  try {
    const previous = state.cities;
    state.cities = await api("GET", "cities");
    // A registration that finished since the last poll adds a World: reload the catalog.
    const wasRegistering = new Set((previous?.jobs || [])
      .filter((job) => job.registration?.state === "running").map((job) => job.id));
    if (state.cities.jobs.some((job) => wasRegistering.has(job.id) && job.registration?.state === "succeeded")) {
      state.assets = await api("GET", "assets");
      renderAssets();
    }
    renderCities();
  } catch (error) {
    setStatus($("#city-web-status"), error.message, "error");
  }
  const busy = state.cities?.web_ui.job?.state === "running"
    || state.cities?.jobs.some((job) => job.registration?.state === "running");
  const visible = !$("#tab-city").hidden;
  state.cityTimer = setTimeout(pollCities, busy || visible ? 2000 : 10000);
}

async function waitForJob(id) {
  for (;;) {
    const job = await api("GET", `jobs/${id}`);
    if (job.state !== "running") return job;
    await new Promise((resolve) => setTimeout(resolve, 700));
  }
}

async function newCity() {
  const web = state.cities?.web_ui;
  if (web?.running) {
    window.open(web.url, "_blank");
    return;
  }
  // Open the tab now, inside the click, so a popup blocker allows it.
  const tab = window.open("", "_blank");
  tab?.document.write("<p style='font-family: sans-serif'>City World Web UI を起動しています…</p>");
  try {
    const job = await api("POST", "cities/web-ui/start");
    pollCities();
    const finished = await waitForJob(job.id);
    await pollCities();
    if (finished.state === "succeeded" && state.cities.web_ui.running) {
      if (tab) tab.location.href = state.cities.web_ui.url;
      else window.open(state.cities.web_ui.url, "_blank");
    } else {
      tab?.close();
    }
  } catch (error) {
    tab?.close();
    setStatus($("#city-web-status"), error.message, "error");
  }
}

async function stopCityWebUi() {
  try {
    const job = await api("POST", "cities/web-ui/stop");
    pollCities();
    await waitForJob(job.id);
  } catch (error) {
    setStatus($("#city-web-status"), error.message, "error");
  }
  pollCities();
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
  state.selected = 0;
  renderEditor();
  renderCompositionList();
  setStatus($("#compose-status"), state.current.editable ? "" : "例の Composition です。保存するとコピーが作られます。");
  loadWorld();
}

// --- Placement ------------------------------------------------------------------------

function vehicleKind(asset) {
  if (!asset) return "other";
  return asset.id.includes("fpv") ? "fpv" : asset.category;
}

function placementVehicles() {
  return (state.current?.composition.vehicles || []).map((vehicle) => {
    const asset = assetById(vehicle.asset);
    const clearance = Number(asset?.ground_clearance_m ?? 0);
    return {
      name: vehicle.name, kind: vehicleKind(asset), clearance,
      east: Number(vehicle.spawn?.east_m ?? 0), north: Number(vehicle.spawn?.north_m ?? 0),
      yaw: Number(vehicle.spawn?.yaw_deg ?? 0), up: Number(vehicle._ground ?? 0) + clearance,
    };
  });
}

function refreshPlacement() {
  const vehicles = placementVehicles();
  state.placement?.setVehicles(vehicles, state.selected);
  state.map?.setVehicles(vehicles, state.selected);
  const selected = state.current?.composition.vehicles[state.selected];
  $("#placement-selected").textContent = selected
    ? `選択中: ${selected.name}（east ${selected.spawn.east_m} m, north ${selected.spawn.north_m} m, yaw ${selected.spawn.yaw_deg}°）`
    : "";
}

async function fetchHeight(vehicle) {
  const world = state.current?.composition.world;
  if (!world || !vehicle.spawn) return;
  try {
    const { ground_m: ground, rooftops } = await api(
      "GET", `worlds/${world}/height?east=${vehicle.spawn.east_m}&north=${vehicle.spawn.north_m}`);
    vehicle._ground = ground;
    vehicle._rooftops = rooftops;
    vehicle._heightError = false;
  } catch {
    vehicle._ground = undefined;
    vehicle._heightError = true;
  }
}

async function loadWorld() {
  const worldId = state.current?.composition.world;
  if (!worldId) return;
  try {
    state.world = await api("GET", `worlds/${worldId}`);
  } catch (error) {
    state.world = null;
    state.placement?.clearWorld();
    $('#placement-views [data-view="map"]').disabled = true;
    if (state.view === "map") showPlacementView("three");
    const missing = !assetById(worldId);
    setStatus($("#compose-status"), missing
      ? `World「${worldId}」は登録されていません。World を選び直すか、City タブで作成・登録してください。`
      : `World を読み込めません: ${error.message}`, "error");
    for (const vehicle of state.current.composition.vehicles) {
      vehicle._ground = undefined;
      vehicle._heightError = true;
    }
    renderEditor();
    return;
  }
  if ($("#compose-status").classList.contains("error")) setStatus($("#compose-status"), "");
  const mapButton = $('#placement-views [data-view="map"]');
  mapButton.disabled = !state.world.map;
  if (!state.world.map && state.view === "map") showPlacementView("three");
  state.placement?.setWorld(state.world).catch((error) =>
    setStatus($("#compose-status"), `World の表示に失敗しました: ${error.message}`, "error"));
  if (state.world.map) state.map?.setWorld(state.world);
  refreshPlacement();
  await Promise.all(state.current.composition.vehicles.map(fetchHeight));
  renderEditor();
}

function showPlacementView(view) {
  state.view = view;
  for (const button of document.querySelectorAll("#placement-views button")) {
    button.setAttribute("aria-selected", String(button.dataset.view === view));
  }
  $("#placement-three").hidden = view !== "three";
  $("#placement-map").hidden = view !== "map";
  if (view === "map") {
    if (state.world?.map) state.map?.setWorld(state.world);
    state.map?.show();
    refreshPlacement();
  }
}

function selectVehicle(index) {
  if (index === state.selected) return;
  state.selected = index;
  renderEditor();
}

async function moveVehicle(index, east, north) {
  const vehicle = state.current?.composition.vehicles[index];
  if (!vehicle) return;
  state.selected = index;
  vehicle.spawn = { ...vehicle.spawn, east_m: round2(east), north_m: round2(north) };
  vehicle._ground = undefined;
  vehicle._heightError = false;
  renderEditor();
  await fetchHeight(vehicle);
  renderEditor();
}

function setYaw(index, degrees) {
  const vehicle = state.current?.composition.vehicles[index];
  if (!vehicle) return;
  vehicle.spawn.yaw_deg = ((Math.round(degrees) + 540) % 360) - 180;
  renderEditor();
}

function turnSelected(degrees) {
  const vehicle = state.current?.composition.vehicles[state.selected];
  if (!vehicle) return;
  vehicle.spawn.yaw_deg = ((Number(vehicle.spawn.yaw_deg) + degrees + 540) % 360) - 180;
  renderEditor();
}

async function initPlacement() {
  try {
    const { PlacementView } = await import("./placement.js");
    state.placement = new PlacementView($("#placement-three"), { onSelect: selectVehicle, onMove: moveVehicle, onTurn: setYaw });
  } catch (error) {
    $("#placement-three").replaceChildren(el("p", { class: "hint", style: "padding: 12px" },
      `3D 表示を読み込めませんでした（three.js の取得にネットワークが必要です）: ${error.message}`));
  }
  const { MapView } = await import("./map.js");
  state.map = new MapView($("#placement-map"), {
    onSelect: selectVehicle,
    onPick: (east, north) => moveVehicle(state.selected, east, north),
    onMove: moveVehicle,
  });
  if (!window.L) $('#placement-views [data-view="map"]').disabled = true;
}

function newComposition() {
  const world = worlds().find((item) => item.kind === "plain") || worlds()[0];
  state.current = {
    id: "", editable: true,
    composition: { schema: "hakoniwa.composition/v1", id: "", world: world?.id, vehicles: [] },
  };
  state.selected = 0;
  renderEditor();
  renderCompositionList();
  setStatus($("#compose-status"), "");
  $("#composition-id").focus();
  loadWorld();
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
  state.selected = list.length - 1;
  renderEditor();
  fetchHeight(list[state.selected]).then(renderEditor);
}

function numberInput(label, value, onchange, step = "0.1") {
  return el("label", { class: "field" }, label,
    el("input", { type: "number", step, value: String(value ?? 0), onchange: (event) => onchange(Number(event.target.value)) }));
}

function spawnInput(vehicle, label, key, step) {
  return numberInput(label, vehicle.spawn?.[key], async (value) => {
    vehicle.spawn[key] = value;
    if (key !== "yaw_deg") {
      vehicle._ground = undefined;
      vehicle._heightError = false;
      await fetchHeight(vehicle);
    }
    renderEditor();
  }, step);
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

  const clearance = Number(asset?.ground_clearance_m ?? 0);
  const height = el("span", { class: "height" }, vehicle._heightError ? "高さ: 不明"
    : vehicle._ground === undefined ? "高さ: 計算中…"
    : `地面 ${vehicle._ground.toFixed(2)} m + ${clearance} m = ${(vehicle._ground + clearance).toFixed(2)} m`
      + (vehicle._rooftops === false ? "（屋上は未考慮）" : ""));
  return el("div", {
    class: `vehicle${index === state.selected ? " selected" : ""}`,
    onclick: (event) => { if (!event.target.closest("input, select, button")) selectVehicle(index); },
  },
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
      spawnInput(vehicle, "east (m)", "east_m", "0.1"),
      spawnInput(vehicle, "north (m)", "north_m", "0.1"),
      spawnInput(vehicle, "yaw (°)", "yaw_deg", "1"),
      height,
      mirror,
      el("button", {
        class: "icon", title: "削除", "aria-label": `${vehicle.name} を削除`,
        onclick: () => {
          composition.vehicles.splice(index, 1);
          composition.interactions = (composition.interactions || []).filter((item) => item.drone !== vehicle.name);
          state.selected = Math.max(0, Math.min(state.selected, composition.vehicles.length - 1));
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
  const worldSelect = $("#world-select");
  worldSelect.querySelector("option[data-missing]")?.remove();
  if (composition.world && !assetById(composition.world)) {
    worldSelect.prepend(el("option", { value: composition.world, "data-missing": true }, `（未登録）${composition.world}`));
  }
  worldSelect.value = composition.world;
  $("#vehicles").replaceChildren(...(composition.vehicles.length
    ? composition.vehicles.map(renderVehicle)
    : [el("p", { class: "hint" }, "車両を追加してください")]));
  refreshPlacement();
}

function setStatus(node, message, kind = "") {
  node.textContent = message;
  node.className = `status ${kind}`;
}

async function saveComposition() {
  const current = state.current;
  if (!current) return;
  const id = current.id;
  const composition = { ...current.composition, vehicles: current.composition.vehicles.map(withoutTransient) };
  if (!composition.interactions?.length) delete composition.interactions;
  for (const vehicle of composition.vehicles) if (vehicle.params && !Object.keys(vehicle.params).length) delete vehicle.params;
  const status = $("#compose-status");
  setStatus(status, "検証中…");
  try {
    const plan = await api("PUT", `compositions/${id}`, composition);
    state.current = { id, editable: true, composition: { ...current.composition, id } };
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

// Real-time factor from the running simulation's pacer (GET .../rtf).
const RTF_SLOW = 0.95;       // below this the simulation cannot keep up
const RTF_STALE_SEC = 15;    // no report for this long: not running (the pacer reports every 2 s)

async function pollRtf() {
  clearTimeout(state.rtfTimer);
  const id = $("#run-composition").value;
  const node = $("#rtf");
  if (id && !$("#tab-simulation").hidden) {
    try {
      const { report } = await api("GET", `compositions/${id}/rtf`);
      if (!report) {
        node.textContent = "";
      } else if (report.age_sec > RTF_STALE_SEC) {
        node.className = "rtf";
        node.textContent = `rtf ${report.rtf.toFixed(3)}（最終: sim ${report.sim_sec.toFixed(1)} s / 実時間 ${report.wall_sec.toFixed(1)} s・停止中）`;
      } else {
        const idle = report.idle_percent;
        node.className = `rtf ${report.rtf < RTF_SLOW ? "slow" : "ok"}`;
        node.textContent = `rtf ${report.rtf.toFixed(3)}（sim ${report.sim_sec.toFixed(1)} s / 実時間 ${report.wall_sec.toFixed(1)} s）`
          + (idle === null ? "" : `  余裕 ${idle.toFixed(0)}%`)
          + (report.rtf < RTF_SLOW ? "  実時間に追いついていません" : idle !== null && idle < 10 ? "  限界に近い" : "");
      }
    } catch {
      node.textContent = "";
    }
  }
  state.rtfTimer = setTimeout(pollRtf, 2000);
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
    if (!state.current) return;
    state.current.composition.world = event.target.value;
    for (const vehicle of state.current.composition.vehicles) { vehicle._ground = undefined; vehicle._heightError = false; }
    renderEditor();
    loadWorld();
  });
  for (const button of document.querySelectorAll("#placement-views button")) {
    button.addEventListener("click", () => showPlacementView(button.dataset.view));
  }
  $("#focus-selected").addEventListener("click", () => {
    showPlacementView("three");
    state.placement?.focusVehicle(state.selected);
  });
  $("#focus-overview").addEventListener("click", () => {
    showPlacementView("three");
    state.placement?.overview();
  });
  $("#yaw-left").addEventListener("click", () => turnSelected(15));
  $("#yaw-right").addEventListener("click", () => turnSelected(-15));
  $("#add-vehicle").addEventListener("click", addVehicle);
  $("#city-new").addEventListener("click", newCity);
  $("#city-stop").addEventListener("click", stopCityWebUi);
  $("#save-composition").addEventListener("click", saveComposition);
  $("#run-composition").addEventListener("change", () => { refreshRunPlan(); pollRtf(); });
  for (const button of document.querySelectorAll("[data-command]")) button.addEventListener("click", () => runCommand(button.dataset.command));

  state.assets = await api("GET", "assets");
  renderAssets();
  await initPlacement();
  await loadCompositions();
  if (state.compositions.length) await openComposition(state.compositions[0].id);
  else newComposition();
  let tab = "compose";
  try { tab = localStorage.getItem("urban-studio-tab") || tab; } catch { /* storage may be unavailable */ }
  showTab(tab);
  if (tab !== "city") pollCities(); // keeps registering finished Cities while another tab is open
  pollRtf();
}

main().catch((error) => {
  document.body.prepend(el("p", { class: "status error", style: "padding: 12px 20px" }, `Urban Studio を読み込めませんでした: ${error.message}`));
});

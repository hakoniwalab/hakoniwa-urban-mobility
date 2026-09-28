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
  savedSnapshot: null, // snapshot() of state.current as last opened or saved; null if never saved
  scenarios: [],   // /api/scenarios (Car route scenarios)
  route: null,     // { id, editable, scenario } open in the Route tab
  routePoint: -1,  // selected route point
  routeMap: null,
};

// --- Composition identity shared by Compose and Simulation ----------------------------

const LAST_COMPOSITION_KEY = "urban-studio-composition";

function rememberComposition(id) {
  try { if (id) localStorage.setItem(LAST_COMPOSITION_KEY, id); } catch { /* storage may be unavailable */ }
}

function rememberedComposition() {
  try { return localStorage.getItem(LAST_COMPOSITION_KEY); } catch { return null; }
}

// The Composition a save writes: no placement-only fields, and no empty
// params / interactions that the editor adds while rendering.
function savedForm(composition) {
  const result = { ...composition, vehicles: composition.vehicles.map(withoutTransient) };
  if (!result.interactions?.length) delete result.interactions;
  for (const vehicle of result.vehicles) if (vehicle.params && !Object.keys(vehicle.params).length) delete vehicle.params;
  return result;
}

function snapshot(current) {
  return current ? JSON.stringify({ id: current.id, composition: savedForm(current.composition) }) : null;
}

// Simulation runs the saved file, so an edit in Compose is not used until saved.
const isDirty = () => Boolean(state.current) && snapshot(state.current) !== state.savedSnapshot;

// "Golf Cart×2・EAMS Hexa×1" (fleets count their drones).
function compositionMakeup(item) {
  const counts = new Map();
  for (const vehicle of item.vehicle_list || []) counts.set(vehicle.title, (counts.get(vehicle.title) || 0) + 1);
  for (const fleet of item.fleets || []) counts.set(fleet.title, (counts.get(fleet.title) || 0) + Number(fleet.count || 0));
  return [...counts].map(([title, count]) => `${title}×${count}`).join("・") || "車両なし";
}

const worldKindLabel = (kind) => (kind === "city" ? "City" : kind === "plain" ? "プレーン" : "未登録");

function compositionLabel(item) {
  return `${item.id} — ${item.world_title}（${worldKindLabel(item.world_kind)}）／${compositionMakeup(item)}${item.editable ? "" : "・例"}`;
}

// Point the Simulation selector at a Composition when it is listed (saved).
function selectRunComposition(id) {
  const select = $("#run-composition");
  if (id && [...select.options].some((option) => option.value === id)) select.value = id;
}

// Placement-only vehicle fields (the ground height under the vehicle) are
// prefixed with "_" and never saved.
const withoutTransient = (vehicle) =>
  Object.fromEntries(Object.entries(vehicle).filter(([key]) => !key.startsWith("_")));
const round2 = (value) => Math.round(value * 100) / 100;

const assetById = (id) => state.assets.find((asset) => asset.id === id);
const vehicles = () => state.assets.filter((asset) => asset.kind === "vehicle");
const worlds = () => state.assets.filter((asset) => asset.kind === "city" || asset.kind === "plain");
// A City whose City World receipt is gone is listed but cannot be selected.
const usableWorlds = () => worlds().filter((world) => world.available !== false);
const MISSING_RECEIPT = "receipt がありません（City World のジョブが削除されています）";

// --- Tabs ---------------------------------------------------------------------------

function showTab(name) {
  for (const button of document.querySelectorAll(".tabs button")) {
    button.setAttribute("aria-selected", String(button.dataset.tab === name));
  }
  for (const section of document.querySelectorAll(".tab")) section.hidden = section.id !== `tab-${name}`;
  try { localStorage.setItem("urban-studio-tab", name); } catch { /* storage may be unavailable */ }
  if (name === "simulation") {
    // Follow the Composition open in Compose, so Simulation never runs a different one by surprise.
    selectRunComposition(state.current?.id);
    renderUnsavedNotice();
    refreshRunPlan();
    pollRtf();
  }
  if (name === "city") { pollCities(); loadCache(); }
  if (name === "route") {
    if (!state.route) {
      const first = state.scenarios.find((item) => item.world === state.current?.composition.world);
      if (first) openRoute(first.id); else newRoute();
    } else loadRouteWorld();
  }
}

// --- Assets and City ----------------------------------------------------------------

function assetCard(preview, title, meta) {
  return el("div", { class: "asset-card" }, preview,
    el("div", { class: "asset-card-body" },
      el("div", { class: "asset-card-title" }, title),
      el("div", { class: "asset-card-meta" }, ...meta.map((line) => el("div", {}, line)))));
}

async function mountAssetPreviews(items) {
  const previews = await import("./asset_preview.js");
  for (const [container, asset] of items) {
    try {
      if (asset.kind === "vehicle") {
        if (!asset.preview) previews.showPreviewMessage(container, "プレビューなし");
        else await previews.mountVehiclePreview(container, await api("GET", `assets/${asset.id}/preview`));
      } else if (asset.available === false) {
        previews.showPreviewMessage(container, MISSING_RECEIPT);
      } else {
        const info = await api("GET", `worlds/${asset.id}`);
        if (asset.kind === "city") {
          previews.drawCityPreview(container, await api("GET", `worlds/${asset.id}/footprints`), info.half_extent_m);
        } else {
          await previews.mountWorldPreview(container, info.glb);
        }
      }
    } catch (error) {
      console.warn(`[Studio] preview of ${asset.id} failed:`, error);
      previews.showPreviewMessage(container, "プレビューを表示できません");
    }
  }
}

function renderAssets() {
  const previews = [];
  const preview = (asset) => {
    const container = el("div", { class: "asset-preview" });
    previews.push([container, asset]);
    return container;
  };
  $("#world-cards").replaceChildren(...worlds().map((world) => assetCard(preview(world),
    world.available === false ? `${world.title}（${MISSING_RECEIPT}）` : world.title,
    [el("code", {}, world.id), world.kind === "city" ? "City" : "プレーン"])));
  $("#vehicle-cards").replaceChildren(...vehicles().map((vehicle) => assetCard(preview(vehicle), vehicle.title, [
    el("code", {}, vehicle.id),
    `${vehicle.category} ・ ${vehicle.simulator}`,
    `制御: ${Object.keys(vehicle.controls).join(" / ")} ・ 地上高 ${vehicle.ground_clearance_m} m`,
  ])));
  mountAssetPreviews(previews);
  const cityBody = $("#city-table tbody");
  const cities = state.assets.filter((asset) => asset.kind === "city");
  cityBody.replaceChildren(...(cities.length ? cities.map((city) => el("tr", {},
    el("td", {}, el("code", {}, city.id)), el("td", {}, el("code", {}, city.path)),
    city.available === false
      ? el("td", { class: "city-state error" }, MISSING_RECEIPT)
      : el("td", { class: "city-state ok" }, "利用可能"))) :
    [el("tr", {}, el("td", { colspan: 3, class: "hint" }, "登録済みの City はありません"))]));

  const worldSelect = $("#world-select");
  const groups = [["City", "city"], ["プレーン", "plain"]].map(([label, kind]) => el("optgroup", { label },
    ...usableWorlds().filter((world) => world.kind === kind).map((world) => el("option", { value: world.id }, `${world.title} (${world.id})`))));
  worldSelect.replaceChildren(...groups);
  // A fleet-capable Asset is added as a fleet (many drones in a grid).
  $("#add-asset").replaceChildren(...vehicles().map((vehicle) => el("option", { value: vehicle.id }, vehicle.title)));
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
    // So does a City unregistered because its City World job was deleted.
    if (state.cities.unregistered?.length
      || state.cities.jobs.some((job) => wasRegistering.has(job.id) && job.registration?.state === "succeeded")) {
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

// --- Caches ------------------------------------------------------------------------------

function formatBytes(value) {
  const units = ["B", "KiB", "MiB", "GiB"];
  let size = value;
  let unit = 0;
  while (size >= 1024 && unit < units.length - 1) { size /= 1024; unit += 1; }
  return unit === 0 ? `${size} B` : `${size.toFixed(1)} ${units[unit]}`;
}

function renderCache() {
  const cache = state.cache;
  if (!cache) return;
  const urban = cache.urban;
  const height = urban.world_height;
  const plain = urban.plain_world;
  const sum = (entries, removable) => entries.reduce((total, entry) => total + (removable && !entry.remove ? 0 : entry.size_bytes), 0);
  const city = cache.city_world;
  const rows = [
    ["Urban 高さモデル (world-height)", formatBytes(sum(height, false)), formatBytes(sum(height, true))],
    ["Urban プレーン World (plain-world)", formatBytes(sum(plain, false)), formatBytes(sum(plain, true))],
    city.available
      ? ["City World PLATEAU ダウンロード", formatBytes(city.shared_cache_bytes), `${formatBytes(city.reclaimable_bytes.both)}（Business Pack で整理）`]
      : ["City World PLATEAU ダウンロード", "取得できません", "—"],
  ];
  $("#cache-table tbody").replaceChildren(...rows.map((cells) => el("tr", {}, ...cells.map((cell) => el("td", {}, cell)))));
  $("#cache-prune").disabled = cache.prune_job?.state === "running" || urban.reclaimable_bytes === 0;
  const details = $("#cache-details");
  details.hidden = height.length === 0;
  $("#cache-entry-table tbody").replaceChildren(...height.map((entry) => el("tr", {},
    el("td", {}, entry.remove ? "削除" : "保持"),
    el("td", {}, formatBytes(entry.size_bytes)),
    el("td", {}, entry.reason),
    el("td", {}, el("code", {}, entry.mjcf || entry.path.split(/[\\/]/).pop())))));
  const status = $("#cache-status");
  if (!city.available) setStatus(status, `City World の容量は ${city.command} で確認・整理してください。`);
  else if (city.reclaimable_bytes.both > 0) setStatus(status, `City World の PLATEAU ダウンロードを整理するには Business Pack で「${city.command}」を実行してください（既定は dry run、--apply で削除）。`);
  else setStatus(status, "");
}

async function loadCache() {
  try {
    state.cache = await api("GET", "cache");
    renderCache();
  } catch (error) {
    setStatus($("#cache-status"), error.message, "error");
  }
}

async function pruneCache() {
  const urban = state.cache?.urban;
  if (!urban) return;
  const count = [...urban.world_height, ...urban.plain_world].filter((entry) => entry.remove).length;
  if (!window.confirm(`Urban キャッシュ ${count} 件（${formatBytes(urban.reclaimable_bytes)}）を削除しますか？\n登録済み City の World と City World のジョブは削除しません。`)) return;
  $("#cache-prune").disabled = true;
  try {
    const job = await api("POST", "cache/prune");
    const finished = await waitForJob(job.id);
    await loadCache();
    if (finished.state === "succeeded") setStatus($("#cache-status"), `Urban キャッシュを整理しました（${formatBytes(urban.reclaimable_bytes)}）`, "ok");
    else setStatus($("#cache-status"), `Urban キャッシュの整理に失敗しました（終了コード ${finished.exit_code}）: ${finished.lines.slice(-3).join(" ")}`, "error");
  } catch (error) {
    setStatus($("#cache-status"), error.message, "error");
    renderCache();
  }
}

// --- Composition list and editor -------------------------------------------------------

function renderCompositionList() {
  const list = $("#composition-list");
  list.replaceChildren(...state.compositions.map((item) => el("li", {},
    el("button", {
      "aria-current": String(state.current?.id === item.id),
      onclick: () => { if (confirmDiscard()) openComposition(item.id); },
    }, item.id, el("span", { class: "meta" }, `${item.vehicles} 台${item.editable ? "" : "・例"}`)))));
  const runSelect = $("#run-composition");
  const selected = runSelect.value;
  runSelect.replaceChildren(...state.compositions.map((item) => el("option", { value: item.id }, compositionLabel(item))));
  selectRunComposition(selected);
}

async function loadCompositions() {
  state.compositions = await api("GET", "compositions");
  renderCompositionList();
}

async function openComposition(id) {
  state.current = await api("GET", `compositions/${id}`);
  // A fleet-only Composition may have no vehicles list.
  state.current.composition.vehicles = state.current.composition.vehicles || [];
  state.savedSnapshot = snapshot(state.current);
  state.selected = 0;
  renderEditor();
  renderCompositionList();
  rememberComposition(id);
  selectRunComposition(id);
  setStatus($("#compose-status"), state.current.editable ? "" : "例の Composition です。保存するとコピーが作られます。");
  loadWorld();
}

// Opening another Composition drops unsaved edits of the current one.
function confirmDiscard() {
  return !isDirty() || window.confirm(`Compose の「${state.current.id || "新しい Composition"}」には未保存の変更があります。破棄して開きますか？`);
}

// --- Placement ------------------------------------------------------------------------

function vehicleKind(asset) {
  if (!asset) return "other";
  return asset.id.includes("fpv") ? "fpv" : asset.category;
}

// --- Route Cars in the placement views ------------------------------------------------
// An api Car on a route starts at the route start set back by its offset,
// facing along the route (tools/urban_composition.py _assign_route_cars and
// _route_start_spawn). The placement views show that pose, not the placed spawn.

const ROUTE_SPACING_M = 6.0; // urban_composition.ADDED_CAR_SPACING_M

function routeReferenceOf(vehicle) {
  return vehicle.control === "api" ? vehicle.params?.scenario : undefined;
}

// The same geometry as route_geometry.RouteGeometry.sample / heading_rad.
function routeSample(points, distance) {
  const segments = points.map((point, index) => {
    const next = points[(index + 1) % points.length];
    return { start: point, end: next, length: Math.hypot(next.east_m - point.east_m, next.north_m - point.north_m) };
  });
  const total = segments.reduce((sum, segment) => sum + segment.length, 0);
  if (!(total > 0)) return null;
  let remaining = ((distance % total) + total) % total;
  for (const segment of segments) {
    if (remaining <= segment.length || segment === segments.at(-1)) {
      const ratio = segment.length > 0 ? remaining / segment.length : 0;
      return {
        east: segment.start.east_m + ratio * (segment.end.east_m - segment.start.east_m),
        north: segment.start.north_m + ratio * (segment.end.north_m - segment.start.north_m),
        yaw: Math.atan2(segment.end.north_m - segment.start.north_m, segment.end.east_m - segment.start.east_m) * 180 / Math.PI,
      };
    }
    remaining -= segment.length;
  }
  return null;
}

// Offsets as the builder assigns them: route offsets for named Cars, unnamed
// Cars 6 m behind, the leading Car at 0.
function routeOffsets(scenario, names) {
  const listed = new Map((scenario.vehicles || []).map((item) => [item.name, Number(item.route_offset_m || 0)]));
  const offsets = new Map();
  for (const name of names) if (listed.has(name)) offsets.set(name, listed.get(name));
  for (const name of names) {
    if (offsets.has(name)) continue;
    offsets.set(name, offsets.size ? Math.min(...offsets.values()) - ROUTE_SPACING_M : 0);
  }
  const lead = Math.max(...offsets.values());
  return new Map([...offsets].map(([name, offset]) => [name, offset - lead]));
}

async function loadRouteStarts() {
  const vehicles = state.current?.composition.vehicles || [];
  state.routeData = state.routeData || {};
  const references = [...new Set(vehicles.map(routeReferenceOf).filter(Boolean))];
  await Promise.all(references.map(async (reference) => {
    if (reference in state.routeData) return;
    const item = state.scenarios.find((scenario) => scenario.reference === reference);
    try {
      state.routeData[reference] = item ? (await api("GET", `scenarios/${item.id}`)).scenario : null;
    } catch {
      state.routeData[reference] = null;
    }
  }));
  for (const vehicle of vehicles) vehicle._routeStart = undefined;
  for (const reference of references) {
    const scenario = state.routeData[reference];
    const points = scenario?.route?.points || [];
    if (points.length < 3) continue;
    const riders = vehicles.filter((vehicle) => routeReferenceOf(vehicle) === reference);
    const offsets = routeOffsets(scenario, riders.map((vehicle) => vehicle.name));
    for (const vehicle of riders) vehicle._routeStart = routeSample(points, offsets.get(vehicle.name));
  }
}

function composedRoutes() {
  const vehicles = state.current?.composition.vehicles || [];
  return [...new Set(vehicles.map(routeReferenceOf).filter(Boolean))]
    .map((reference) => state.routeData?.[reference]?.route?.points || [])
    .filter((points) => points.length >= 3);
}

// After a route or control change: move route Cars to their route starts.
async function refreshRouteStarts() {
  await loadRouteStarts();
  refreshPlacement();
  await Promise.all((state.current?.composition.vehicles || []).map(fetchHeight));
  renderEditor();
}

function routeLocked(vehicle) {
  if (!vehicle?._routeStart) return false;
  setStatus($("#compose-status"), `${vehicle.name} はルートの開始点から出発します。位置を変えるには Route タブでルートを編集してください。`);
  return true;
}

function placementVehicles() {
  return (state.current?.composition.vehicles || []).map((vehicle) => {
    const asset = assetById(vehicle.asset);
    const clearance = Number(asset?.ground_clearance_m ?? 0);
    const start = vehicle._routeStart;
    return {
      name: vehicle.name, kind: vehicleKind(asset), clearance,
      east: start ? start.east : Number(vehicle.spawn?.east_m ?? 0),
      north: start ? start.north : Number(vehicle.spawn?.north_m ?? 0),
      yaw: start ? start.yaw : Number(vehicle.spawn?.yaw_deg ?? 0),
      up: Number(vehicle._ground ?? 0) + clearance,
    };
  });
}

function refreshPlacement() {
  const vehicles = placementVehicles();
  state.placement?.setVehicles(vehicles, state.selected);
  state.map?.setVehicles(vehicles, state.selected);
  const fleetPoints = (state.current?.composition.fleets || []).flatMap(fleetGrid);
  state.placement?.setFleets(fleetPoints);
  state.map?.setFleets(fleetPoints);
  state.map?.setRoutes(composedRoutes());
  const selected = state.current?.composition.vehicles[state.selected];
  const start = selected?._routeStart;
  $("#placement-selected").textContent = !selected ? ""
    : start
      ? `選択中: ${selected.name}（ルートの開始点から出発: east ${round2(start.east)} m, north ${round2(start.north)} m, yaw ${Math.round(start.yaw)}°）`
      : `選択中: ${selected.name}（east ${selected.spawn.east_m} m, north ${selected.spawn.north_m} m, yaw ${selected.spawn.yaw_deg}°）`;
}

async function fetchHeight(vehicle) {
  const world = state.current?.composition.world;
  if (!world || !vehicle.spawn) return;
  const east = vehicle._routeStart ? vehicle._routeStart.east : vehicle.spawn.east_m;
  const north = vehicle._routeStart ? vehicle._routeStart.north : vehicle.spawn.north_m;
  try {
    const { ground_m: ground, rooftops } = await api("GET", `worlds/${world}/height?east=${east}&north=${north}`);
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
  if (state.world.map) {
    state.map?.setWorld(state.world);
    state.footprints = state.footprints || {};
    if (!(worldId in state.footprints)) {
      try { state.footprints[worldId] = (await api("GET", `worlds/${worldId}/footprints`)).buildings; }
      catch { state.footprints[worldId] = []; }
    }
    state.map?.setFootprints(state.footprints[worldId]);
  }
  await loadRouteStarts();
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
  if (routeLocked(vehicle)) { renderEditor(); return; }
  vehicle.spawn = { ...vehicle.spawn, east_m: round2(east), north_m: round2(north) };
  vehicle._ground = undefined;
  vehicle._heightError = false;
  renderEditor();
  await fetchHeight(vehicle);
  renderEditor();
}

function setYaw(index, degrees) {
  const vehicle = state.current?.composition.vehicles[index];
  if (!vehicle || routeLocked(vehicle)) { renderEditor(); return; }
  vehicle.spawn.yaw_deg = ((Math.round(degrees) + 540) % 360) - 180;
  renderEditor();
}

function turnSelected(degrees) {
  const vehicle = state.current?.composition.vehicles[state.selected];
  if (!vehicle || routeLocked(vehicle)) return;
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
    onPick: pickOnMap,
    onMove: moveVehicle,
  });
  if (!window.L) $('#placement-views [data-view="map"]').disabled = true;
}

// A map click moves the selected vehicle; with a fleet only, the fleet centre.
function pickOnMap(east, north) {
  const composition = state.current?.composition;
  if (!composition) return;
  const fleets = composition.fleets || [];
  if (!composition.vehicles.length && fleets.length) {
    fleets[0].area = { east_m: round2(east), north_m: round2(north) };
    renderEditor();
    return;
  }
  moveVehicle(state.selected, east, north);
}

function newComposition() {
  const world = usableWorlds().find((item) => item.kind === "plain") || usableWorlds()[0];
  state.current = {
    id: "", editable: true,
    composition: { schema: "hakoniwa.composition/v1", id: "", world: world?.id, vehicles: [] },
  };
  state.savedSnapshot = null; // never saved
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

function addFleet(asset) {
  const composition = state.current.composition;
  composition.fleets = composition.fleets || [];
  let index = 1;
  const name = () => (index === 1 ? "Fleet" : `Fleet-${index}`);
  while (composition.fleets.some((fleet) => fleet.name === name())) index += 1;
  composition.fleets.push({
    name: name(), asset: asset.id, control: defaultControl(asset),
    count: Math.min(FLEET_DEFAULT_COUNT, asset.fleet.max_count),
    spacing_m: asset.fleet.default_spacing_m,
    area: { east_m: 0, north_m: 0 },
  });
  renderEditor();
}

function addVehicle() {
  const asset = assetById($("#add-asset").value);
  if (!asset || !state.current) return;
  if (asset.fleet) return addFleet(asset);
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

// --- Fleets (asset-contract 5.8) ----------------------------------------------------

// urban_composition.py FLEET_SPACING_RANGE_M.
const FLEET_SPACING_RANGE_M = [0.75, 5.0];
const FLEET_DEFAULT_COUNT = 10;
// drone_fleet_city.py SPAWN_CLEARANCE_RADIUS_M: the tightest grid pitch.
const FLEET_MIN_PITCH_M = 0.75;

// The spawn grid tools/drone_fleet_city.py lays out around the fleet area
// (_grid_spawn_offsets): columns run north, rows run west. The builder may
// shift the whole grid a little when its first choice is unsafe.
function fleetGrid(fleet) {
  const count = Math.max(0, Math.floor(Number(fleet.count) || 0));
  if (!count) return [];
  const spacing = Math.max(Number(fleet.spacing_m) || 0, FLEET_MIN_PITCH_M);
  const columns = Math.ceil(Math.sqrt(count));
  const rows = Math.ceil(count / columns);
  const width = (columns - 1) * spacing;
  const depth = (rows - 1) * spacing;
  const clearance = Number(assetById(fleet.asset)?.ground_clearance_m ?? 0);
  const points = [];
  for (let row = 0; row < rows && points.length < count; row += 1) {
    for (let column = 0; column < columns && points.length < count; column += 1) {
      const north = column * spacing - width / 2;
      const west = row * spacing - depth / 2;
      points.push({
        east: Number(fleet.area?.east_m ?? 0) - west,
        north: Number(fleet.area?.north_m ?? 0) + north,
        clearance,
      });
    }
  }
  return points;
}

function renderFleet(fleet, index) {
  const composition = state.current.composition;
  const asset = assetById(fleet.asset);
  const controls = asset ? Object.keys(asset.controls) : [fleet.control];
  const maxCount = asset?.fleet?.max_count ?? 1;
  const [minSpacing, maxSpacing] = FLEET_SPACING_RANGE_M;
  fleet.area = fleet.area || { east_m: 0, north_m: 0 };
  const field = (label, input) => el("label", { class: "field" }, label, input);
  const number = (value, attributes, onchange) => el("input", {
    type: "number", value: String(value), ...attributes,
    onchange: (event) => { onchange(Number(event.target.value)); renderEditor(); },
  });
  // tools/urban_composition.py to_fleet_recipe: one fleet and nothing else (for now).
  const conflict = composition.vehicles.length > 0 || (composition.fleets || []).length > 1;
  return el("div", { class: "vehicle fleet" },
    el("div", { class: "row" },
      field("名前", el("input", { value: fleet.name, onchange: (event) => { fleet.name = event.target.value.trim(); } })),
      el("label", { class: "field grow" }, "Asset", el("span", {}, asset ? `${asset.title} (${asset.id})` : fleet.asset)),
      field("制御", el("select", { onchange: (event) => { fleet.control = event.target.value; } },
        ...controls.map((name) => el("option", { value: name, selected: name === fleet.control },
          name === "rc" ? "RC（コントローラ）" : "API（プログラム）")))),
      field(`台数 (1–${maxCount})`, number(fleet.count, { min: 1, max: maxCount, step: 1 },
        (value) => { fleet.count = Math.max(1, Math.min(maxCount, Math.round(value) || 1)); })),
      field(`間隔 (m, ${minSpacing}–${maxSpacing})`, number(fleet.spacing_m, { min: minSpacing, max: maxSpacing, step: 0.25 },
        (value) => { fleet.spacing_m = Math.max(minSpacing, Math.min(maxSpacing, value || minSpacing)); })),
      field("中心 east (m)", number(fleet.area.east_m, { step: 0.5 }, (value) => { fleet.area.east_m = value || 0; })),
      field("中心 north (m)", number(fleet.area.north_m, { step: 0.5 }, (value) => { fleet.area.north_m = value || 0; })),
      field("プロセス", el("input", {
        value: fleet.processes ?? "", placeholder: "自動",
        onchange: (event) => {
          const raw = event.target.value.trim();
          if (raw === "" || raw === "auto") delete fleet.processes;
          else fleet.processes = Math.round(Number(raw));
          renderEditor();
        },
      })),
      el("button", {
        class: "icon", title: "削除", "aria-label": `${fleet.name} を削除`,
        onclick: () => {
          composition.fleets.splice(index, 1);
          if (!composition.fleets.length) delete composition.fleets;
          renderEditor();
        },
      }, "✕")),
    el("p", { class: "hint" }, `${fleet.count} 機を ${fleet.spacing_m} m 間隔のグリッドで中心の周りに並べます（地図と 3D の点）。`
      + "高さは屋上も含めて起動時に計算します。" + (conflict ? "" : "地図をクリックすると中心を移動できます。")),
    conflict ? el("p", { class: "hint warn" }, "フリートは 1 つだけで、ほかの車両とは同じ Composition に入れられません（今の実行経路の制約）。") : null);
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
    onchange: (event) => { vehicle.control = event.target.value; vehicle.params = {}; renderEditor(); refreshRouteStarts(); },
  }, ...controls.map((name) => el("option", { value: name, selected: name === vehicle.control }, name === "rc" ? "RC（コントローラ）" : "API（プログラム）")));

  const paramFields = Object.entries(params).map(([name, definition]) => definition.type === "path"
    && (definition.kinds || []).includes(ROUTE_KIND) ? routeParamField(vehicle, name, definition) : el("label", { class: "field" },
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
  const fleets = composition.fleets || [];
  $("#vehicles").replaceChildren(...(composition.vehicles.length || fleets.length
    ? [...composition.vehicles.map(renderVehicle), ...fleets.map(renderFleet)]
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
  const composition = savedForm(current.composition);
  const status = $("#compose-status");
  setStatus(status, "検証中…");
  try {
    const plan = await api("PUT", `compositions/${id}`, composition);
    state.current = { id, editable: true, composition: { ...current.composition, id } };
    state.savedSnapshot = snapshot(state.current);
    rememberComposition(id);
    await loadCompositions();
    selectRunComposition(id);
    setStatus(status, `保存しました（経路: ${plan.route}${plan.managed_recipe ? "・managed Recipe あり" : ""}）`, "ok");
  } catch (error) {
    setStatus(status, error.message, "error");
  }
}

// --- Route (Car route scenarios) ------------------------------------------------------

const ROUTE_KIND = "car-route-scenario";
// Pure-pursuit controller values that work for the Golf Cart; the Route tab edits only speed.
const DEFAULT_ROUTE_CONTROL = { speed_m_s: 1.0, lookahead_m: 2.5, position_gain: 0.8, wheelbase_m: 1.55, max_steering_deg: 32.0 };

function newRouteScenario(world) {
  return {
    schema_version: 2, name: "", rate_hz: 50, start_delay_sec: 1.0, loop_count: "forever",
    meta: { world },
    vehicles: [{ name: "Car-1", route_offset_m: 0.0 }],
    control: { ...DEFAULT_ROUTE_CONTROL },
    route: { closed: true, points: [] },
  };
}

async function loadScenarios() {
  state.scenarios = await api("GET", "scenarios");
  renderRouteList();
}

function renderRouteList() {
  $("#route-list").replaceChildren(...state.scenarios.map((item) => el("li", {},
    el("button", {
      "aria-current": String(state.route?.id === item.id),
      onclick: () => openRoute(item.id),
    }, item.name, el("span", { class: "meta" }, `${item.id}・${item.points} 点${item.editable ? "" : "・例"}`)))));
}

async function openRoute(id) {
  const loaded = await api("GET", `scenarios/${id}`);
  const scenario = loaded.scenario;
  scenario.meta = scenario.meta || {};
  scenario.route.points = scenario.route.points || [];
  // savedId: the saved (not example) file this route came from, the one 削除 removes.
  state.route = {
    id, editable: loaded.editable, savedId: loaded.editable ? id : null,
    scenario, conflicts: loaded.conflicts || [], checked: true,
  };
  state.routePoint = -1;
  setStatus($("#route-list-status"), "");
  renderRouteList();
  renderRoute();
  setStatus($("#route-status"), loaded.editable ? ""
    : "例のルートです。保存するとコピーが作られます。" + (scenario.meta.world ? "" : " World が未設定なので、使う World を選んでください。"));
  await loadRouteWorld();
}

function newRoute() {
  const world = state.current?.composition.world
    || (usableWorlds().find((item) => item.kind === "city") || usableWorlds()[0])?.id;
  state.route = { id: "", editable: true, scenario: newRouteScenario(world), conflicts: [], checked: false };
  state.routePoint = -1;
  setStatus($("#route-list-status"), "");
  renderRouteList();
  renderRoute();
  setStatus($("#route-status"), "");
  $("#route-id").focus();
  loadRouteWorld();
}

async function loadRouteWorld() {
  const worldId = state.route?.scenario.meta.world;
  const mapNode = $("#route-map");
  let hasMap = false;
  if (worldId && state.routeMap) {
    try {
      const info = await api("GET", `worlds/${worldId}`);
      hasMap = state.routeMap.setWorld(info);
    } catch (error) {
      setStatus($("#route-status"), error.message, "error");
    }
  }
  mapNode.hidden = !hasMap;
  $("#route-map-hint").textContent = hasMap
    ? "地図をクリックすると点を追加します。点はドラッグで移動、クリックで選択します。点は番号順に結ばれ、最後の点から最初の点へ戻るループになります（3点以上）。"
    : "この World には地図がありません。「点を追加」で点を増やし、east / north を数値で入力してください（World の中心からのメートル）。";
  if (hasMap) {
    state.routeMap.show();
    await showRouteFootprints(worldId);
    state.routeMap.setRoute(state.route.scenario.route.points, state.routePoint, state.route.conflicts || []);
  }
}

// Building outlines are the collision walls a Car cannot drive through.
async function showRouteFootprints(worldId) {
  state.footprints = state.footprints || {};
  if (!(worldId in state.footprints)) {
    try {
      state.footprints[worldId] = (await api("GET", `worlds/${worldId}/footprints`)).buildings;
    } catch {
      state.footprints[worldId] = [];
    }
  }
  state.routeMap.setFootprints(state.footprints[worldId], $("#route-show-buildings").checked);
}

function routeNumber(value, fallback) {
  const number = Number(value);
  return Number.isFinite(number) ? number : fallback;
}

// A point edit makes the last building check stale until the next save.
function routeEdited() {
  if (state.route) { state.route.conflicts = []; state.route.checked = false; }
}

function addRoutePoint(east, north) {
  routeEdited();
  const points = state.route.scenario.route.points;
  points.push({ name: `p${points.length + 1}`, east_m: round2(east), north_m: round2(north) });
  state.routePoint = points.length - 1;
  renderRoute();
}

function renderRoute() {
  const route = state.route;
  $(".route-editor").hidden = !route;
  if (!route) return;
  const scenario = route.scenario;
  $("#route-delete").hidden = !route.savedId;
  $("#route-id").value = route.id;
  $("#route-name").value = scenario.name || "";
  const worldSelect = $("#route-world");
  worldSelect.replaceChildren(
    el("option", { value: "" }, "（未設定）"),
    ...usableWorlds().map((world) => el("option", { value: world.id }, `${world.title}（${worldKindLabel(world.kind)}）`)));
  worldSelect.value = scenario.meta.world || "";
  $("#route-speed").value = scenario.control?.speed_m_s ?? DEFAULT_ROUTE_CONTROL.speed_m_s;
  const forever = scenario.loop_count === "forever";
  $("#route-forever").checked = forever;
  $("#route-loops").disabled = forever;
  $("#route-loops").value = forever ? "" : scenario.loop_count;
  $("#route-delay").value = scenario.start_delay_sec ?? 1.0;

  $("#route-vehicles").replaceChildren(...scenario.vehicles.map((vehicle, index) => el("div", { class: "route-vehicle" },
    el("label", { class: "field" }, "名前", el("input", {
      value: vehicle.name, size: 8, onchange: (event) => { vehicle.name = event.target.value.trim(); },
    })),
    el("label", { class: "field" }, "間隔 (m)", el("input", {
      type: "number", step: "0.5", max: "0", value: String(vehicle.route_offset_m ?? 0), disabled: index === 0,
      onchange: (event) => { vehicle.route_offset_m = routeNumber(event.target.value, 0); },
    })),
    index > 0 ? el("button", {
      class: "icon", title: "削除", onclick: () => { scenario.vehicles.splice(index, 1); renderRoute(); },
    }, "✕") : null)));

  const points = scenario.route.points;
  $("#route-points tbody").replaceChildren(...(points.length ? points.map((point, index) => {
    const cell = (key, step) => el("td", {}, el("input", {
      type: "number", step, value: String(point[key] ?? 0),
      onchange: (event) => { point[key] = routeNumber(event.target.value, 0); routeEdited(); renderRoute(); },
    }));
    return el("tr", {
      class: index === state.routePoint ? "selected" : "",
      onclick: (event) => { if (!event.target.closest("input, button")) { state.routePoint = index; renderRoute(); } },
    },
      el("td", {}, String(index + 1)),
      el("td", {}, el("input", { value: point.name || "", onchange: (event) => { point.name = event.target.value.trim(); renderRoute(); } })),
      cell("east_m", "0.1"), cell("north_m", "0.1"), cell("dwell_sec", "0.5"),
      el("td", {}, el("button", {
        class: "icon", title: "削除", onclick: () => {
          points.splice(index, 1);
          routeEdited();
          state.routePoint = Math.min(state.routePoint, points.length - 1);
          renderRoute();
        },
      }, "✕")));
  }) : [el("tr", {}, el("td", { colspan: 6, class: "hint" }, "点がありません"))]));
  const conflictList = $("#route-conflicts");
  const conflicts = route.conflicts || [];
  conflictList.hidden = !conflicts.length && route.checked;
  conflictList.replaceChildren(...(conflicts.length
    ? conflicts.map((conflict) => el("li", {},
      `点${conflict.from}→点${conflict.to}：${conflict.reason === "inside" ? "建物の中に入ります" : "建物の壁に近すぎます（車の幅を考えると通れません）"}`
      + `（${conflict.at[0]}E, ${conflict.at[1]}N）`))
    : route.checked ? [] : [el("li", { class: "hint" }, "建物との当たりは、保存すると再チェックします")]));
  if (!$("#route-map").hidden) state.routeMap?.setRoute(points, state.routePoint, conflicts);
}

async function saveRoute() {
  const route = state.route;
  if (!route) return;
  const status = $("#route-status");
  if (!route.id) { setStatus(status, "ID を入力してください", "error"); return; }
  const scenario = route.scenario;
  // Drop unset dwell values so the saved file stays minimal.
  scenario.route.points = scenario.route.points.map((point) => {
    const copy = { ...point };
    if (!copy.dwell_sec) delete copy.dwell_sec;
    return copy;
  });
  setStatus(status, "検証中…");
  try {
    const saved = await api("PUT", `scenarios/${route.id}`, scenario);
    route.editable = true;
    route.savedId = route.id;
    state.routeData = {}; // Compose re-reads the saved route for its route starts
    route.conflicts = saved.conflicts || [];
    route.checked = true;
    await loadScenarios();
    renderEditor(); // Compose selectors list the new or renamed route
    renderRoute();
    if (route.conflicts.length) {
      setStatus(status, `保存しました。ただし ${route.conflicts.length} 区間が建物の壁にぶつかります（赤い破線）。点を動かして保存し直してください。`, "error");
    } else {
      setStatus(status, "保存しました。建物にぶつかる区間はありません。Compose の API の車で、このルートを選べます。", "ok");
    }
  } catch (error) {
    setStatus(status, error.message, "error");
  }
}

async function deleteRoute() {
  const route = state.route;
  if (!route?.savedId) return;
  if (!window.confirm(`ルート「${route.scenario.name || route.savedId}」（${route.savedId}）を削除しますか？\n元に戻せません。`)) return;
  const status = $("#route-status");
  try {
    await api("DELETE", `scenarios/${route.savedId}`);
    state.route = null;
    state.routeData = {};
    await loadScenarios();
    renderEditor(); // Compose selectors drop the route
    renderRoute();
    setStatus($("#route-list-status"), `ルート ${route.savedId} を削除しました。`, "ok");
  } catch (error) {
    setStatus(status, error.message, "error");
  }
}

async function initRoute() {
  const { RouteMapView } = await import("./route.js");
  state.routeMap = new RouteMapView($("#route-map"), {
    onAdd: (east, north) => { if (state.route) addRoutePoint(east, north); },
    onSelect: (index) => { state.routePoint = index; renderRoute(); },
    onMove: (index, east, north) => {
      const point = state.route?.scenario.route.points[index];
      if (!point) return;
      routeEdited();
      point.east_m = round2(east);
      point.north_m = round2(north);
      state.routePoint = index;
      renderRoute();
    },
  });
  $("#new-route").addEventListener("click", newRoute);
  $("#route-show-buildings").addEventListener("change", () => {
    const worldId = state.route?.scenario.meta.world;
    if (worldId && !$("#route-map").hidden) showRouteFootprints(worldId);
  });
  $("#route-save").addEventListener("click", saveRoute);
  $("#route-delete").addEventListener("click", deleteRoute);
  $("#route-add-point").addEventListener("click", () => {
    if (!state.route) return;
    const last = state.route.scenario.route.points.at(-1);
    addRoutePoint((last?.east_m ?? 0) + 5, last?.north_m ?? 0);
  });
  $("#route-add-vehicle").addEventListener("click", () => {
    if (!state.route) return;
    const vehicles = state.route.scenario.vehicles;
    const offset = (vehicles.at(-1)?.route_offset_m ?? 0) - 6;
    vehicles.push({ name: `Car-${vehicles.length + 1}`, route_offset_m: offset });
    renderRoute();
  });
  const bind = (selector, apply) => $(selector).addEventListener("change", (event) => {
    if (state.route) { apply(state.route, event.target); renderRoute(); }
  });
  bind("#route-id", (route, input) => { route.id = input.value.trim(); });
  bind("#route-name", (route, input) => { route.scenario.name = input.value.trim(); });
  bind("#route-world", (route, input) => { route.scenario.meta.world = input.value || undefined; loadRouteWorld(); });
  bind("#route-speed", (route, input) => {
    route.scenario.control = { ...DEFAULT_ROUTE_CONTROL, ...route.scenario.control, speed_m_s: routeNumber(input.value, 1.0) };
  });
  bind("#route-forever", (route, input) => { route.scenario.loop_count = input.checked ? "forever" : 1; });
  bind("#route-loops", (route, input) => { route.scenario.loop_count = Math.max(1, Math.round(routeNumber(input.value, 1))); });
  bind("#route-delay", (route, input) => { route.scenario.start_delay_sec = Math.max(0, routeNumber(input.value, 1.0)); });
}

// Compose -> Route: edit the route a Car uses.
async function editRouteFor(reference) {
  const item = state.scenarios.find((scenario) => scenario.reference === reference);
  showTab("route");
  if (item) await openRoute(item.id);
  else newRoute();
}

// The scenario param of an API Car: pick a saved or example route.
function routeParamField(vehicle, name, definition) {
  const composition = state.current.composition;
  const value = vehicle.params[name] ?? "";
  const known = state.scenarios.some((item) => item.reference === value);
  const ordered = [...state.scenarios].sort((a, b) =>
    Number(b.world === composition.world) - Number(a.world === composition.world) || a.name.localeCompare(b.name));
  const select = el("select", {
    onchange: (event) => {
      if (event.target.value) vehicle.params[name] = event.target.value;
      else delete vehicle.params[name];
      renderEditor();
      refreshRouteStarts();
    },
  },
    el("option", { value: "" }, "（ルートを選択）"),
    !known && value ? el("option", { value }, `（現在）${value}`) : null,
    ...ordered.map((item) => el("option", { value: item.reference },
      `${item.name}（${item.id}）${item.world && item.world !== composition.world ? "・別の World" : item.world ? "" : "・World 未設定"}`)));
  select.value = value;
  const chosen = state.scenarios.find((item) => item.reference === value);
  const warning = chosen && chosen.world && chosen.world !== composition.world
    ? el("span", { class: "hint error" }, "別の World 用のルートです（座標が合わない可能性があります）")
    : null;
  // The route decides where the Car starts; its placed spawn is not used.
  const note = value
    ? el("span", { class: "hint" }, "初期位置はルートの開始点です（同じルートの2台目以降は、ルートで決めた間隔だけ後ろ）。配置の east / north / yaw は使いません。")
    : null;
  return el("label", { class: "field grow" }, `ルート${definition.required ? " *" : ""}`,
    el("div", { class: "row" }, select,
      el("button", { class: "secondary", onclick: (event) => { event.preventDefault(); editRouteFor(value); } }, "ルートを編集")),
    warning, note);
}

// --- Simulation ---------------------------------------------------------------------

// Warn when Compose holds edits that Simulation (which runs the saved file) will not use.
function renderUnsavedNotice() {
  const notice = $("#run-unsaved");
  const current = state.current;
  const runId = $("#run-composition").value;
  const relevant = current && (!current.id || current.id === runId || !state.savedSnapshot);
  notice.hidden = !(isDirty() && relevant);
  if (notice.hidden) return;
  $("#run-unsaved-text").textContent = current.id && state.savedSnapshot
    ? `Compose の「${current.id}」に未保存の変更があります。Simulation が実行するのは保存済みの内容です。`
    : "Compose の新しい Composition はまだ保存されていません。保存すると、ここで選んで実行できます。";
}

function formatTime(epochSeconds) {
  return epochSeconds ? new Date(epochSeconds * 1000).toLocaleString() : "";
}

function renderRunSummary(item, plan) {
  const vehicles = (item.vehicle_list || []).map((vehicle) =>
    el("li", {}, el("strong", {}, vehicle.name), ` ${vehicle.title}（${vehicle.control}）`));
  const fleets = (item.fleets || []).map((fleet) =>
    el("li", {}, el("strong", {}, fleet.name), ` ${fleet.title}×${fleet.count}（${fleet.control}・フリート）`));
  return [
    el("div", { class: "summary-line" },
      el("span", { class: "summary-label" }, "World"),
      `${item.world_title}（${worldKindLabel(item.world_kind)}）`),
    el("div", { class: "summary-line" },
      el("span", { class: "summary-label" }, "車両"),
      el("ul", { class: "summary-vehicles" }, ...vehicles, ...fleets)),
    el("div", { class: "summary-line hint" },
      plan ? `経路: ${plan.route}${plan.managed_recipe ? "・managed Recipe あり" : ""} ／ ` : "",
      item.editable ? `保存: ${formatTime(item.updated_at)}` : "例（読み取り専用）"),
  ];
}

async function refreshRunPlan() {
  const id = $("#run-composition").value;
  const node = $("#run-plan");
  $("#run-open-compose").disabled = !id;
  if (!id) { node.replaceChildren(); return; }
  const item = state.compositions.find((composition) => composition.id === id);
  try {
    const plan = await api("GET", `compositions/${id}/plan`);
    node.replaceChildren(...renderRunSummary(item || { id }, plan));
    node.classList.remove("error");
  } catch (error) {
    node.replaceChildren(...(item ? renderRunSummary(item, null) : []), el("div", {}, error.message));
    node.classList.add("error");
  }
  refreshViewer(false);
}

// Simulation -> Compose: look at (or edit) the selected Composition.
async function openRunCompositionInCompose() {
  const id = $("#run-composition").value;
  if (!id) return;
  if (state.current?.id !== id) {
    if (!confirmDiscard()) return;
    await openComposition(id);
  }
  showTab("compose");
}

async function refreshViewer(show) {
  const id = $("#run-composition").value;
  const link = $("#viewer-link");
  try {
    const { url, collider_url: colliderUrl } = await api("GET", `compositions/${id}/viewer`);
    link.hidden = !url;
    if (url) link.href = url;
    const colliderLink = $("#viewer-collider-link");
    colliderLink.hidden = !colliderUrl;
    if (colliderUrl) colliderLink.href = colliderUrl;
    if (url && show) {
      $("#viewer-panel").hidden = false;
      $("#viewer-frame").src = url;
    }
  } catch {
    link.hidden = true;
    $("#viewer-collider-link").hidden = true;
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
  $("#new-composition").addEventListener("click", () => { if (confirmDiscard()) newComposition(); });
  $("#to-simulation").addEventListener("click", () => showTab("simulation"));
  $("#run-open-compose").addEventListener("click", openRunCompositionInCompose);
  $("#run-unsaved-compose").addEventListener("click", () => showTab("compose"));
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
  $("#cache-refresh").addEventListener("click", loadCache);
  $("#cache-prune").addEventListener("click", pruneCache);
  $("#save-composition").addEventListener("click", saveComposition);
  $("#run-composition").addEventListener("change", (event) => {
    rememberComposition(event.target.value);
    renderUnsavedNotice();
    refreshRunPlan();
    pollRtf();
  });
  for (const button of document.querySelectorAll("[data-command]")) button.addEventListener("click", () => runCommand(button.dataset.command));

  state.assets = await api("GET", "assets");
  renderAssets();
  await initPlacement();
  await initRoute();
  await loadScenarios();
  await loadCompositions();
  // Reopen the Composition used last (in Compose or Simulation), else the first one.
  const last = rememberedComposition();
  const initial = state.compositions.find((item) => item.id === last) || state.compositions[0];
  if (initial) await openComposition(initial.id);
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

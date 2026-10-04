// Asset previews: vehicles, plain Worlds and Cities turn slowly in 3D (drag to
// turn them by hand); a City's buildings are its outlines raised to their
// heights (a flat map when it has none).
//
// Every 3D card shares one WebGL renderer: browsers allow only a few WebGL
// contexts, so each frame is rendered offscreen and copied into the card's
// own 2D canvas.

import * as THREE from "three";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { mergeGeometries } from "three/addons/utils/BufferGeometryUtils.js";

const WIDTH = 240;
const HEIGHT = 170;
const TURN_RAD_S = 0.5;
const ELEVATION_RAD = 0.45;

const loader = new GLTFLoader();
let renderer = null;
const cards = new Set();
let animating = false;
let last = 0;

// Vehicle frame (X forward, Y left, Z up) -> three.js (X right, Y up, Z back).
function positionToThree([x, y, z]) {
  return new THREE.Vector3(-y, z, -x);
}

function quaternionToThree([x, y, z, w]) {
  return new THREE.Quaternion(-y, z, -x, w);
}

// A GLB authored in the vehicle frame (hakoniwa-threejs-drone src/vehicle.js).
const FLU_BASIS = new THREE.Matrix4().set(
  0, -1, 0, 0,
  0, 0, 1, 0,
  -1, 0, 0, 0,
  0, 0, 0, 1,
);

function sharedRenderer() {
  if (!renderer) {
    renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    renderer.setSize(WIDTH, HEIGHT, false);
    renderer.outputColorSpace = THREE.SRGBColorSpace;
  }
  return renderer;
}

function makeScene() {
  const scene = new THREE.Scene();
  scene.add(new THREE.HemisphereLight(0xffffff, 0x8090a0, 2.2));
  const sun = new THREE.DirectionalLight(0xffffff, 1.6);
  sun.position.set(3, 5, 4);
  scene.add(sun);
  return scene;
}

function frame(card) {
  // A City frames its buildings (not its whole ground), a little tighter.
  const box = new THREE.Box3().setFromObject(card.focus ?? card.model);
  const sphere = box.getBoundingSphere(new THREE.Sphere());
  card.model.position.sub(sphere.center);
  const distance = sphere.radius / Math.sin(THREE.MathUtils.degToRad(card.camera.fov / 2)) * (card.focus ? 0.9 : 1.05);
  const elevation = card.elevation ?? ELEVATION_RAD;
  card.camera.position.set(0, Math.sin(elevation) * distance, Math.cos(elevation) * distance);
  card.camera.near = distance / 100;
  card.camera.far = distance * 10;
  card.camera.lookAt(0, 0, 0);
  card.camera.updateProjectionMatrix();
}

function render(card) {
  const target = sharedRenderer();
  target.render(card.scene, card.camera);
  const context = card.canvas.getContext("2d");
  context.clearRect(0, 0, card.canvas.width, card.canvas.height);
  context.drawImage(target.domElement, 0, 0, card.canvas.width, card.canvas.height);
}

function tick(now) {
  const dt = last ? Math.min(0.1, (now - last) / 1000) : 0;
  last = now;
  let visible = 0;
  for (const card of cards) {
    if (!card.canvas.isConnected) {
      cards.delete(card);
      continue;
    }
    // Hidden tabs have no layout box: skip their cards.
    if (!card.canvas.offsetParent) continue;
    visible += 1;
    if (!card.dragging) card.turntable.rotation.y += TURN_RAD_S * dt;
    render(card);
  }
  if (cards.size === 0) {
    animating = false;
    last = 0;
    return;
  }
  requestAnimationFrame(tick);
  if (visible === 0) last = 0;
}

function start(card) {
  cards.add(card);
  if (!animating) {
    animating = true;
    requestAnimationFrame(tick);
  }
}

function canvasFor(container) {
  const ratio = Math.min(window.devicePixelRatio || 1, 2);
  const canvas = document.createElement("canvas");
  canvas.width = WIDTH * ratio;
  canvas.height = HEIGHT * ratio;
  canvas.className = "asset-preview-canvas";
  container.replaceChildren(canvas);
  return canvas;
}

function message(container, text) {
  const note = document.createElement("div");
  note.className = "asset-preview-empty";
  note.textContent = text;
  container.replaceChildren(note);
}

function mountTurntable(container, model, elevation = ELEVATION_RAD, focus = null) {
  try {
    sharedRenderer();
  } catch (error) {
    console.warn("[AssetPreview] WebGL unavailable:", error);
    message(container, "3D プレビューには WebGL が必要です");
    return;
  }
  const canvas = canvasFor(container);
  const card = {
    canvas, model, elevation, focus, dragging: false,
    scene: makeScene(),
    camera: new THREE.PerspectiveCamera(35, WIDTH / HEIGHT, 0.01, 1000),
    turntable: new THREE.Group(),
  };
  card.turntable.add(model);
  card.scene.add(card.turntable);
  frame(card);
  let lastX = 0;
  canvas.addEventListener("pointerdown", (event) => {
    card.dragging = true;
    lastX = event.clientX;
    canvas.setPointerCapture(event.pointerId);
  });
  canvas.addEventListener("pointermove", (event) => {
    if (!card.dragging) return;
    card.turntable.rotation.y += (event.clientX - lastX) * 0.01;
    lastX = event.clientX;
  });
  const release = () => { card.dragging = false; };
  canvas.addEventListener("pointerup", release);
  canvas.addEventListener("pointercancel", release);
  start(card);
}

async function loadPart(part) {
  const gltf = await loader.loadAsync(part.url);
  const visual = gltf.scene;
  if (part.basis === "flu") visual.applyMatrix4(FLU_BASIS);
  const node = new THREE.Group();
  node.position.copy(positionToThree(part.position));
  node.quaternion.copy(quaternionToThree(part.quaternion));
  node.scale.setScalar(part.scale ?? 1);
  node.add(visual);
  return node;
}

/** A vehicle Asset (GET /api/assets/<id>/preview) turning in 3D. */
export async function mountVehiclePreview(container, info) {
  message(container, "読み込み中…");
  try {
    const nodes = await Promise.all(info.parts.map(loadPart));
    const model = new THREE.Group();
    model.add(...nodes);
    mountTurntable(container, model);
  } catch (error) {
    console.warn("[AssetPreview] vehicle preview failed:", error);
    message(container, "プレビューを表示できません");
  }
}

/** A plain World's display GLB (already in three.js axes) turning in 3D. */
export async function mountWorldPreview(container, glbUrl) {
  message(container, "読み込み中…");
  try {
    const gltf = await loader.loadAsync(glbUrl);
    mountTurntable(container, gltf.scene);
  } catch (error) {
    console.warn("[AssetPreview] world preview failed:", error);
    message(container, "プレビューを表示できません");
  }
}

const CITY_ELEVATION_RAD = 0.62;

// The City's ground (its extent) and its buildings: each outline raised from the
// ground under it (base_m) to its top (height_m), courtyards left open. One
// merged mesh, so a big City is still one draw call.
function cityModel(footprints, halfExtent) {
  const buildings = footprints.buildings.filter((b) => b.vertices.length >= 3 && Number.isFinite(b.height_m));
  if (!buildings.length) return null;
  const bases = buildings.map((b) => (Number.isFinite(b.base_m) ? b.base_m : b.height_m));
  const ground = Math.min(...bases);
  const shapeOf = (ring) => new THREE.Shape(ring.map(([east, north]) => new THREE.Vector2(east, north)));
  const geometries = [];
  buildings.forEach((building, index) => {
    const base = Number.isFinite(building.base_m) ? building.base_m : ground;
    const height = Math.max(building.height_m - base, 1);
    const shape = shapeOf(building.vertices);
    shape.holes = (building.holes || []).filter((ring) => ring.length >= 3).map(shapeOf);
    const geometry = new THREE.ExtrudeGeometry(shape, { depth: height, bevelEnabled: false });
    // Shape plane (east, north) -> three.js: east = x, north = -z, up = y.
    geometry.rotateX(-Math.PI / 2);
    geometry.translate(0, bases[index] - ground, 0);
    geometries.push(geometry);
  });
  const merged = mergeGeometries(geometries, false);
  geometries.forEach((geometry) => geometry.dispose());
  if (!merged) return null;
  const model = new THREE.Group();
  const floor = new THREE.Mesh(
    new THREE.PlaneGeometry(2 * halfExtent.east_west, 2 * halfExtent.north_south),
    new THREE.MeshStandardMaterial({ color: 0xdfe5ec, roughness: 1 }),
  );
  floor.rotation.x = -Math.PI / 2;
  model.add(floor);
  const buildingsMesh = new THREE.Mesh(merged, new THREE.MeshStandardMaterial({ color: 0x8c99ab, roughness: 0.85 }));
  model.add(buildingsMesh);
  model.userData.focus = buildingsMesh;
  model.add(new THREE.LineSegments(
    new THREE.EdgesGeometry(merged, 30),
    new THREE.LineBasicMaterial({ color: 0x4d5868, transparent: true, opacity: 0.35 }),
  ));
  return model;
}

// A City without building outlines (a World made in the Environment Studio) shows
// its display GLB instead, when that is small enough for a card.
const CITY_GLB_MAX_BYTES = 25 * 1024 * 1024;

/**
 * A City World's buildings (GET /api/worlds/<id>/footprints) in 3D, turning slowly.
 * Without outlines: its display GLB (glbUrl, glbBytes from GET /api/worlds/<id>) if small enough, else a note.
 */
export async function mountCityPreview(container, footprints, halfExtent, glbUrl, glbBytes) {
  let model = null;
  try {
    model = cityModel(footprints, halfExtent);
  } catch (error) {
    console.warn("[AssetPreview] city model failed:", error);
  }
  if (model) {
    mountTurntable(container, model, CITY_ELEVATION_RAD, model.userData.focus);
    return;
  }
  if (glbUrl && glbBytes > 0 && glbBytes <= CITY_GLB_MAX_BYTES) {
    try {
      message(container, "読み込み中…");
      const gltf = await loader.loadAsync(glbUrl);
      mountTurntable(container, gltf.scene, CITY_ELEVATION_RAD);
      return;
    } catch (error) {
      console.warn("[AssetPreview] city GLB preview failed:", error);
    }
  }
  message(container, "プレビューなし（建物の外形データがありません）");
}

/** A City World's building outlines (GET /api/worlds/<id>/footprints) as a map. */
export function drawCityPreview(container, footprints, halfExtent) {
  const canvas = canvasFor(container);
  const context = canvas.getContext("2d");
  const style = getComputedStyle(container);
  // halfExtent: {east_west, north_south} in metres (World info half_extent_m).
  const scale = Math.min(canvas.width / (2 * halfExtent.east_west), canvas.height / (2 * halfExtent.north_south));
  const toCanvas = ([east, north]) => [canvas.width / 2 + east * scale, canvas.height / 2 - north * scale];
  context.fillStyle = style.getPropertyValue("--preview-ground").trim() || "#e8edf3";
  context.fillRect(0, 0, canvas.width, canvas.height);
  context.fillStyle = style.getPropertyValue("--preview-building").trim() || "#7b8798";
  for (const building of footprints.buildings) {
    context.beginPath();
    for (const ring of [building.vertices, ...(building.holes || [])]) {
      ring.forEach((point, index) => {
        const [x, y] = toCanvas(point);
        if (index === 0) context.moveTo(x, y);
        else context.lineTo(x, y);
      });
      context.closePath();
    }
    // Courtyards (holes) stay open.
    context.fill("evenodd");
  }
}

export function showPreviewMessage(container, text) {
  message(container, text);
}

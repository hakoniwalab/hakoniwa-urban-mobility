// Asset previews: vehicles and plain Worlds turn slowly in 3D (drag to turn
// them by hand); a City shows its building outlines as a small map.
//
// Every 3D card shares one WebGL renderer: browsers allow only a few WebGL
// contexts, so each frame is rendered offscreen and copied into the card's
// own 2D canvas.

import * as THREE from "three";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";

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
  const box = new THREE.Box3().setFromObject(card.model);
  const sphere = box.getBoundingSphere(new THREE.Sphere());
  card.model.position.sub(sphere.center);
  const distance = sphere.radius / Math.sin(THREE.MathUtils.degToRad(card.camera.fov / 2)) * 1.05;
  card.camera.position.set(0, Math.sin(ELEVATION_RAD) * distance, Math.cos(ELEVATION_RAD) * distance);
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

function mountTurntable(container, model) {
  try {
    sharedRenderer();
  } catch (error) {
    console.warn("[AssetPreview] WebGL unavailable:", error);
    message(container, "3D プレビューには WebGL が必要です");
    return;
  }
  const canvas = canvasFor(container);
  const card = {
    canvas, model, dragging: false,
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

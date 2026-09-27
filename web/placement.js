// 3D placement view: the World GLB with draggable vehicle markers.
//
// Scene frame = City World GLB frame: X = East, Y = Up, Z = -North.
// Dragging raycasts the World meshes, so a marker rides on roofs and
// obstacles while it moves; the backend height API gives the final height.

import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";

const COLORS = { car: 0x2e7dd7, drone: 0xe08a1e, fpv: 0x9b5de5 };

export class PlacementView {
  constructor(container, { onSelect, onMove }) {
    this.container = container;
    this.onSelect = onSelect;
    this.onMove = onMove;
    this.scene = new THREE.Scene();
    this.scene.background = new THREE.Color(0x9fc2e8);
    this.camera = new THREE.PerspectiveCamera(50, 1, 0.1, 5000);
    this.renderer = new THREE.WebGLRenderer({ antialias: true });
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    container.append(this.renderer.domElement);
    this.controls = new OrbitControls(this.camera, this.renderer.domElement);
    this.controls.maxPolarAngle = Math.PI * 0.49;
    this.scene.add(new THREE.HemisphereLight(0xffffff, 0x445566, 1.4));
    const sun = new THREE.DirectionalLight(0xffffff, 1.6);
    sun.position.set(80, 150, 60);
    this.scene.add(sun);
    this.worldGroup = new THREE.Group();
    this.markerGroup = new THREE.Group();
    this.scene.add(this.worldGroup, this.markerGroup);
    this.raycaster = new THREE.Raycaster();
    this.pointer = new THREE.Vector2();
    this.ground = new THREE.Plane(new THREE.Vector3(0, 1, 0), 0);
    this.vehicles = [];
    this.dragging = null;

    const canvas = this.renderer.domElement;
    canvas.addEventListener("pointerdown", (event) => this.pointerDown(event));
    canvas.addEventListener("pointermove", (event) => this.pointerMove(event));
    canvas.addEventListener("pointerup", (event) => this.pointerUp(event));
    new ResizeObserver(() => this.resize()).observe(container);
    this.resize();
    this.renderer.setAnimationLoop(() => {
      this.controls.update();
      this.renderer.render(this.scene, this.camera);
    });
  }

  resize() {
    const width = this.container.clientWidth || 1;
    const height = this.container.clientHeight || 1;
    this.renderer.setSize(width, height, false);
    this.camera.aspect = width / height;
    this.camera.updateProjectionMatrix();
  }

  async setWorld(info) {
    if (this.worldId === info.id) return;
    this.worldId = info.id;
    this.worldGroup.clear();
    const halfNorth = info.half_extent_m.north_south;
    const halfEast = info.half_extent_m.east_west;
    const span = Math.max(halfNorth, halfEast);
    this.camera.position.set(-span * 0.9, span * 0.8, span * 0.9);
    this.controls.target.set(0, 0, 0);
    this.camera.far = span * 20;
    this.camera.updateProjectionMatrix();
    const grid = new THREE.GridHelper(span * 2, Math.max(4, Math.round(span / 10)), 0x335577, 0x557799);
    grid.position.y = 0.02;
    grid.material.transparent = true;
    grid.material.opacity = 0.35;
    this.worldGroup.add(grid);
    const gltf = await new GLTFLoader().loadAsync(info.glb);
    if (this.worldId !== info.id) return; // another World was selected meanwhile
    this.worldGroup.add(gltf.scene);
  }

  clearWorld() {
    this.worldId = null;
    this.worldGroup.clear();
  }

  setVehicles(vehicles, selected) {
    this.vehicles = vehicles;
    this.markerGroup.clear();
    vehicles.forEach((vehicle, index) => {
      const color = COLORS[vehicle.kind] ?? 0x888888;
      const marker = new THREE.Group();
      const size = vehicle.kind === "car" ? 1.6 : 1.0;
      const body = new THREE.Mesh(
        new THREE.ConeGeometry(size * 0.6, size * 1.8, 16).rotateZ(-Math.PI / 2),
        new THREE.MeshStandardMaterial({ color, emissive: index === selected ? 0x333300 : 0x000000 }),
      );
      body.userData.index = index;
      marker.add(body);
      const pole = new THREE.Mesh(
        new THREE.CylinderGeometry(0.05, 0.05, 6, 6),
        new THREE.MeshBasicMaterial({ color, transparent: true, opacity: index === selected ? 0.9 : 0.4 }),
      );
      pole.position.y = 3;
      marker.add(pole);
      if (index === selected) {
        const ring = new THREE.Mesh(
          new THREE.RingGeometry(size * 1.3, size * 1.6, 32).rotateX(-Math.PI / 2),
          new THREE.MeshBasicMaterial({ color: 0xffee55, side: THREE.DoubleSide }),
        );
        ring.position.y = -vehicle.clearance + 0.05; // on the ground under the marker
        marker.add(ring);
      }
      marker.position.set(vehicle.east, vehicle.up, -vehicle.north);
      marker.rotation.y = THREE.MathUtils.degToRad(vehicle.yaw);
      this.markerGroup.add(marker);
    });
  }

  ray(event) {
    const rect = this.renderer.domElement.getBoundingClientRect();
    this.pointer.set(((event.clientX - rect.left) / rect.width) * 2 - 1, -((event.clientY - rect.top) / rect.height) * 2 + 1);
    this.raycaster.setFromCamera(this.pointer, this.camera);
  }

  surfacePoint() {
    const hit = this.raycaster.intersectObjects(this.worldGroup.children, true)
      .find((intersection) => intersection.object.type === "Mesh");
    if (hit) return hit.point;
    const point = new THREE.Vector3();
    return this.raycaster.ray.intersectPlane(this.ground, point) ? point : null;
  }

  pointerDown(event) {
    this.ray(event);
    const hit = this.raycaster.intersectObjects(this.markerGroup.children, true)
      .find((intersection) => intersection.object.userData.index !== undefined);
    if (!hit) return;
    const index = hit.object.userData.index;
    this.dragging = { index, marker: this.markerGroup.children[index] };
    this.controls.enabled = false;
    this.renderer.domElement.setPointerCapture(event.pointerId);
    this.onSelect(index);
  }

  pointerMove(event) {
    if (!this.dragging) return;
    this.ray(event);
    const point = this.surfacePoint();
    if (!point) return;
    const vehicle = this.vehicles[this.dragging.index];
    this.dragging.marker.position.set(point.x, point.y + vehicle.clearance, point.z);
    this.dragging.point = point;
  }

  pointerUp(event) {
    if (!this.dragging) return;
    const { index, point } = this.dragging;
    this.dragging = null;
    this.controls.enabled = true;
    this.renderer.domElement.releasePointerCapture(event.pointerId);
    if (point) this.onMove(index, point.x, -point.z);
  }
}

// Flight tab views: a Drone flight on the City World map and in 3D.
//
// The map edits the flight: click to add a waypoint (or to place the takeoff
// or landing point while picking one), drag a point to move it. The 3D view
// shows the same line at its heights over the World GLB: each point has a
// stem down to the top of the World under it (its height above the ground),
// and a leg that meets the World is red, with a red ball where it meets it.
// Both take the flight's line: [{east_m, north_m, up_m, ground_m, label, kind}]
// where kind is "takeoff", "waypoint" (with its waypoint index) or "land".

import * as THREE from "three";
import { MapView } from "./map.js";
import { PlacementView } from "./placement.js";

const KIND_COLORS = { takeoff: "#2e9d4f", waypoint: "#2e7dd7", land: "#d0572a" };

// selected: a waypoint's index, or "takeoff" / "land".
const isSelected = (point, selected) => (point.kind === "waypoint" ? point.index === selected : point.kind === selected);

// A text label that always faces the camera.
function labelSprite(text, color) {
  const canvas = document.createElement("canvas");
  canvas.width = 64;
  canvas.height = 64;
  const context = canvas.getContext("2d");
  context.fillStyle = color;
  context.beginPath();
  context.arc(32, 32, 28, 0, Math.PI * 2);
  context.fill();
  context.lineWidth = 4;
  context.strokeStyle = "#ffffff";
  context.stroke();
  context.fillStyle = "#ffffff";
  context.font = "bold 30px sans-serif";
  context.textAlign = "center";
  context.textBaseline = "middle";
  context.fillText(text, 32, 34);
  const sprite = new THREE.Sprite(new THREE.SpriteMaterial({ map: new THREE.CanvasTexture(canvas), depthTest: false }));
  sprite.scale.set(2.2, 2.2, 1);
  sprite.renderOrder = 10;
  return sprite;
}

export class FlightMapView extends MapView {
  constructor(container, { onPick, onSelect, onMove }) {
    super(container, { onPick, onSelect, onMove });
    this.line = null;
    this.blocked = null;
  }

  // points: the map markers [{east_m, north_m, label, kind, index}]; line: the
  // flight's [east, north] in order; blocked: [[from, to]] pairs of line points.
  setFlight(points, line, selected, blocked = []) {
    if (!this.map) return;
    for (const marker of this.markers) marker.remove();
    this.line?.remove();
    this.blocked?.remove();
    this.blocked = blocked.length ? L.layerGroup(blocked.map(([from, to]) => L.polyline(
      [from, to].map((point) => this.toLatLng(point[0], point[1])),
      { color: "#d32f2f", weight: 6, dashArray: "8 6", interactive: false },
    ))).addTo(this.map) : null;
    this.line = line.length > 1
      ? L.polyline(line.map((point) => this.toLatLng(point[0], point[1])), { color: "#2e7dd7", weight: 3, interactive: false }).addTo(this.map)
      : null;
    this.markers = points.map((point) => {
      const current = isSelected(point, selected);
      const marker = L.marker(this.toLatLng(point.east_m, point.north_m), {
        draggable: point.draggable !== false,
        autoPan: true,
        zIndexOffset: current ? 1000 : point.kind === "waypoint" ? 0 : 500,
        icon: L.divIcon({
          className: "route-point",
          html: `<span class="${current ? "selected" : ""} flight-${point.kind}">${point.label}</span>`,
          iconSize: [24, 24],
        }),
      }).bindTooltip(point.name || point.label).addTo(this.map);
      marker.on("click", (event) => {
        L.DomEvent.stopPropagation(event);
        this.onSelect(point);
      });
      marker.on("dragend", () => {
        const { east, north } = this.toLocal(marker.getLatLng());
        this.onMove(point, east, north);
      });
      return marker;
    });
  }
}

export class FlightView extends PlacementView {
  constructor(container, { onSelect }) {
    super(container, { onSelect: () => {}, onMove: () => {}, onTurn: () => {} });
    this.onSelectPoint = onSelect;
    this.flightGroup = new THREE.Group();
    this.scene.add(this.flightGroup);
  }

  // line: [{east_m, north_m, up_m, ground_m, label, kind, index}];
  // conflicts: [{from, to, at: [e, n, u]}] with 1-based line point numbers.
  setFlight(line, selected, conflicts = []) {
    this.flightGroup.clear();
    this.line = line;
    if (!line.length) return;
    const position = (point) => new THREE.Vector3(point.east_m, point.up_m, -point.north_m);
    const blocked = new Set(conflicts.map((conflict) => conflict.from));
    for (let index = 0; index + 1 < line.length; index += 1) {
      const color = blocked.has(index + 1) ? 0xd32f2f : 0x2e7dd7;
      const geometry = new THREE.BufferGeometry().setFromPoints([position(line[index]), position(line[index + 1])]);
      this.flightGroup.add(new THREE.Line(geometry, new THREE.LineBasicMaterial({ color, linewidth: 2 })));
    }
    line.forEach((point, index) => {
      const current = isSelected(point, selected);
      const color = new THREE.Color(KIND_COLORS[point.kind] ?? "#2e7dd7");
      const ball = new THREE.Mesh(
        new THREE.SphereGeometry(current ? 0.9 : 0.6, 16, 12),
        new THREE.MeshStandardMaterial({ color, emissive: current ? 0x554400 : 0x000000 }),
      );
      ball.position.copy(position(point));
      ball.userData.flightPoint = index;
      this.flightGroup.add(ball);
      // One label per place: T at the top of the climb, L over the landing point, each waypoint once.
      const labelled = point.kind === "waypoint" ? !point.again
        : point.kind === "takeoff" ? index === 1 : !point.stand;
      if (labelled) {
        const label = labelSprite(point.label, KIND_COLORS[point.kind] ?? "#2e7dd7");
        label.position.copy(position(point)).add(new THREE.Vector3(0, current ? 2.4 : 1.9, 0));
        this.flightGroup.add(label);
      }
      if (point.ground_m !== null && point.ground_m !== undefined && point.up_m - point.ground_m > 0.6) {
        // The stem down to the top of the World under the point: its height above the ground.
        const stem = new THREE.BufferGeometry().setFromPoints([
          position(point), new THREE.Vector3(point.east_m, point.ground_m, -point.north_m)]);
        const line = new THREE.Line(stem, new THREE.LineDashedMaterial({ color: 0x555555, dashSize: 0.6, gapSize: 0.4 }));
        line.computeLineDistances();
        this.flightGroup.add(line);
      }
    });
    for (const conflict of conflicts) {
      const hit = new THREE.Mesh(new THREE.SphereGeometry(0.7, 12, 8), new THREE.MeshBasicMaterial({ color: 0xd32f2f }));
      hit.position.set(conflict.at[0], conflict.at[2], -conflict.at[1]);
      this.flightGroup.add(hit);
    }
  }

  focusPoint(index) {
    const point = this.line?.[index];
    if (point) this.focusOn(new THREE.Vector3(point.east_m, point.up_m, -point.north_m));
  }

  // The whole flight in view.
  focusFlight() {
    if (!this.line?.length) return this.overview();
    const box = new THREE.Box3();
    for (const point of this.line) box.expandByPoint(new THREE.Vector3(point.east_m, point.up_m, -point.north_m));
    const centre = box.getCenter(new THREE.Vector3());
    const size = Math.max(20, box.getSize(new THREE.Vector3()).length());
    this.flyTo(centre, centre.clone().add(new THREE.Vector3(-0.6, 0.7, 0.6).normalize().multiplyScalar(size * 1.2)));
  }

  pointerDown(event) {
    this.ray(event);
    const hit = this.raycaster.intersectObjects(this.flightGroup.children, false)
      .find((intersection) => intersection.object.userData.flightPoint !== undefined);
    if (hit) {
      this.onSelectPoint(this.line[hit.object.userData.flightPoint]);
      return;
    }
    super.pointerDown(event);
  }
}

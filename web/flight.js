// Flight tab views: a Drone flight on the City World map and in 3D.
//
// The map edits the flight: click to add a waypoint (or to place the takeoff
// or landing point while picking one), drag a point to move it. The 3D view
// shows the same line at its heights over the World GLB: each point has a
// stem down to the top of the World under it (its height above the ground),
// and a leg that meets the World is red, with a red ball where it meets it.
// Both take the flight's line: [{east_m, north_m, up_m, ground_m, label, kind}]
// where kind is "takeoff", "waypoint" (with its waypoint index) or "land",
// and the wind / fault zones (flight_zones.js): see-through boxes (cyan wind
// with an arrow where it blows, red fault), their footprints on the map.

import * as THREE from "three";
import { zoneColor, zoneText } from "./flight_zones.js";
import { MapView } from "./map.js";
import { PlacementView } from "./placement.js";

const KIND_COLORS = { takeoff: "#2e9d4f", waypoint: "#2e7dd7", land: "#d0572a" };

// selected: a waypoint's index, or "takeoff" / "land".
const isSelected = (point, selected) => (point.kind === "waypoint" ? point.index === selected : point.kind === selected);

// A text label that always faces the camera.
export function labelSprite(text, color) {
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
  setFlight(points, line, selected, blocked = [], zones = []) {
    if (!this.map) return;
    for (const marker of this.markers) marker.remove();
    this.line?.remove();
    this.blocked?.remove();
    this.zones?.remove();
    this.zones = L.layerGroup(zones.map((zone) => L.polygon(
      zone.corners.slice(0, 4).map(([east, north]) => this.toLatLng(east, north)),
      { color: zoneColor(zone), weight: 2, fillColor: zoneColor(zone), fillOpacity: 0.2, interactive: false },
    ).bindTooltip(zoneText(zone)))).addTo(this.map);
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

// The faces and edges of a zone's box: corners 0-3 the bottom face, 4-7 the top.
const BOX_FACES = [[0, 1, 2, 3], [4, 5, 6, 7], [0, 1, 5, 4], [1, 2, 6, 5], [2, 3, 7, 6], [3, 0, 4, 7]];
const BOX_EDGES = [[0, 1], [1, 2], [2, 3], [3, 0], [4, 5], [5, 6], [6, 7], [7, 4], [0, 4], [1, 5], [2, 6], [3, 7]];

// A zone in the 3D view: its see-through box, coloured edges, the wind's arrow.
function addZone(group, zone) {
  const color = zoneColor(zone);
  const corners = zone.corners.map(([east, north, up]) => new THREE.Vector3(east, up, -north));
  const positions = BOX_FACES.flatMap(([a, b, c, d]) => [a, b, c, a, c, d].flatMap((index) => corners[index].toArray()));
  const box = new THREE.BufferGeometry();
  box.setAttribute("position", new THREE.Float32BufferAttribute(positions, 3));
  const fill = new THREE.Mesh(box, new THREE.MeshBasicMaterial({
    color, transparent: true, opacity: 0.18, side: THREE.DoubleSide, depthWrite: false,
  }));
  fill.renderOrder = 3;
  group.add(fill);
  const edges = new THREE.BufferGeometry().setFromPoints(BOX_EDGES.flatMap(([a, b]) => [corners[a], corners[b]]));
  group.add(new THREE.LineSegments(edges, new THREE.LineBasicMaterial({ color })));
  const centre = corners.reduce((sum, corner) => sum.add(corner), new THREE.Vector3()).multiplyScalar(1 / 8);
  if (zone.wind) {
    const theta = (Number(zone.wind.towards_deg) * Math.PI) / 180;
    const ends = [corners[0].clone().add(corners[1]).multiplyScalar(0.5), corners[2].clone().add(corners[3]).multiplyScalar(0.5)];
    const length = Math.max(1, Math.min(4, Math.hypot(ends[1].x - ends[0].x, ends[1].z - ends[0].z)));
    const arrow = new THREE.ArrowHelper(new THREE.Vector3(Math.cos(theta), 0, -Math.sin(theta)),
      centre.clone().sub(new THREE.Vector3(Math.cos(theta), 0, -Math.sin(theta)).multiplyScalar(length / 2)),
      length, 0x26c6da, Math.min(1, length * 0.3), Math.min(0.6, length * 0.2));
    arrow.renderOrder = 6;
    group.add(arrow);
  }
  const label = labelSprite(zone.fault ? "!" : "風", color);
  label.scale.set(1.4, 1.4, 1);
  label.position.copy(centre).add(new THREE.Vector3(0, Math.max(...corners.map((corner) => corner.y)) - centre.y + 1.0, 0));
  group.add(label);
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
  setFlight(line, selected, conflicts = [], zones = []) {
    this.flightGroup.clear();
    this.line = line;
    if (!line.length) return;
    for (const zone of zones) addZone(this.flightGroup, zone);
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

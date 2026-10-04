// Route tab map: edit a Car route scenario's waypoints on a City World map.
//
// Click the map to append a point, drag a point to move it, click a point to
// select it. Points are local ENU metres around the World origin (the same
// frame as vehicle spawns); the loop closes from the last point to the first.

import * as THREE from "three";
import { labelSprite } from "./flight.js";
import { bandOutline, frictionColor, legFrictions, legWidths } from "./friction.js";
import { MapView } from "./map.js";
import { PlacementView } from "./placement.js";

export class RouteMapView extends MapView {
  constructor(container, { onAdd, onSelect, onMove }) {
    super(container, { onPick: onAdd, onSelect, onMove });
    this.line = null;
  }

  setRoute(points, selected, conflicts = [], roadWidth = 6) {
    if (!this.map) return;
    for (const marker of this.markers) marker.remove();
    this.line?.remove();
    this.blocked?.remove();
    this.bands?.remove();
    // Where each leg's road friction holds: its band, filled in its colour.
    const frictionsForBands = legFrictions(points);
    const widths = legWidths(points, roadWidth);
    const legs = points.length > 2 ? points.length : points.length - 1;
    this.bands = L.layerGroup(Array.from({ length: Math.max(0, legs) }, (_, index) => index)
      .filter((index) => frictionsForBands[index] !== undefined)
      .map((index) => L.polygon(
        bandOutline(points[index], points[(index + 1) % points.length], widths[index])
          .map(([east, north]) => this.toLatLng(east, north)),
        { stroke: false, fillColor: frictionColor(frictionsForBands[index]), fillOpacity: 0.35, interactive: false },
      ))).addTo(this.map);
    // Segments that run into a building wall (from the last save's check).
    this.blocked = conflicts.length ? L.layerGroup(conflicts.map((conflict) => L.polyline(
      [conflict.from - 1, conflict.to - 1].map((index) => this.toLatLng(points[index].east_m, points[index].north_m)),
      { color: "#d32f2f", weight: 6, dashArray: "8 6", interactive: false },
    ))).addTo(this.map) : null;
    const latlngs = points.map((point) => this.toLatLng(point.east_m, point.north_m));
    const frictions = legFrictions(points);
    if (latlngs.length > 1 && frictions.some((value) => value !== undefined)) {
      // Each leg in its road friction's colour (friction.js).
      this.line = L.layerGroup(latlngs.slice(0, latlngs.length > 2 ? undefined : -1).map((latlng, index) => L.polyline(
        [latlng, latlngs[(index + 1) % latlngs.length]],
        { color: frictionColor(frictions[index]), weight: 4, interactive: false },
      ))).addTo(this.map);
    } else {
      this.line = latlngs.length > 1
        ? L.polygon(latlngs, { color: "#2e7dd7", weight: 3, fill: false, interactive: false }).addTo(this.map)
        : null;
    }
    this.markers = points.map((point, index) => {
      const current = index === selected;
      const marker = L.marker(latlngs[index], {
        draggable: true,
        autoPan: true,
        zIndexOffset: current ? 1000 : 0,
        icon: L.divIcon({
          className: "route-point",
          html: `<span class="${current ? "selected" : ""}${point.dwell_sec ? " dwell" : ""}">${index + 1}</span>`,
          iconSize: [24, 24],
        }),
      }).bindTooltip(point.name || `point-${index + 1}`).addTo(this.map);
      marker.on("click", (event) => {
        L.DomEvent.stopPropagation(event);
        this.onSelect(index);
      });
      marker.on("dragend", () => {
        const { east, north } = this.toLocal(marker.getLatLng());
        this.onMove(index, east, north);
      });
      return marker;
    });
  }
}

// A leg's band in the 3D view: a strip widthM wide along the leg's samples
// (scene frame), a little under the line, see-through.
function bandMesh(leg, widthM, color) {
  const first = leg[0];
  const last = leg[leg.length - 1];
  const along = new THREE.Vector3(last.x - first.x, 0, last.z - first.z).normalize();
  const side = new THREE.Vector3(-along.z, 0, along.x).multiplyScalar(widthM / 2);
  const positions = [];
  for (const point of leg) {
    const y = point.y - 0.2;
    positions.push(point.x + side.x, y, point.z + side.z, point.x - side.x, y, point.z - side.z);
  }
  const indices = [];
  for (let index = 0; index + 1 < leg.length; index += 1) {
    const a = index * 2;
    indices.push(a, a + 1, a + 2, a + 1, a + 3, a + 2);
  }
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute("position", new THREE.Float32BufferAttribute(positions, 3));
  geometry.setIndex(indices);
  const mesh = new THREE.Mesh(geometry, new THREE.MeshBasicMaterial({
    color, transparent: true, opacity: 0.35, side: THREE.DoubleSide, depthWrite: false,
  }));
  mesh.renderOrder = 4;
  return mesh;
}

// Route tab 3D view: the route on the World GLB at the height of the surface
// under it (POST worlds/<id>/route-line: a road under a bridge stays on the
// road), each route point a numbered ball, the legs coloured by their road
// friction when the route sets one (friction.js), and the legs that run into
// a building wall red.
export class RouteView3D extends PlacementView {
  constructor(container, { onSelect }) {
    super(container, { onSelect: () => {}, onMove: () => {}, onTurn: () => {} });
    this.onSelectPoint = onSelect;
    this.routeGroup = new THREE.Group();
    this.scene.add(this.routeGroup);
  }

  // line: [{east_m, north_m, up_m}] (the loop, sampled); corners: the index in
  // it of each route point; conflicts: [{from, to}] with 1-based point numbers;
  // routePoints: the route's points (their road_friction).
  setRoute(line, corners, selected, conflicts = [], routePoints = [], roadWidth = 6) {
    this.routeGroup.clear();
    this.line = line;
    this.corners = corners;
    if (line.length < 2) return;
    const position = (point) => new THREE.Vector3(point.east_m, point.up_m, -point.north_m);
    const loop = [...line.map(position), position(line[0])];
    const frictions = legFrictions(routePoints);
    const widths = legWidths(routePoints, roadWidth);
    if (corners.length === routePoints.length && frictions.some((value) => value !== undefined)) {
      corners.forEach((start, index) => {
        const end = index + 1 < corners.length ? corners[index + 1] : line.length;
        const leg = loop.slice(start, end + 1);
        if (leg.length < 2) return;
        this.routeGroup.add(new THREE.Line(new THREE.BufferGeometry().setFromPoints(leg),
          new THREE.LineBasicMaterial({ color: frictionColor(frictions[index]) })));
        if (frictions[index] !== undefined) {
          this.routeGroup.add(bandMesh(leg, widths[index], frictionColor(frictions[index])));
        }
      });
    } else {
      this.routeGroup.add(new THREE.Line(new THREE.BufferGeometry().setFromPoints(loop),
        new THREE.LineBasicMaterial({ color: 0x2e7dd7 })));
    }
    for (const conflict of conflicts) {
      // The samples of that leg, from its point to the next one (or round to the first).
      const start = corners[conflict.from - 1];
      const end = conflict.to - 1 < corners.length && conflict.to > conflict.from ? corners[conflict.to - 1] : line.length;
      if (start === undefined) continue;
      const leg = loop.slice(start, end + 1);
      if (leg.length < 2) continue;
      this.routeGroup.add(new THREE.Line(new THREE.BufferGeometry().setFromPoints(leg),
        new THREE.LineBasicMaterial({ color: 0xd32f2f })));
    }
    corners.forEach((at, index) => {
      const point = line[at];
      if (!point) return;
      const current = index === selected;
      const ball = new THREE.Mesh(new THREE.SphereGeometry(current ? 0.7 : 0.45, 16, 12),
        new THREE.MeshStandardMaterial({ color: 0x2e7dd7, emissive: current ? 0x554400 : 0x000000 }));
      ball.position.copy(position(point));
      ball.userData.routePoint = index;
      this.routeGroup.add(ball);
      const label = labelSprite(String(index + 1), "#2e7dd7");
      label.position.copy(position(point)).add(new THREE.Vector3(0, current ? 2.0 : 1.6, 0));
      this.routeGroup.add(label);
    });
  }

  focusPoint(index) {
    const point = this.line?.[this.corners?.[index]];
    if (point) this.focusOn(new THREE.Vector3(point.east_m, point.up_m, -point.north_m));
  }

  // The whole route in view.
  focusRoute() {
    if (!this.line?.length) return this.overview();
    const box = new THREE.Box3();
    for (const point of this.line) box.expandByPoint(new THREE.Vector3(point.east_m, point.up_m, -point.north_m));
    const centre = box.getCenter(new THREE.Vector3());
    const size = Math.max(20, box.getSize(new THREE.Vector3()).length());
    this.flyTo(centre, centre.clone().add(new THREE.Vector3(-0.6, 0.7, 0.6).normalize().multiplyScalar(size * 1.1)));
  }

  pointerDown(event) {
    this.ray(event);
    const hit = this.raycaster.intersectObjects(this.routeGroup.children, false)
      .find((intersection) => intersection.object.userData.routePoint !== undefined);
    if (hit) {
      this.onSelectPoint(hit.object.userData.routePoint);
      return;
    }
    super.pointerDown(event);
  }
}

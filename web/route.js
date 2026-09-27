// Route tab map: edit a Car route scenario's waypoints on a City World map.
//
// Click the map to append a point, drag a point to move it, click a point to
// select it. Points are local ENU metres around the World origin (the same
// frame as vehicle spawns); the loop closes from the last point to the first.

import { MapView } from "./map.js";

export class RouteMapView extends MapView {
  constructor(container, { onAdd, onSelect, onMove }) {
    super(container, { onPick: onAdd, onSelect, onMove });
    this.line = null;
  }

  setRoute(points, selected, conflicts = []) {
    if (!this.map) return;
    for (const marker of this.markers) marker.remove();
    this.line?.remove();
    this.blocked?.remove();
    // Segments that run into a building wall (from the last save's check).
    this.blocked = conflicts.length ? L.layerGroup(conflicts.map((conflict) => L.polyline(
      [conflict.from - 1, conflict.to - 1].map((index) => this.toLatLng(points[index].east_m, points[index].north_m)),
      { color: "#d32f2f", weight: 6, dashArray: "8 6", interactive: false },
    ))).addTo(this.map) : null;
    const latlngs = points.map((point) => this.toLatLng(point.east_m, point.north_m));
    this.line = latlngs.length > 1
      ? L.polygon(latlngs, { color: "#2e7dd7", weight: 3, fill: false, interactive: false }).addTo(this.map)
      : null;
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

// Map placement view (City Worlds): Leaflet with OpenStreetMap tiles.
//
// Drag a marker to move that vehicle, or click the map to move the selected
// vehicle there. Local ENU metres around the City
// origin use a flat-Earth approximation, accurate to centimetres over a City
// World extent (a few hundred metres).

const METRES_PER_DEGREE = 111320;
// OpenStreetMap tiles exist up to zoom 19.
const MAX_ZOOM = 19;

export class MapView {
  constructor(container, { onPick, onSelect, onMove }) {
    this.container = container;
    this.onPick = onPick;
    this.onSelect = onSelect;
    this.onMove = onMove;
    this.map = null;
    this.markers = [];
  }

  toLatLng(east, north) {
    const { latitude, longitude } = this.origin;
    return [
      latitude + north / METRES_PER_DEGREE,
      longitude + east / (METRES_PER_DEGREE * Math.cos((latitude * Math.PI) / 180)),
    ];
  }

  toLocal(latlng) {
    const { latitude, longitude } = this.origin;
    return {
      east: (latlng.lng - longitude) * METRES_PER_DEGREE * Math.cos((latitude * Math.PI) / 180),
      north: (latlng.lat - latitude) * METRES_PER_DEGREE,
    };
  }

  setWorld(info) {
    if (!info.origin || !window.L) return false;
    this.origin = info.origin;
    if (!this.map) {
      this.map = L.map(this.container, { zoomControl: true, maxZoom: MAX_ZOOM });
      L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
        maxZoom: MAX_ZOOM, attribution: "&copy; OpenStreetMap contributors",
      }).addTo(this.map);
      this.map.on("click", (event) => {
        const { east, north } = this.toLocal(event.latlng);
        this.onPick(east, north);
      });
    }
    if (this.worldId !== info.id) {
      this.worldId = info.id;
      this.extent?.remove();
      const { north_south: halfNorth, east_west: halfEast } = info.half_extent_m;
      this.extent = L.rectangle([this.toLatLng(-halfEast, -halfNorth), this.toLatLng(halfEast, halfNorth)], {
        color: "#1f6feb", weight: 2, fill: false, interactive: false,
      }).addTo(this.map);
      this.fitted = false;
      this.fit();
    }
    return true;
  }

  setVehicles(vehicles, selected) {
    if (!this.map) return;
    for (const marker of this.markers) marker.remove();
    this.markers = vehicles.map((vehicle, index) => {
      const size = index === selected ? 20 : 14;
      const color = { car: "#2e7dd7", drone: "#e08a1e", fpv: "#9b5de5" }[vehicle.kind] || "#888888";
      const border = index === selected ? "#ffb300" : "#ffffff";
      const marker = L.marker(this.toLatLng(vehicle.east, vehicle.north), {
        draggable: true,
        autoPan: true,
        zIndexOffset: index === selected ? 1000 : 0,
        icon: L.divIcon({
          className: "map-vehicle",
          html: `<span style="background:${color};border-color:${border}"></span>`,
          iconSize: [size, size],
        }),
      }).bindTooltip(vehicle.name).addTo(this.map);
      marker.on("click", (event) => {
        L.DomEvent.stopPropagation(event);
        this.onSelect(index);
      });
      // Selecting re-renders the markers, which would end the drag; onMove selects on release.
      marker.on("dragend", () => {
        const { east, north } = this.toLocal(marker.getLatLng());
        this.onMove(index, east, north);
      });
      return marker;
    });
  }

  // A hidden container has no size, so the extent is fitted once it is shown.
  fit() {
    if (this.fitted || !this.container.clientWidth) return;
    this.map.fitBounds(this.extent.getBounds(), { padding: [20, 20] });
    this.fitted = true;
  }

  show() {
    if (!this.map) return;
    this.map.invalidateSize();
    this.fit();
  }

  // Closed route loops (lists of {east_m, north_m}) drawn under the markers.
  setRoutes(routes) {
    if (!this.map) return;
    this.routes?.remove();
    this.routes = routes?.length ? L.layerGroup(routes.map((points) => L.polygon(
      points.map((point) => this.toLatLng(point.east_m, point.north_m)),
      { color: "#2e7dd7", weight: 3, fill: false, dashArray: "6 4", interactive: false },
    ))).addTo(this.map) : null;
  }

  // Building outlines (City World collision walls) drawn under the markers.
  setFootprints(buildings, visible = true) {
    if (!this.map) return;
    this.footprints?.remove();
    this.footprints = null;
    if (!visible || !buildings?.length) return;
    const ring = (points) => points.map(([east, north]) => this.toLatLng(east, north));
    // The outline plus its courtyards as holes, so open ground inside a building stays clear.
    this.footprints = L.layerGroup(buildings.map((building) => L.polygon(
      [ring(building.vertices), ...(building.holes || []).map(ring)],
      { color: "#d32f2f", weight: 1, fillColor: "#d32f2f", fillOpacity: 0.3, interactive: false },
    ))).addTo(this.map);
  }
}

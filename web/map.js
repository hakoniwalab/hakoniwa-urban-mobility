// Map placement view (City Worlds): Leaflet with OpenStreetMap tiles.
//
// A click moves the selected vehicle there. Local ENU metres around the City
// origin use a flat-Earth approximation, accurate to centimetres over a City
// World extent (a few hundred metres).

const METRES_PER_DEGREE = 111320;
// OpenStreetMap tiles exist up to zoom 19.
const MAX_ZOOM = 19;

export class MapView {
  constructor(container, { onPick, onSelect }) {
    this.container = container;
    this.onPick = onPick;
    this.onSelect = onSelect;
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
      const marker = L.circleMarker(this.toLatLng(vehicle.east, vehicle.north), {
        radius: index === selected ? 9 : 6,
        color: index === selected ? "#ffb300" : "#ffffff",
        weight: 2,
        fillColor: { car: "#2e7dd7", drone: "#e08a1e", fpv: "#9b5de5" }[vehicle.kind] || "#888888",
        fillOpacity: 1,
      }).bindTooltip(vehicle.name).addTo(this.map);
      marker.on("click", (event) => {
        L.DomEvent.stopPropagation(event);
        this.onSelect(index);
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
}

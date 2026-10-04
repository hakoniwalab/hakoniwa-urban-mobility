// Road friction colours for the route lines (Route tab map and 3D view).
//
// A route point's road_friction is that of the leg from it to the next point
// (apps/car/scenario_executor.py TireFriction); a leg without one keeps the
// model's tire friction and the usual blue line. The bands follow the
// guideline table (docs/asset-contract.md section 4.4), from grippy to
// slippery; the Viewer uses the same colours (hakoniwa-threejs-drone
// src/flight_path.js). Ice is purple, not red: red marks legs into a wall.

export const FRICTION_BANDS = [
  { min: 0.7, color: "#43a047", label: "乾燥" },
  { min: 0.35, color: "#fdd835", label: "濡れ" },
  { min: 0.15, color: "#fb8c00", label: "雪" },
  { min: 0, color: "#8e24aa", label: "氷" },
];

export const ROUTE_COLOR = "#2e7dd7";  // a leg without road_friction

export function frictionColor(value) {
  if (!Number.isFinite(value)) return ROUTE_COLOR;
  return FRICTION_BANDS.find((band) => value >= band.min).color;
}

// The road friction of each leg (the leg from point i to the next one):
// undefined where the point sets none, all undefined when no point does.
export function legFrictions(points) {
  return points.map((point) => (typeof point?.road_friction === "number" && Number.isFinite(point.road_friction)
    ? point.road_friction : undefined));
}

// Each leg's road width: its point's road_width_m, else the route's.
export function legWidths(points, roadWidth) {
  return points.map((point) => (typeof point?.road_width_m === "number" && point.road_width_m > 0
    ? point.road_width_m : roadWidth));
}

// The band where a leg's road friction holds (apps/car/route_geometry.py
// leg_at): width wide round the leg from a to b, its ends round. Returns the
// outline [[east, north], ...].
export function bandOutline(a, b, width, arcSteps = 8) {
  const dx = b.east_m - a.east_m;
  const dy = b.north_m - a.north_m;
  const heading = Math.atan2(dy, dx);
  const half = width / 2;
  const arc = (centre, from) => Array.from({ length: arcSteps + 1 }, (_, step) => {
    const angle = from + (Math.PI * step) / arcSteps;
    return [centre.east_m + half * Math.cos(angle), centre.north_m + half * Math.sin(angle)];
  });
  // Round b's end from the left side to the right, then a's back.
  return [...arc(b, heading + Math.PI / 2 - Math.PI), ...arc(a, heading + Math.PI / 2)];
}

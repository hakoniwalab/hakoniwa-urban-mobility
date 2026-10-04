// Road friction colours for the route lines (Route tab map and 3D view).
//
// A route point's road_friction holds to the next point that sets one, round
// the loop (apps/car/scenario_executor.py TireFriction). The bands follow the
// guideline table (docs/asset-contract.md section 4.4), from grippy to
// slippery; the Viewer uses the same colours (hakoniwa-threejs-drone
// src/flight_path.js). Ice is purple, not red: red marks legs into a wall.

export const FRICTION_BANDS = [
  { min: 0.7, color: "#43a047", label: "乾燥" },
  { min: 0.35, color: "#fdd835", label: "濡れ" },
  { min: 0.15, color: "#fb8c00", label: "雪" },
  { min: 0, color: "#8e24aa", label: "氷" },
];

export function frictionColor(value) {
  return FRICTION_BANDS.find((band) => value >= band.min)?.color;
}

// The road friction of each leg (the leg from point i to the next one), or
// all undefined when no point sets one.
export function legFrictions(points) {
  const set = (point) => typeof point?.road_friction === "number" && Number.isFinite(point.road_friction);
  const marked = points.filter(set);
  if (!marked.length) return points.map(() => undefined);
  let current = Number(marked[marked.length - 1].road_friction);
  return points.map((point) => {
    if (set(point)) current = Number(point.road_friction);
    return current;
  });
}

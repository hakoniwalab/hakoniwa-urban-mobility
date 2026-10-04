// Wind and rotor-fault zones of a Drone flight (apps/drone/flight_events.py).
//
// A waypoint's wind / fault acts on the leg from it to the next point of the
// flight's line: a box zone_width_m wide across the leg and zone_height_m tall
// round its line (sheared along a climbing leg), half its width longer at each
// end (a Drone holding at the waypoint is inside it); the waypoint's own size
// overrides the drone's (default 2 m x 2 m). These are the Python zones'
// corners, for the Flight tab map and 3D view; the Viewer draws the ones the
// builder writes (hakoniwa-threejs-drone flight_path.js).

export const DEFAULT_ZONE_WIDTH_M = 2.0;
export const DEFAULT_ZONE_HEIGHT_M = 2.0;
export const ZONE_COLORS = { wind: "#26c6da", fault: "#e53935" };

const positive = (value, fallback) => (Number.isFinite(Number(value)) && Number(value) > 0 ? Number(value) : fallback);

// The box's 8 corners [east, north, up]: the bottom face (a-left, a-right,
// b-right, b-left), then the top face in the same order (flight_events.Zone.corners).
export function zoneCorners(a, b, width, height) {
  let de = b.east_m - a.east_m;
  let dn = b.north_m - a.north_m;
  const length = Math.hypot(de, dn);
  const halfW = width / 2;
  const halfH = height / 2;
  let ends;
  if (length < 1e-6) {  // a vertical leg: a square column from one height to the other
    de = 1;
    dn = 0;
    const low = Math.min(a.up_m, b.up_m) - halfH;
    const high = Math.max(a.up_m, b.up_m) + halfH;
    ends = [[a.east_m - halfW, a.north_m, low, high], [a.east_m + halfW, a.north_m, low, high]];
  } else {
    de /= length;
    dn /= length;
    // Half the width past each end (flight_events.Zone).
    ends = [[a.east_m - de * halfW, a.north_m - dn * halfW, a.up_m - halfH, a.up_m + halfH],
      [b.east_m + de * halfW, b.north_m + dn * halfW, b.up_m - halfH, b.up_m + halfH]];
  }
  const left = [-dn * halfW, de * halfW];
  const footprint = [[ends[0], 1], [ends[0], -1], [ends[1], -1], [ends[1], 1]];
  const bottom = footprint.map(([end, side]) => [end[0] + side * left[0], end[1] + side * left[1], end[2]]);
  const top = footprint.map(([end, side]) => [end[0] + side * left[0], end[1] + side * left[1], end[3]]);
  return [...bottom, ...top];
}

// The zones of a drone's waypoints from the flight's checked line
// ([{east_m, north_m, up_m, kind, index, again}]): [{label, corners, wind?, fault?}].
export function flightZones(line, points, drone) {
  const zones = [];
  line.forEach((item, at) => {
    if (item.kind !== "waypoint" || item.again || !Number.isFinite(item.up_m)) return;
    const point = points[item.index];
    if (!point || (!point.wind && !point.fault)) return;
    const next = line[at + 1] && Number.isFinite(line[at + 1].up_m) ? line[at + 1] : item;
    zones.push({
      label: point.name,
      index: item.index,
      corners: zoneCorners(item, next,
        positive(point.zone_width_m, positive(drone.zone_width_m, DEFAULT_ZONE_WIDTH_M)),
        positive(point.zone_height_m, positive(drone.zone_height_m, DEFAULT_ZONE_HEIGHT_M))),
      ...(point.wind ? { wind: point.wind } : {}),
      ...(point.fault ? { fault: point.fault } : {}),
    });
  });
  return zones;
}

export const zoneColor = (zone) => (zone.fault ? ZONE_COLORS.fault : ZONE_COLORS.wind);

export function zoneText(zone) {
  const parts = [];
  if (zone.wind) parts.push(`風 ${Number(zone.wind.speed_m_s).toFixed(1).replace(/\.0$/, "")}m/s`);
  if (zone.fault) parts.push(`故障 ${zone.fault.rotors.map((rotor) => `R${rotor}`).join(",")}`);
  return parts.join(" ");
}

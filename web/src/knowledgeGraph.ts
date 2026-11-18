export interface GraphPoint {x: number; y: number}

export function findGraphDropTarget(point: GraphPoint, draggedId: string, targets: Array<{id: string; position: GraphPoint; radius: number}>) {
  let nearestId: string | undefined;
  let nearestDistance = Infinity;
  for (const {id, position, radius} of targets) {
    if (id === draggedId) continue;
    const distance = Math.hypot(point.x - position.x, point.y - position.y);
    if (distance <= radius && distance < nearestDistance) {
      nearestDistance = distance;
      nearestId = id;
    }
  }
  return nearestId;
}

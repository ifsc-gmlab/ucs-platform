#!/usr/bin/env python3
"""Small deterministic 2D visibility-graph planner for semantic missions."""

import heapq
import math


DEFAULT_CLEARANCE_M = 1.25
_EPSILON = 1e-6


def _expanded_bounds(obstacle, clearance_m):
    center = obstacle.get("center_m", [0.0, 0.0])
    size = obstacle.get("size_m", [0.0, 0.0])
    half_x = float(size[0]) / 2.0 + float(clearance_m)
    half_y = float(size[1]) / 2.0 + float(clearance_m)
    return (
        float(center[0]) - half_x,
        float(center[0]) + half_x,
        float(center[1]) - half_y,
        float(center[1]) + half_y,
    )


def segment_crosses_box_interior(start, end, obstacle, clearance_m=DEFAULT_CLEARANCE_M):
    """Return True only if a segment enters an expanded box's open interior.

    Touching a visibility-graph corner or following an expanded boundary is
    allowed. The physical obstacle still has ``clearance_m`` separation.
    """
    x_min, x_max, y_min, y_max = _expanded_bounds(obstacle, clearance_m)
    x_min += _EPSILON
    x_max -= _EPSILON
    y_min += _EPSILON
    y_max -= _EPSILON
    t_min, t_max = 0.0, 1.0
    for origin, delta, lower, upper in (
            (float(start[0]), float(end[0]) - float(start[0]), x_min, x_max),
            (float(start[1]), float(end[1]) - float(start[1]), y_min, y_max)):
        if abs(delta) < _EPSILON:
            if origin <= lower or origin >= upper:
                return False
            continue
        t1 = (lower - origin) / delta
        t2 = (upper - origin) / delta
        if t1 > t2:
            t1, t2 = t2, t1
        t_min = max(t_min, t1)
        t_max = min(t_max, t2)
        if t_min > t_max:
            return False
    return t_max > _EPSILON and t_min < 1.0 - _EPSILON


def segment_is_clear(start, end, obstacles, clearance_m=DEFAULT_CLEARANCE_M):
    return not any(
        segment_crosses_box_interior(start, end, obstacle, clearance_m)
        for obstacle in obstacles)


def intersected_obstacle_ids(start, end, obstacles, clearance_m=DEFAULT_CLEARANCE_M):
    return [
        str(obstacle.get("id", "unknown"))
        for obstacle in obstacles
        if segment_crosses_box_interior(start, end, obstacle, clearance_m)
    ]


def _point_inside_box(point, obstacle, clearance_m):
    x_min, x_max, y_min, y_max = _expanded_bounds(obstacle, clearance_m)
    return (
        x_min + _EPSILON < float(point[0]) < x_max - _EPSILON and
        y_min + _EPSILON < float(point[1]) < y_max - _EPSILON
    )


def path_distance(points):
    return sum(
        math.hypot(
            float(right[0]) - float(left[0]),
            float(right[1]) - float(left[1]))
        for left, right in zip(points, points[1:])
    )


def plan_safe_route(start, goal, obstacles, clearance_m=DEFAULT_CLEARANCE_M):
    """Plan the shortest collision-free polyline around axis-aligned boxes.

    Returns a dict with ``points`` (including start and goal), ``distance_m``
    and ``detour_waypoints``. Returns ``None`` when start/goal is inside an
    expanded obstacle or the visibility graph has no route.
    """
    start = (float(start[0]), float(start[1]))
    goal = (float(goal[0]), float(goal[1]))
    obstacles = list(obstacles or [])
    if any(_point_inside_box(start, item, clearance_m) for item in obstacles):
        return None
    if any(_point_inside_box(goal, item, clearance_m) for item in obstacles):
        return None

    nodes = [start, goal]
    for obstacle in obstacles:
        x_min, x_max, y_min, y_max = _expanded_bounds(
            obstacle, clearance_m)
        for corner in (
                (x_min, y_min), (x_min, y_max),
                (x_max, y_min), (x_max, y_max)):
            if not any(
                    _point_inside_box(corner, other, clearance_m)
                    for other in obstacles if other is not obstacle):
                nodes.append(corner)

    unique_nodes = []
    for node in nodes:
        if not any(
                math.hypot(node[0] - old[0], node[1] - old[1]) < _EPSILON
                for old in unique_nodes):
            unique_nodes.append(node)
    nodes = unique_nodes

    graph = [[] for _ in nodes]
    for left in range(len(nodes)):
        for right in range(left + 1, len(nodes)):
            if not segment_is_clear(
                    nodes[left], nodes[right], obstacles, clearance_m):
                continue
            distance = math.hypot(
                nodes[right][0] - nodes[left][0],
                nodes[right][1] - nodes[left][1])
            graph[left].append((right, distance))
            graph[right].append((left, distance))

    best = [float("inf")] * len(nodes)
    previous = [None] * len(nodes)
    best[0] = 0.0
    queue = [(0.0, 0)]
    while queue:
        distance, node = heapq.heappop(queue)
        if distance > best[node] + _EPSILON:
            continue
        if node == 1:
            break
        for neighbour, edge_distance in graph[node]:
            candidate = distance + edge_distance
            if candidate + _EPSILON < best[neighbour]:
                best[neighbour] = candidate
                previous[neighbour] = node
                heapq.heappush(queue, (candidate, neighbour))
    if not math.isfinite(best[1]):
        return None

    indices = []
    node = 1
    while node is not None:
        indices.append(node)
        node = previous[node]
    indices.reverse()
    points = [nodes[index] for index in indices]
    return {
        "points": [[round(x, 4), round(y, 4)] for x, y in points],
        "distance_m": round(path_distance(points), 4),
        "detour_waypoints": max(0, len(points) - 2),
        "clearance_m": float(clearance_m),
    }

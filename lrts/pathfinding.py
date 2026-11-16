"""A* on a 4-connected grid (decision D22). Pure function: no globals, deterministic.

Ties in the priority queue are broken by an insertion counter, so the same inputs
always give the same path (a heap of bare tuples would compare the nodes instead).
"""
import heapq

DIRS = ((0, -1), (1, 0), (0, 1), (-1, 0))


def find_path(start, goal, size, blocked, max_expand=2500):
    """Return the list of tiles from just after `start` up to and including `goal`.

    `blocked` is a set of (x, y). The goal itself may be blocked (a building or a
    mine we want to stand next to); callers cut the path when in range. Returns []
    if no path is found within `max_expand` expansions (or start == goal).
    """
    if start == goal:
        return []
    gx, gy = goal
    open_heap = [(abs(start[0] - gx) + abs(start[1] - gy), 0, 0, start)]
    best = {start: 0}
    came = {}
    counter = 0
    expanded = 0
    while open_heap:
        _, g, _, cur = heapq.heappop(open_heap)
        if cur == goal:
            out = []
            while cur != start:
                out.append(cur)
                cur = came[cur]
            out.reverse()
            return out
        if g > best.get(cur, 1 << 30):
            continue
        expanded += 1
        if expanded > max_expand:
            return []
        cx, cy = cur
        for dx, dy in DIRS:
            nx, ny = cx + dx, cy + dy
            if not (0 <= nx < size and 0 <= ny < size):
                continue
            nxt = (nx, ny)
            if nxt in blocked and nxt != goal:
                continue
            ng = g + 1
            if ng < best.get(nxt, 1 << 30):
                best[nxt] = ng
                came[nxt] = cur
                counter += 1
                heapq.heappush(open_heap, (ng + abs(nx - gx) + abs(ny - gy), ng, counter, nxt))
    return []

"""Bounded deterministic graph algorithms, independent of runtime state."""


def topological_order(nodes):
    by_id = {n.node_id: n for n in nodes}
    if len(by_id) != len(nodes):
        raise ValueError('duplicate node_id')
    for node in nodes:
        if len(set(node.depends_on)) != len(node.depends_on):
            raise ValueError('duplicate dependency')
        if any(dep not in by_id for dep in node.depends_on):
            raise ValueError('missing dependency')
    remaining = {n.node_id: set(n.depends_on) for n in nodes}
    order = []
    while remaining:
        ready = sorted(id for id, deps in remaining.items() if not deps)
        if not ready:
            raise ValueError('cycle detected')
        order.extend(ready)
        for id in ready:
            remaining.pop(id)
        for deps in remaining.values():
            deps.difference_update(ready)
    return order


def descendants(nodes, roots):
    affected = set(roots)
    for id in topological_order(nodes):
        node = next(n for n in nodes if n.node_id == id)
        if any(dep in affected for dep in node.depends_on):
            affected.add(id)
    return affected


def ancestors(nodes, id):
    result = set()
    by_id = {n.node_id: n for n in nodes}

    def visit(current):
        for dep in by_id[current].depends_on:
            if dep not in result:
                result.add(dep)
                visit(dep)
    visit(id)
    return result

"""Grupos y proyectos de una cuenta como un árbol n-ario de dos niveles (NaryTree de app.structures).

La raíz es la cuenta; sus hijos son los grupos, en el orden dado; los hijos de cada grupo son sus
proyectos (salas), en el orden dado. GET /groups lista los grupos y cuenta sus proyectos con un recorrido
en profundidad de este árbol.
"""

from app.structures import NaryTree

GROUP_LEVEL = 1
PROJECT_LEVEL = 2


def build_project_tree(groups, projects):
    """Arma el árbol cuenta -> grupos -> proyectos.

    Args:
        groups: objetos con atributo id, en el orden en que se listarán.
        projects: pares (id del proyecto, id de su grupo). Los que no tienen grupo (None) o cuyo grupo no
            está en groups no entran en el árbol: no cuentan en ningún grupo.

    Returns:
        NaryTree cuya raíz tiene value None; los nodos de grupo guardan el grupo y los de proyecto su id.

    O(g + p): un dict id de grupo -> nodo da el padre de cada proyecto en O(1).
    """
    tree = NaryTree(None)
    group_nodes = {}
    for group in groups:
        group_nodes[group.id] = tree.add_child(tree.root, group)
    for project_id, group_id in projects:
        node = group_nodes.get(group_id)
        if node is not None:
            tree.add_child(node, project_id)
    return tree


def groups_with_counts(tree):
    """[(grupo, número de proyectos)] en el orden del árbol, con UN recorrido en profundidad (preorden).

    En preorden, cada grupo (nivel 1) aparece justo antes de sus proyectos (nivel 2), así que un nodo de
    nivel 1 abre una entrada nueva y cada nodo de nivel 2 suma uno a la última. O(n).
    """
    result = []
    for node, level in tree.walk():
        if level == GROUP_LEVEL:
            result.append([node.value, 0])
        elif level == PROJECT_LEVEL:
            result[-1][1] += 1
    return [(group, count) for group, count in result]

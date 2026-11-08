"""Grupos y proyectos de una cuenta como un árbol n-ario (NaryTree de app.structures).

La raíz es la cuenta. Sus hijos son primero los grupos, en el orden dado, y después los proyectos sin grupo
(o cuyo grupo no está en la lista), en el orden dado. Los hijos de cada grupo son sus proyectos (salas), en
el orden dado. GET /groups lista los grupos y cuenta sus proyectos con un recorrido en profundidad de este
árbol; los proyectos sin grupo están en el árbol pero no cuentan en ningún grupo.
"""

from dataclasses import dataclass

from app.structures import NaryTree

GROUP_LEVEL = 1
PROJECT_LEVEL = 2


@dataclass(frozen=True)
class ProjectEntry:
    """Valor de un nodo de proyecto: su id. Distingue en el nivel 1 un proyecto sin grupo de un grupo."""

    id: int


def build_project_tree(groups, projects):
    """Arma el árbol cuenta -> grupos -> proyectos, con los proyectos sin grupo colgando de la raíz.

    Args:
        groups: objetos con atributo id, en el orden en que se listarán.
        projects: pares (id del proyecto, id de su grupo), en orden. Los que no tienen grupo (None) o cuyo
            grupo no está en groups pasan a ser hijos directos de la raíz, detrás de todos los grupos.

    Returns:
        NaryTree cuya raíz tiene value None; los nodos de grupo guardan el grupo y los de proyecto un
        ProjectEntry con su id.

    O(g + p): un dict id de grupo -> nodo da el padre de cada proyecto en O(1).
    """
    tree = NaryTree(None)
    group_nodes = {}
    for group in groups:
        group_nodes[group.id] = tree.add_child(tree.root, group)
    ungrouped = []
    for project_id, group_id in projects:
        node = group_nodes.get(group_id)
        if node is None:
            ungrouped.append(project_id)
        else:
            tree.add_child(node, ProjectEntry(project_id))
    # Se añaden al final para que todos los grupos vayan antes que los proyectos sueltos.
    for project_id in ungrouped:
        tree.add_child(tree.root, ProjectEntry(project_id))
    return tree


def groups_with_counts(tree):
    """[(grupo, número de proyectos)] en el orden del árbol, con UN recorrido en profundidad (preorden).

    En preorden, cada grupo (nivel 1) aparece justo antes de sus proyectos (nivel 2), así que un grupo de
    nivel 1 abre una entrada nueva y cada nodo de nivel 2 suma uno a la última. Los proyectos sin grupo
    (ProjectEntry en el nivel 1) no abren entrada ni suman a ninguna. O(n).
    """
    result = []
    for node, level in tree.walk():
        if level == GROUP_LEVEL:
            if not isinstance(node.value, ProjectEntry):
                result.append([node.value, 0])
        elif level == PROJECT_LEVEL:
            result[-1][1] += 1
    return [(group, count) for group, count in result]

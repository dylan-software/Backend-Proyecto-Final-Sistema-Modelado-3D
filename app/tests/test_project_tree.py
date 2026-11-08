"""Árbol cuenta -> grupos -> proyectos, con los sin grupo en la raíz (app/services/project_tree.py)."""

import random
from collections import Counter
from dataclasses import dataclass

from app.services.project_tree import ProjectEntry, build_project_tree, groups_with_counts


@dataclass(frozen=True)
class Group:
    id: int
    name: str


def test_groups_and_projects_form_a_two_level_tree():
    first, second = Group(1, "Casa"), Group(2, "Oficina")
    tree = build_project_tree([first, second], [(10, 1), (11, 2), (12, 1)])
    tree.check_invariants()
    assert tree.root.value is None
    assert [(node.value, level) for node, level in tree.walk()] == [
        (None, 0),
        (first, 1),
        (ProjectEntry(10), 2),
        (ProjectEntry(12), 2),
        (second, 1),
        (ProjectEntry(11), 2),
    ]


def test_counts_follow_the_group_order_and_include_empty_groups():
    groups = [Group(3, "c"), Group(1, "a"), Group(2, "b")]
    tree = build_project_tree(groups, [(5, 1), (6, 1), (7, 3)])
    assert groups_with_counts(tree) == [(groups[0], 1), (groups[1], 2), (groups[2], 0)]


def test_projects_without_group_or_of_an_unknown_group_hang_from_the_root():
    group = Group(1, "a")
    tree = build_project_tree([group], [(5, None), (6, 99), (7, 1), (8, None)])
    tree.check_invariants()
    assert len(tree) == 6  # raíz, grupo y cuatro proyectos
    # El recorrido lista los grupos con sus proyectos y después los proyectos sueltos, en su orden.
    assert [(node.value, level) for node, level in tree.walk()] == [
        (None, 0),
        (group, 1),
        (ProjectEntry(7), 2),
        (ProjectEntry(5), 1),
        (ProjectEntry(6), 1),
        (ProjectEntry(8), 1),
    ]
    # El recuento por grupo no cambia: los sueltos no cuentan en ningún grupo.
    assert groups_with_counts(tree) == [(group, 1)]


def test_an_account_without_groups_lists_nothing_but_keeps_its_projects():
    tree = build_project_tree([], [(1, None), (2, 7)])
    assert groups_with_counts(tree) == []
    assert [(node.value, level) for node, level in tree.walk()] == [
        (None, 0),
        (ProjectEntry(1), 1),
        (ProjectEntry(2), 1),
    ]


def test_ungrouped_projects_after_a_group_do_not_add_to_its_count():
    # El último grupo va justo antes de los proyectos sueltos en el preorden: no debe absorberlos.
    first, last = Group(1, "a"), Group(2, "b")
    tree = build_project_tree([first, last], [(5, None), (6, 2), (7, None)])
    assert groups_with_counts(tree) == [(first, 0), (last, 1)]


def test_counts_match_a_counter_and_the_walk_lists_every_project_on_random_accounts():
    for seed in range(30):
        rng = random.Random(seed)
        groups = [Group(i, f"g{i}") for i in rng.sample(range(1, 80), rng.randint(0, 50))]
        ids = [g.id for g in groups] + [None, 999]
        projects = [(p, rng.choice(ids)) for p in range(rng.randint(0, 300))]
        reference = Counter(group_id for _, group_id in projects)
        tree = build_project_tree(groups, projects)
        tree.check_invariants()
        # Mismo recuento que antes de meter los sueltos en el árbol.
        assert groups_with_counts(tree) == [(g, reference.get(g.id, 0)) for g in groups]
        # El recorrido contiene cada proyecto exactamente una vez; los sueltos, en el nivel 1 y en su orden.
        known = {g.id for g in groups}
        walked = [(node.value, level) for node, level in tree.walk() if isinstance(node.value, ProjectEntry)]
        assert sorted(entry.id for entry, _ in walked) == [p for p, _ in projects]
        loose = [entry.id for entry, level in walked if level == 1]
        assert loose == [p for p, g in projects if g not in known]
        assert len(tree) == 1 + len(groups) + len(projects)

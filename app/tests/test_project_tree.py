"""Árbol de dos niveles cuenta -> grupos -> proyectos (app/services/project_tree.py)."""

import random
from collections import Counter
from dataclasses import dataclass

from app.services.project_tree import build_project_tree, groups_with_counts


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
        (10, 2),
        (12, 2),
        (second, 1),
        (11, 2),
    ]


def test_counts_follow_the_group_order_and_include_empty_groups():
    groups = [Group(3, "c"), Group(1, "a"), Group(2, "b")]
    tree = build_project_tree(groups, [(5, 1), (6, 1), (7, 3)])
    assert groups_with_counts(tree) == [(groups[0], 1), (groups[1], 2), (groups[2], 0)]


def test_projects_without_group_or_of_an_unknown_group_are_left_out():
    group = Group(1, "a")
    tree = build_project_tree([group], [(5, None), (6, 99), (7, 1)])
    assert len(tree) == 3  # raíz, grupo y un proyecto
    assert groups_with_counts(tree) == [(group, 1)]


def test_an_account_without_groups_lists_nothing():
    assert groups_with_counts(build_project_tree([], [(1, None), (2, 7)])) == []


def test_counts_match_a_counter_on_random_accounts():
    for seed in range(30):
        rng = random.Random(seed)
        groups = [Group(i, f"g{i}") for i in rng.sample(range(1, 80), rng.randint(0, 50))]
        ids = [g.id for g in groups] + [None, 999]
        projects = [(p, rng.choice(ids)) for p in range(rng.randint(0, 300))]
        reference = Counter(group_id for _, group_id in projects)
        tree = build_project_tree(groups, projects)
        tree.check_invariants()
        assert groups_with_counts(tree) == [(g, reference.get(g.id, 0)) for g in groups]

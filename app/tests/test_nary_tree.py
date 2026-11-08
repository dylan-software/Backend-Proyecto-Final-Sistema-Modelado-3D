"""NaryTree frente a un modelo de referencia con dict y list (solo como referencia)."""

import random

import pytest

from app.structures import CapacityError, InvariantError, NaryTree, TreeNode

OPERATIONS = 3000


class ReferenceTree:
    """Modelo trivial: id -> lista de ids hijos, con preorden recursivo de la librería estándar."""

    def __init__(self, root_value):
        self.values = {0: root_value}
        self.children = {0: []}
        self.parent = {0: None}
        self.next_id = 1

    def add(self, parent_id, value):
        new_id = self.next_id
        self.next_id += 1
        self.values[new_id] = value
        self.children[new_id] = []
        self.parent[new_id] = parent_id
        self.children[parent_id].append(new_id)
        return new_id

    def remove(self, node_id):
        removed = self.preorder(node_id)
        self.children[self.parent[node_id]].remove(node_id)
        for inner in removed:
            del self.values[inner], self.children[inner], self.parent[inner]
        return len(removed)

    def preorder(self, node_id=0):
        result = [node_id]
        for child in self.children[node_id]:
            result.extend(self.preorder(child))
        return result

    def depth(self, node_id):
        levels = 0
        while self.parent[node_id] is not None:
            node_id = self.parent[node_id]
            levels += 1
        return levels


def assert_same(tree, nodes, reference):
    tree.check_invariants()
    order = reference.preorder()
    assert len(tree) == len(order)
    assert list(tree.values()) == [reference.values[i] for i in order]
    assert list(tree) == list(tree.values())
    walked = list(tree.walk())
    assert [node for node, _ in walked] == [nodes[i] for i in order]
    assert [level for _, level in walked] == [reference.depth(i) for i in order]
    for node_id in order:
        node = nodes[node_id]
        assert [child.value for child in node.children()] == [
            reference.values[c] for c in reference.children[node_id]
        ]
        assert node.child_count == len(reference.children[node_id])
        assert node.is_leaf() == (not reference.children[node_id])


@pytest.mark.parametrize("seed", [1, 2, 3, 4, 5])
def test_random_operations_match_the_reference(seed):
    rng = random.Random(seed)
    tree, reference = NaryTree("raíz"), ReferenceTree("raíz")
    nodes = {0: tree.root}

    for step in range(OPERATIONS):
        alive = list(reference.values)
        target = rng.choice(alive)
        action = rng.random()
        if action < 0.6:
            new_id = reference.add(target, step)
            nodes[new_id] = tree.add_child(nodes[target], step)
        elif action < 0.75:
            if target == 0:
                with pytest.raises(ValueError):
                    tree.remove(tree.root)
            else:
                assert tree.remove(nodes[target]) == reference.remove(target)
                for gone in [i for i in nodes if i not in reference.values]:
                    del nodes[gone]
        elif action < 0.85:
            assert tree.depth(nodes[target]) == reference.depth(target)
            assert tree.count(nodes[target]) == len(reference.preorder(target))
        else:
            subtree = reference.preorder(target)
            assert [node.value for node in tree.depth_first(nodes[target])] == [
                reference.values[i] for i in subtree
            ]
            base = reference.depth(target)
            assert [level for _, level in tree.walk(nodes[target])] == [
                reference.depth(i) - base for i in subtree
            ]
        if step % 50 == 0:
            assert_same(tree, nodes, reference)
    assert_same(tree, nodes, reference)


def test_a_new_tree_has_only_its_root():
    tree = NaryTree("cuenta")
    tree.check_invariants()
    assert len(tree) == 1
    assert tree.root.value == "cuenta"
    assert tree.root.is_leaf()
    assert list(tree) == ["cuenta"]
    assert list(tree.walk()) == [(tree.root, 0)]
    assert tree.count() == 1
    assert tree.depth(tree.root) == 0


def test_children_keep_insertion_order_and_preorder_goes_left_to_right():
    tree = NaryTree("cuenta")
    first = tree.add_child(tree.root, "grupo 1")
    second = tree.add_child(tree.root, "grupo 2")
    tree.add_child(first, "p1")
    tree.add_child(second, "p3")
    tree.add_child(first, "p2")
    assert list(tree) == ["cuenta", "grupo 1", "p1", "p2", "grupo 2", "p3"]
    assert [(n.value, d) for n, d in tree.walk()] == [
        ("cuenta", 0),
        ("grupo 1", 1),
        ("p1", 2),
        ("p2", 2),
        ("grupo 2", 1),
        ("p3", 2),
    ]
    assert tree.count(first) == 3
    assert first.child_count == 2


def test_removing_the_first_middle_and_last_child_relinks_the_siblings():
    tree = NaryTree(0)
    a, b, c, d = (tree.add_child(tree.root, v) for v in "abcd")
    tree.add_child(b, "b1")
    assert tree.remove(b) == 2
    assert [n.value for n in tree.root.children()] == ["a", "c", "d"]
    tree.remove(a)
    tree.remove(d)
    assert tree.root.first_child is c and tree.root.last_child is c
    assert c.prev_sibling is None and c.next_sibling is None
    tree.remove(c)
    assert tree.root.is_leaf() and len(tree) == 1
    tree.check_invariants()


def test_a_removed_subtree_no_longer_belongs_to_the_tree():
    tree = NaryTree(0)
    group = tree.add_child(tree.root, "g")
    project = tree.add_child(group, "p")
    tree.remove(group)
    for gone in (group, project):
        assert gone.owner is None
        with pytest.raises(ValueError):
            tree.add_child(gone, "x")
        with pytest.raises(ValueError):
            tree.remove(gone)
        with pytest.raises(ValueError):
            tree.depth(gone)
        with pytest.raises(ValueError):
            list(tree.walk(gone))


def test_nodes_of_another_tree_or_other_objects_are_rejected():
    tree, other = NaryTree(0), NaryTree(0)
    foreign = other.add_child(other.root, 1)
    for bad in (foreign, other.root, TreeNode(5), "no es un nodo", None):
        with pytest.raises(ValueError):
            tree.add_child(bad, 1)
    with pytest.raises(ValueError):
        tree.remove(tree.root)
    with pytest.raises(ValueError):
        tree.count(foreign)


def test_capacity_counts_the_root_and_frees_space_after_remove():
    tree = NaryTree("r", capacity=3)
    first = tree.add_child(tree.root, 1)
    tree.add_child(first, 2)
    assert tree.is_full()
    with pytest.raises(CapacityError):
        tree.add_child(tree.root, 3)
    assert len(tree) == 3
    tree.remove(first)
    assert not tree.is_full()
    tree.add_child(tree.root, 4)
    tree.check_invariants()


@pytest.mark.parametrize("capacity", [0, -1, 1.5, True, "3"])
def test_invalid_capacity_is_rejected(capacity):
    with pytest.raises(ValueError):
        NaryTree(0, capacity=capacity)


def test_capacity_one_is_just_the_root():
    tree = NaryTree(0, capacity=1)
    with pytest.raises(CapacityError):
        tree.add_child(tree.root, 1)


def test_a_very_deep_tree_is_walked_without_recursion():
    tree = NaryTree(0)
    node = tree.root
    for value in range(1, 20_000):
        node = tree.add_child(node, value)
    assert tree.count() == 20_000
    assert tree.depth(node) == 19_999
    assert list(tree.values())[-1] == 19_999
    tree.check_invariants()


def test_a_wide_tree_keeps_every_child():
    tree = NaryTree(0)
    for value in range(5000):
        tree.add_child(tree.root, value)
    assert tree.root.child_count == 5000
    assert [n.value for n in tree.root.children()] == list(range(5000))


def test_check_invariants_detects_broken_links():
    tree = NaryTree(0)
    a = tree.add_child(tree.root, "a")
    b = tree.add_child(tree.root, "b")
    b.prev_sibling = None
    with pytest.raises(InvariantError):
        tree.check_invariants()
    b.prev_sibling = a
    tree.root.child_count = 5
    with pytest.raises(InvariantError):
        tree.check_invariants()
    tree.root.child_count = 2
    a.parent = b
    with pytest.raises(InvariantError):
        tree.check_invariants()
    a.parent = tree.root
    tree.root.last_child = a
    with pytest.raises(InvariantError):
        tree.check_invariants()
    tree.root.last_child = b
    b.next_sibling = a  # ciclo en la lista de hijos
    with pytest.raises(InvariantError):
        tree.check_invariants()
    b.next_sibling = None
    tree.check_invariants()

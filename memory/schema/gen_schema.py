"""Generate the seed dump `slater-build` consumes to create the Graphiti graph.

Run it, commit the output. The generated `.cypher` is what the build reads, so the image
build stays hermetic and does not need graphiti-core installed.

    python schema/gen_schema.py > schema/graphiti-schema.cypher

## Why a seed dump exists at all

Slater declares indexes when a generation is **built**, not at runtime — that is what
lets it promise a query's cost is knowable from the manifest. And a label or
relationship type only enters the symbol table through an actual node or edge: the
builder interns them from MERGE records, never from index declarations. So a graph built
from index DDL alone would reject `SET n:Person` on the first write, because `Person`
would not exist.

Hence: every label needs a seed node and every relationship type a seed edge.

Two rules that follow, and matter:

* **Never delete the seed rows.** `CALL slater.consolidate()` rebuilds the graph from the
  merged view. Lose a seed and its label goes with it, and every subsequent write
  carrying that label starts failing. They carry `group_id: '_slater_seed_'`, which no
  Graphiti call uses — every search leg filters on `group_id`, and `clear_graph` always
  passes explicit ones — so they are invisible to the application.
* **No comments in the dump.** The builder's dialect rejects `//` and `/* */`. The
  explanation lives here instead.

## The requirement is derived, not transcribed

The range indexes come from `graphiti_slater.schema.required_range_indexes()`, which
reads `get_range_indices()` out of the installed graphiti-core — the same function the
adapter's startup assertion checks against. So the dump this writes and the schema the
driver demands cannot drift apart, and upgrading graphiti-core changes both together.

**Regenerate and rebuild the graph when** graphiti-core's schema changes, the configured
entity types change, or the embedder's dimension changes.

## Full-text indexes are declared here too

Slater's full-text indexes are declared at build time like everything else. Node indexes
(`Entity`, `Episodic`, `Community`) see writes immediately through Slater's overlay arm;
a relationship index (`RELATES_TO`) is served from the built generation alone, so an edge
whose `fact` changed keeps its old text until `CALL slater.consolidate()`.
"""

from __future__ import annotations

import argparse
import sys

from graphiti_slater.schema import required_fulltext_indexes, required_range_indexes

#: Graphiti's own node labels.
CORE_LABELS = ['Entity', 'Episodic', 'Community', 'Saga']

#: The MCP server's default entity types. Graphiti applies these as a *second* label on
#: an `Entity` (`SET n:Person`), so each needs to exist in the symbol table. This is a
#: user-configurable open set — adding a custom entity type means regenerating and
#: rebuilding.
MCP_ENTITY_TYPES = [
    'Preference',
    'Requirement',
    'Procedure',
    'Location',
    'Event',
    'Organization',
    'Document',
    'Topic',
    'Person',
    'Object',
]

#: Fixed regardless of the configured `edge_types`: `get_entity_edge_save_query` always
#: emits `RELATES_TO` and puts the LLM-extracted name in `e.name`.
#:
#: Endpoints are named explicitly rather than derived from the labels so that no seed
#: edge is a self-loop — a seed exists to put a type in the symbol table, and it should
#: be the least unusual edge that does so.
RELTYPES = {
    'RELATES_TO': ('seed-entity', 'seed-person'),
    'MENTIONS': ('seed-episodic', 'seed-entity'),
    'HAS_MEMBER': ('seed-community', 'seed-entity'),
    'HAS_EPISODE': ('seed-saga', 'seed-episodic'),
    'NEXT_EPISODE': ('seed-episodic', 'seed-episodic-2'),
}

#: Labels of the endpoints above, so each MERGE can anchor on a business key.
SEED_LABEL = {
    'seed-entity': 'Entity',
    'seed-person': 'Entity',
    'seed-episodic': 'Episodic',
    'seed-episodic-2': 'Episodic',
    'seed-community': 'Community',
    'seed-saga': 'Saga',
}

SEED_GROUP = '_slater_seed_'

#: Seed value for the timestamp-shaped properties. A range index wants *a* value, and
#: this one parses: nothing should be able to trip over a seed row that reached a date
#: parser by some route nobody predicted.
SEED_TIME = '1970-01-01T00:00:00+00:00'

#: Properties whose seed value must be the timestamp, not the group marker.
TIME_PROPS = {'created_at', 'valid_at', 'invalid_at', 'expired_at'}


def seed_uuid(label: str) -> str:
    return f'seed-{label.lower()}'


def emit(dim: int) -> list[str]:
    out: list[str] = []

    for idx in required_range_indexes():
        out.append(idx.ddl)

    # Full-text indexes, derived from `get_fulltext_indices()` exactly as the range
    # indexes are derived from `get_range_indices()`. Both the properties and the
    # stopword list come from the installed graphiti-core, so what the index stores and
    # what a query looks up cannot drift apart — a term dropped at index time but kept at
    # query time simply stops matching, with nothing to show for it.
    #
    # `Community` gets one even though `build_communities` is off: the declaration costs
    # an empty index, and Graphiti's `community_fulltext_search` calls the procedure
    # unconditionally. A query naming an index the graph never declared is an error.
    for idx in required_fulltext_indexes():
        out.append(idx.ddl)

    # Only `Entity.name_embedding` is indexed, and deliberately so. An indexed embedding
    # is routed out of the property record into the vector store, so a column read of it
    # returns Null — which is exactly why the adapter routes *node* similarity through
    # `db.idx.vector.queryNodes`. Community similarity has no such override and runs
    # Graphiti's own column-scan leg, so indexing `Community.name_embedding` would
    # silently break it. Leave it an ordinary column.
    out.append(f"CALL db.idx.vector.createNodeIndex('Entity', 'name_embedding', {dim}, 'cosine');")

    # A seed embedding is a unit vector, not zeros: cosine distance against a zero vector
    # is undefined, and a seed row should never be able to poison a KNN result.
    unit = '[' + ', '.join(['1.0'] + ['0.0'] * (dim - 1)) + ']'

    for label in CORE_LABELS:
        props = f"n.group_id = '{SEED_GROUP}', n.name = '{SEED_GROUP}'"
        if label == 'Entity':
            props += f", n.summary = '', n.name_embedding = vecf32({unit})"
        elif label == 'Episodic':
            props = f"n.group_id = '{SEED_GROUP}', n.content = '', n.source = ''"
        props += f", n.created_at = '{SEED_TIME}'"
        out.append(f"MERGE (n:{label} {{uuid: '{seed_uuid(label)}'}}) SET {props};")
    # A second Episodic so `NEXT_EPISODE` has two distinct endpoints.
    out.append(
        f"MERGE (n:Episodic {{uuid: 'seed-episodic-2'}}) SET n.group_id = '{SEED_GROUP}', "
        f"n.content = '', n.source = '', n.created_at = '{SEED_TIME}';"
    )

    # The MCP entity types ride on an `Entity`, exactly as Graphiti writes them.
    for label in MCP_ENTITY_TYPES:
        out.append(
            f"MERGE (n:Entity:{label} {{uuid: '{seed_uuid(label)}'}}) "
            f"SET n.group_id = '{SEED_GROUP}', n.name = '{SEED_GROUP}', "
            f"n.created_at = '{SEED_TIME}';"
        )

    # One seed edge per relationship type, carrying every property its range index
    # declares — an index over a property no row has is legal but pointless, and the
    # seed is the cheapest place to prove the declaration and the data agree.
    edge_props = {
        idx.label: sorted(
            i.property for i in required_range_indexes() if i.entity == 'RELATIONSHIP'
            and i.label == idx.label
        )
        for idx in required_range_indexes()
        if idx.entity == 'RELATIONSHIP'
    }
    for i, (reltype, (src, dst)) in enumerate(RELTYPES.items(), start=1):
        def seed_value(p: str, i: int = i) -> str:
            if p == 'uuid':
                return f"r.uuid = 'seed-r{i}'"
            return f"r.{p} = '{SEED_TIME if p in TIME_PROPS else SEED_GROUP}'"

        sets = ', '.join(seed_value(p) for p in edge_props.get(reltype, ['uuid']))
        out.append(
            f"MERGE (a:{SEED_LABEL[src]} {{uuid: '{src}'}})-[r:{reltype}]->"
            f"(b:{SEED_LABEL[dst]} {{uuid: '{dst}'}}) SET {sets};"
        )
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        '--dim',
        type=int,
        default=1024,
        help="embedding dimension; must match the embedder's native output "
        '(voyage-3 is 1024). Slater hard-errors on a mismatch at write time.',
    )
    args = ap.parse_args()
    print('\n'.join(emit(args.dim)))
    return 0


if __name__ == '__main__':
    sys.exit(main())

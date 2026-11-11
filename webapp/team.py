"""Content for the team page.

Names, roles and the project framing come from the project wiki
(``wiki/Project-Documentation/Working-Agreement.md`` and ``Charter.md``).
"""

COURSE = {
    "name": "CSE 449 Senior Capstone",
    "term": "2026-2027",
    "institution": "Miami University",
    "summary": "A two-semester capstone building static analysis tooling for quantum "
               "programs, run as a Scrum team against a client and a faculty advisor.",
}

# Roles are from the Working Agreement's role-based accountability section.
# Every member is also an R&D developer.
MEMBERS = [
    {
        "name": "Max Lovell",
        "handle": "lovellmb",
        "role": "Team Lead / Developer",
        "year": "Senior",
        "focus": "Sprint reviews, merge approvals",
        "bio": "Accountable for the structural integrity of the main branch. Prepares the brief "
               "for each sprint review and approves the merge requests that land.",
        "links": {},
    },
    {
        "name": "Ethan Tackett",
        "handle": "tacketes",
        "role": "QA Lead / Scrum Master",
        "year": "Senior",
        "focus": "Definition of done, issue board",
        "bio": "Maintains the issues board and reviews work against the QA gates: unit tests, "
               "wiki documentation, commented code, and integration with the QDG pipeline. "
               "Holds a veto on anything that does not meet them.",
        "links": {},
    },
    {
        "name": "Caleb Turner",
        "handle": "turne249",
        "role": "R&D Developer",
        "year": "Senior",
        "focus": "Dependence graph and slicing",
        "bio": "Works on QDG construction and the slicing algorithms, and on keeping the "
               "intermediate representation honest as new dependency rules land.",
        "links": {},
    },
    {
        "name": "Dylan Kendall",
        "handle": "kendaldb",
        "role": "R&D Developer",
        "year": "Senior",
        "focus": "Entanglement analysis",
        "bio": "Works on the approximate entanglement analyzer, including the uncomputation "
               "and measurement handling that opens and closes groups.",
        "links": {},
    },
]

# Charter, verbatim in substance.
VISION = (
    "A future where quantum software engineers can reason about relevance, causality, and "
    "debugging with the same clarity and rigor available in classical software engineering."
)

MISSION = (
    "To provide a foundation for understanding relevance in non-deterministic systems, by defining "
    "quantum-specific dependency rules, implementing a graph-based dependency model, and "
    "building a slicing engine."
)

PROJECT_BLURB = (
    "Program slicing is a decades-old idea in classical software engineering: given a point in "
    "a program, find the instructions that could have influenced it. Quantum programs break "
    "the assumptions that make classical slicing work, because entanglement links qubits that "
    "no single instruction names together and measurement destroys the state it reads. This "
    "project builds a dependence graph that accounts for those effects, and a slicer on top "
    "of it."
)

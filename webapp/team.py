"""Content for the team page.

Names, roles and the project framing come from the project wiki
(``wiki/Project-Documentation/Working-Agreement.md`` and ``Charter.md``).
Anything still marked TODO is a genuine gap rather than a guess: fill those in
and delete the note at the bottom of templates/team.html.
"""

COURSE = {
    "name": "CSE 449 Senior Capstone",  # TODO: the course's actual title and number
    "term": "2026-2027",
    "institution": "Miami University",
    "summary": "A two-semester capstone building static analysis tooling for quantum "
               "programs, run as a Scrum team against a client and a faculty advisor.",
}

# The Working Agreement names an instructor and a client who meet the team on
# alternating weeks. Neither is named in the wiki, so neither is named here.
ADVISOR = {
    "name": "Dr. Alomari",  # TODO
    "role": "Instructor and project advisor",
    "affiliation": "Miami University, Department of Computer Science and Software Engineering",
    "bio": "Meets the team biweekly on Mondays to review progress against the charter, and is "
           "the escalation point when the team cannot reach a decision on its own.",
}

CLIENT = {
    "name": "Project Client",  # TODO
    "role": "Client",
    "bio": "Meets the team biweekly on Fridays for a status review. Scope questions that the "
           "team cannot settle internally go here.",
}

# Roles are from the Working Agreement's role-based accountability section.
# Every member is also an R&D developer.
MEMBERS = [
    {
        "name": "Max Lovell",
        "handle": "",
        "role": "Team Lead",
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
               "wiki documentation, commented code, and integration with the QPDG pipeline. "
               "Holds a veto on anything that does not meet them.",
        "links": {},
    },
    {
        "name": "Caleb Turner",
        "handle": "turne249",
        "role": "R&D Developer",
        "year": "Senior",
        "focus": "Dependence graph and slicing",
        "bio": "Works on QPDG construction and the slicing algorithms, and on keeping the "
               "intermediate representation honest as new dependency rules land.",
        "links": {},
    },
    {
        "name": "Dylan Kendall",
        "handle": "kendaldb",
        "role": "R&D Developer",
        "year": "Senior",
        "focus": "Entanglement analysis",
        "bio": "Works on the approximate entanglement analyser, including the uncomputation "
               "and measurement handling that opens and closes groups.",
        "links": {},
    },
]

# Charter, verbatim in substance.
VISION = (
    "A future where quantum software engineers can reason about relevance, causality, and "
    "debugging with the same clarity and rigour available in classical software engineering."
)

MISSION = (
    "To provide a foundation for understanding relevance in deterministic systems, by defining "
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

# Success criteria from the charter. ``done`` drives the tick on the page, and
# is deliberately conservative: only tick what this repository demonstrates.
SUCCESS_CRITERIA = [
    {
        "group": "Quantitative",
        "items": [
            {"text": "A working slicer over at least five OpenQASM or Qiskit benchmark programs",
             "done": True,
             "note": "22 circuits ship with this site; both front ends reach the same slicer."},
            {"text": "Extraction of a QPDG with typed dependency edges",
             "done": True,
             "note": "Four rules build the graph; two more are implemented but disabled."},
            {"text": "Demonstrated slice size reduction",
             "done": True,
             "note": "Every slice on the slicer page reports its size as a fraction of the program."},
        ],
    },
    {
        "group": "Qualitative",
        "items": [
            {"text": "Clear documentation of quantum dependency assumptions",
             "done": True,
             "note": "In the wiki, and summarised on the Quantum Info page."},
            {"text": "Visual explanation of the dependency graph",
             "done": True,
             "note": "The graph view, plus the matplotlib viewers in scripts/."},
            {"text": "Demonstrated impact of entanglement on slice size",
             "done": False,
             "note": "The entanglement rule exists but ships disabled, so the comparison is "
                     "not yet something the site can show."},
        ],
    },
    {
        "group": "Sustainability",
        "items": [
            {"text": "Full documentation in the wiki", "done": True, "note": ""},
            {"text": "Architecture diagrams", "done": False,
             "note": "TODO: the wiki has the dependence-graph figure, not an architecture diagram."},
            {"text": "Dependency model specification", "done": True,
             "note": "wiki/Research/Quantum-Dependencies and its child pages."},
            {"text": "Quantum slicer available for use as a web app", "done": True,
             "note": "This site."},
        ],
    },
]

CONSTRAINTS = [
    "One semester timeline.",
    "Static analysis only -- nothing here simulates a circuit to decide a dependency.",
    "Conservative dependency approximations are allowed, and used.",
    "Familiarisation with quantum computing alongside the build.",
    "Varied coding language experience across the team.",
]

STANDARDS = [
    {
        "name": "IEEE 29148",
        "topic": "Requirements engineering",
        "use": "Formalises the slicing specification and the success criteria.",
        "phase": "Design and development",
    },
    {
        "name": "IEEE 1012",
        "topic": "Verification and validation",
        "use": "Structures the evaluation plan and the validation procedures.",
        "phase": "Testing",
    },
]

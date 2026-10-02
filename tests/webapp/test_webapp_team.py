"""team.py: the structure the team template relies on."""

import team

MEMBER_KEYS = {"name", "handle", "role", "year", "focus", "bio", "links"}


def test_members_structure():
    assert len(team.MEMBERS) >= 1
    names = [m["name"] for m in team.MEMBERS]
    assert len(names) == len(set(names))
    for member in team.MEMBERS:
        assert set(member) == MEMBER_KEYS
        for key in ("name", "role", "year", "focus", "bio"):
            assert isinstance(member[key], str) and member[key].strip(), (member["name"], key)
        assert isinstance(member["handle"], str)
        assert isinstance(member["links"], dict)


def test_roles_from_working_agreement_are_present():
    roles = " ".join(m["role"] for m in team.MEMBERS)
    assert "Team Lead" in roles
    assert "Scrum Master" in roles


def test_course_structure():
    assert set(team.COURSE) == {"name", "term", "institution", "summary"}
    assert all(isinstance(v, str) and v for v in team.COURSE.values())


def test_charter_text():
    for text in (team.VISION, team.MISSION, team.PROJECT_BLURB):
        assert isinstance(text, str) and len(text) > 40
    assert "slicing" in team.MISSION.lower() or "slicing" in team.PROJECT_BLURB.lower()

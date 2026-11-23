"""Engineering skill dictionary (plan section 7, Agent 5), seeded from the BRD example skills.

Matching is deterministic: each canonical skill has aliases matched on word boundaries. It is
used to tag JDs (Agent 5), resumes (Agents 9 and 10) and to score skill match (Agent 7), so the
same names appear everywhere. The Administrator can extend it later (Future Enhancements).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# category -> canonical skill -> aliases (the canonical name is always an alias too)
DICTIONARY: dict[str, dict[str, tuple[str, ...]]] = {
    "cad_tools": {
        "AutoCAD": ("autocad",),
        "SolidWorks": ("solidworks", "solid works"),
        "CATIA": ("catia",),
        "Creo": ("creo", "pro/e", "pro/engineer", "proe"),
        "NX": ("siemens nx", "unigraphics", "NX"),
        "Inventor": ("autodesk inventor",),
    },
    "software": {
        "ANSYS": ("ansys",),
        "HyperMesh": ("hypermesh", "hyper mesh"),
        "Abaqus": ("abaqus",),
        "LS-DYNA": ("ls-dyna", "lsdyna", "ls dyna"),
        "ANSA": ("ANSA",),
        "Nastran": ("nastran", "msc nastran"),
        "STAR-CCM+": ("star-ccm+", "starccm", "star ccm"),
        "Fluent": ("ansys fluent", "fluent"),
        "Minitab": ("minitab",),
    },
    "programming": {
        "Python": ("python",),
        "MATLAB": ("matlab", "simulink"),
        "C++": ("c++",),
        "VBA": ("VBA",),
        "LabVIEW": ("labview",),
    },
    "erp": {
        "SAP": ("SAP", "s/4hana"),
        "Oracle EBS": ("oracle ebs", "oracle e-business"),
        "Epicor": ("epicor",),
    },
    "plm": {
        "Teamcenter": ("teamcenter",),
        "Windchill": ("windchill",),
        "Enovia": ("enovia",),
    },
    "automation": {
        "PLC": ("PLC", "PLCs", "programmable logic controller"),
        "Rockwell": (
            "rockwell",
            "allen-bradley",
            "allen bradley",
            "controllogix",
            "studio 5000",
            "rslogix",
            "factorytalk",
        ),
        "Siemens PLC": ("tia portal", "step 7", "s7-1500", "s7-1200", "siemens plc"),
        "ABB": ("ABB",),
        "Fanuc": ("fanuc",),
        "KUKA": ("kuka",),
        "HMI / SCADA": ("HMI", "SCADA"),
    },
    "manufacturing_methods": {
        "GD&T": ("gd&t", "GDT", "geometric dimensioning"),
        "PFMEA": ("pfmea", "dfmea", "fmea"),
        "APQP": ("APQP",),
        "PPAP": ("PPAP",),
        "Control Plan": ("control plan", "control plans"),
        "8D": ("8D", "8-D"),
        "DOE": ("DOE", "design of experiments"),
        "SPC": ("SPC", "statistical process control"),
        "Injection Molding": ("injection molding", "injection moulding"),
        "CNC Machining": ("CNC", "machining"),
        "Welding": ("welding",),
    },
    "lean": {
        "Lean Manufacturing": (
            "lean manufacturing",
            "lean",
            "kaizen",
            "5s",
            "value stream mapping",
        ),
    },
    "six_sigma": {
        "Six Sigma": ("six sigma", "6 sigma", "green belt", "black belt", "dmaic"),
    },
}


@dataclass(frozen=True)
class SkillMatch:
    skill: str
    category: str


def _pattern(alias: str) -> re.Pattern[str]:
    # Word boundaries that also work for names with symbols (C++, GD&T, STAR-CCM+, 8D).
    # All-caps acronyms (DOE, NX, SAP, PLC) match case-sensitively, so "John Doe" is not DOE.
    flags = 0 if alias.upper() == alias and any(c.isalpha() for c in alias) else re.IGNORECASE
    return re.compile(rf"(?<![\w+#&]){re.escape(alias)}(?![\w+#&])", flags)


_COMPILED: list[tuple[str, str, re.Pattern[str]]] = [
    (category, skill, _pattern(alias))
    for category, skills in DICTIONARY.items()
    for skill, aliases in skills.items()
    for alias in sorted({skill, *aliases}, key=len, reverse=True)
]


def extract_skills(text: str) -> list[SkillMatch]:
    """Canonical skills mentioned in the text, in dictionary order, without duplicates."""
    found: dict[str, SkillMatch] = {}
    for category, skill, pattern in _COMPILED:
        if skill not in found and pattern.search(text):
            found[skill] = SkillMatch(skill, category)
    return list(found.values())


_EXACT: dict[str, str] = {
    alias.lower(): skill
    for skills in DICTIONARY.values()
    for skill, aliases in skills.items()
    for alias in (skill, *aliases)
}


def canonical(name: str) -> str:
    """Map a free-text skill to its canonical name, or return it trimmed if unknown.

    An exact name ignores case ("Ansa" -> "ANSA"); longer text falls back to matching.
    """
    exact = _EXACT.get(name.strip().lower())
    if exact:
        return exact
    matches = extract_skills(name)
    return matches[0].skill if len(matches) == 1 else name.strip()

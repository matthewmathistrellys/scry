#!/usr/bin/env python3
"""
CVSS scoring, standard library only -- the severity behind pins.py when an
advisory publishes a vector and no label.

WHY THIS EXISTS (2026-10-11). Most Hex advisories carry
`severity: [{"type": "CVSS_V4", "score": "CVSS:4.0/..."}]` and no
`database_specific.severity`, and the GHSA alias they name is usually not in
OSV at all (22 of 27 sampled on one repo returned 404). Unscored, they were
all "unrated" -- and an unrated advisory never reaches the startup line or the
status-line count, so a lockfile's worst offenders were the ones never said.

WHAT IS IMPLEMENTED
  * CVSS 4.0, as FIRST specifies it: the six equivalence classes (EQ1-EQ6)
    give a MacroVector, the MacroVector gives a score from the lookup table,
    and that score is lowered by the mean, over the classes that have a lower
    neighbour, of (maximal scoring difference x the vector's severity distance
    from the class's highest-severity vector / the class's depth). Threat and
    environmental metrics are honoured when a vector carries them (E, CR/IR/AR,
    and the M-prefixed overrides), as the reference calculator does.
  * CVSS 3.0 / 3.1 BASE score. Temporal and environmental metrics in a v3
    vector are ignored: what is wanted here is the published base severity.

Arithmetic is exact (integers and Fractions in tenths), so there is no
floating-point rounding to disagree about; the final step rounds half up to
one decimal (v4) or up to one decimal (v3 Roundup), as the specifications say.

Nothing here raises on bad input: a vector that is not well-formed scores None.

The v4 lookup table and the highest-severity-vector and depth data are taken
from FIRST's reference calculator, github.com/FIRSTdotorg/cvss-v4-calculator
(cvss_lookup.js, max_composed.js, max_severity.js), under this licence:

    Copyright (c) 2023 FIRST.ORG, Inc., Red Hat, and contributors
    SPDX-License-Identifier: BSD-2-Clause

    Redistribution and use in source and binary forms, with or without
    modification, are permitted provided that the following conditions are met:

    1. Redistributions of source code must retain the above copyright notice,
       this list of conditions and the following disclaimer.

    2. Redistributions in binary form must reproduce the above copyright
       notice, this list of conditions and the following disclaimer in the
       documentation and/or other materials provided with the distribution.

    THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
    AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
    IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE
    ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE
    LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR
    CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF
    SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS
    INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN
    CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE)
    ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
    POSSIBILITY OF SUCH DAMAGE.

Usage:
    python3 scanners/cvss_score.py 'CVSS:4.0/AV:N/AC:L/AT:N/PR:N/UI:N/VC:H/VI:H/VA:H/SC:N/SI:N/SA:N'
"""

import sys
from fractions import Fraction
from itertools import product

# ── CVSS 4.0 ──────────────────────────────────────────────────────────────

# MacroVector (EQ1..EQ6) -> score, in TENTHS. FIRST cvss_lookup.js, BSD-2-Clause.
V4_LOOKUP = {
    "000000": 100, "000001": 99, "000010": 98, "000011": 95, "000020": 95,
    "000021": 92, "000100": 100, "000101": 96, "000110": 93, "000111": 87,
    "000120": 91, "000121": 81, "000200": 93, "000201": 90, "000210": 89,
    "000211": 80, "000220": 81, "000221": 68, "001000": 98, "001001": 95,
    "001010": 95, "001011": 92, "001020": 90, "001021": 84, "001100": 93,
    "001101": 92, "001110": 89, "001111": 81, "001120": 81, "001121": 65,
    "001200": 88, "001201": 80, "001210": 78, "001211": 70, "001220": 69,
    "001221": 48, "002001": 92, "002011": 82, "002021": 72, "002101": 79,
    "002111": 69, "002121": 50, "002201": 69, "002211": 55, "002221": 27,
    "010000": 99, "010001": 97, "010010": 95, "010011": 92, "010020": 92,
    "010021": 85, "010100": 95, "010101": 91, "010110": 90, "010111": 83,
    "010120": 84, "010121": 71, "010200": 92, "010201": 81, "010210": 82,
    "010211": 71, "010220": 72, "010221": 53, "011000": 95, "011001": 93,
    "011010": 92, "011011": 85, "011020": 85, "011021": 73, "011100": 92,
    "011101": 82, "011110": 80, "011111": 72, "011120": 70, "011121": 59,
    "011200": 84, "011201": 70, "011210": 71, "011211": 52, "011220": 50,
    "011221": 30, "012001": 86, "012011": 75, "012021": 52, "012101": 71,
    "012111": 52, "012121": 29, "012201": 63, "012211": 29, "012221": 17,
    "100000": 98, "100001": 95, "100010": 94, "100011": 87, "100020": 91,
    "100021": 81, "100100": 94, "100101": 89, "100110": 86, "100111": 74,
    "100120": 77, "100121": 64, "100200": 87, "100201": 75, "100210": 74,
    "100211": 63, "100220": 63, "100221": 49, "101000": 94, "101001": 89,
    "101010": 88, "101011": 77, "101020": 76, "101021": 67, "101100": 86,
    "101101": 76, "101110": 74, "101111": 58, "101120": 59, "101121": 50,
    "101200": 72, "101201": 57, "101210": 57, "101211": 52, "101220": 52,
    "101221": 25, "102001": 83, "102011": 70, "102021": 54, "102101": 65,
    "102111": 58, "102121": 26, "102201": 53, "102211": 21, "102221": 13,
    "110000": 95, "110001": 90, "110010": 88, "110011": 76, "110020": 76,
    "110021": 70, "110100": 90, "110101": 77, "110110": 75, "110111": 62,
    "110120": 61, "110121": 53, "110200": 77, "110201": 66, "110210": 68,
    "110211": 59, "110220": 52, "110221": 30, "111000": 89, "111001": 78,
    "111010": 76, "111011": 67, "111020": 62, "111021": 58, "111100": 74,
    "111101": 59, "111110": 57, "111111": 57, "111120": 47, "111121": 23,
    "111200": 61, "111201": 52, "111210": 57, "111211": 29, "111220": 24,
    "111221": 16, "112001": 71, "112011": 59, "112021": 30, "112101": 58,
    "112111": 26, "112121": 15, "112201": 23, "112211": 13, "112221": 6,
    "200000": 93, "200001": 87, "200010": 86, "200011": 72, "200020": 75,
    "200021": 58, "200100": 86, "200101": 74, "200110": 74, "200111": 61,
    "200120": 56, "200121": 34, "200200": 70, "200201": 54, "200210": 52,
    "200211": 40, "200220": 40, "200221": 22, "201000": 85, "201001": 75,
    "201010": 74, "201011": 55, "201020": 62, "201021": 51, "201100": 72,
    "201101": 57, "201110": 55, "201111": 41, "201120": 46, "201121": 19,
    "201200": 53, "201201": 36, "201210": 34, "201211": 19, "201220": 19,
    "201221": 8, "202001": 64, "202011": 51, "202021": 20, "202101": 47,
    "202111": 21, "202121": 11, "202201": 24, "202211": 9, "202221": 4,
    "210000": 88, "210001": 75, "210010": 73, "210011": 53, "210020": 60,
    "210021": 50, "210100": 73, "210101": 55, "210110": 59, "210111": 40,
    "210120": 41, "210121": 20, "210200": 54, "210201": 43, "210210": 45,
    "210211": 22, "210220": 20, "210221": 11, "211000": 75, "211001": 55,
    "211010": 58, "211011": 45, "211020": 40, "211021": 21, "211100": 61,
    "211101": 51, "211110": 48, "211111": 18, "211120": 20, "211121": 9,
    "211200": 46, "211201": 18, "211210": 17, "211211": 7, "211220": 8,
    "211221": 2, "212001": 53, "212011": 24, "212021": 14, "212101": 24,
    "212111": 12, "212121": 5, "212201": 10, "212211": 3, "212221": 1,
}

# The highest-severity vector(s) of each class level. FIRST max_composed.js.
V4_MAX = {
    1: {0: ["AV:N/PR:N/UI:N"],
        1: ["AV:A/PR:N/UI:N", "AV:N/PR:L/UI:N", "AV:N/PR:N/UI:P"],
        2: ["AV:P/PR:N/UI:N", "AV:A/PR:L/UI:P"]},
    2: {0: ["AC:L/AT:N"], 1: ["AC:H/AT:N", "AC:L/AT:P"]},
    # EQ3, then EQ6
    3: {0: {0: ["VC:H/VI:H/VA:H/CR:H/IR:H/AR:H"],
            1: ["VC:H/VI:H/VA:L/CR:M/IR:M/AR:H", "VC:H/VI:H/VA:H/CR:M/IR:M/AR:M"]},
        1: {0: ["VC:L/VI:H/VA:H/CR:H/IR:H/AR:H", "VC:H/VI:L/VA:H/CR:H/IR:H/AR:H"],
            1: ["VC:L/VI:H/VA:L/CR:H/IR:M/AR:H", "VC:L/VI:H/VA:H/CR:H/IR:M/AR:M",
                "VC:H/VI:L/VA:H/CR:M/IR:H/AR:M", "VC:H/VI:L/VA:L/CR:M/IR:H/AR:H",
                "VC:L/VI:L/VA:H/CR:H/IR:H/AR:M"]},
        2: {1: ["VC:L/VI:L/VA:L/CR:H/IR:H/AR:H"]}},
    4: {0: ["SC:H/SI:S/SA:S"], 1: ["SC:H/SI:H/SA:H"], 2: ["SC:L/SI:L/SA:L"]},
    5: {0: ["E:A"], 1: ["E:P"], 2: ["E:U"]},
}

# Depth of each class level: its greatest severity distance, plus one, in
# steps of 0.1. FIRST max_severity.js.
V4_DEPTH = {
    1: {0: 1, 1: 4, 2: 5},
    2: {0: 1, 1: 2},
    3: {0: {0: 7, 1: 6}, 1: {0: 8, 1: 8}, 2: {1: 10}},
    4: {0: 6, 1: 5, 2: 4},
}

# Severity order of each metric's values, in steps (0 is the most severe).
V4_LEVELS = {
    "AV": {"N": 0, "A": 1, "L": 2, "P": 3},
    "PR": {"N": 0, "L": 1, "H": 2},
    "UI": {"N": 0, "P": 1, "A": 2},
    "AC": {"L": 0, "H": 1},
    "AT": {"N": 0, "P": 1},
    "VC": {"H": 0, "L": 1, "N": 2},
    "VI": {"H": 0, "L": 1, "N": 2},
    "VA": {"H": 0, "L": 1, "N": 2},
    "SC": {"H": 1, "L": 2, "N": 3},
    "SI": {"S": 0, "H": 1, "L": 2, "N": 3},
    "SA": {"S": 0, "H": 1, "L": 2, "N": 3},
    "CR": {"H": 0, "M": 1, "L": 2},
    "IR": {"H": 0, "M": 1, "L": 2},
    "AR": {"H": 0, "M": 1, "L": 2},
}
V4_CLASS_METRICS = {1: ("AV", "PR", "UI"), 2: ("AC", "AT"),
                    3: ("VC", "VI", "VA", "CR", "IR", "AR"), 4: ("SC", "SI", "SA")}

V4_BASE = {"AV": "NALP", "AC": "LH", "AT": "NP", "PR": "NLH", "UI": "NPA",
           "VC": "HLN", "VI": "HLN", "VA": "HLN", "SC": "HLN", "SI": "HLN", "SA": "HLN"}
V4_OPTIONAL = {
    "E": "XAPU", "CR": "XHML", "IR": "XHML", "AR": "XHML",
    "MAV": "XNALP", "MAC": "XLH", "MAT": "XNP", "MPR": "XNLH", "MUI": "XNPA",
    "MVC": "XHLN", "MVI": "XHLN", "MVA": "XHLN", "MSC": "XHLN",
    "MSI": "XSHLN", "MSA": "XSHLN",
    "S": "XNP", "AU": "XNY", "R": "XAUI", "V": "XDC", "RE": "XLMH",
    "U": ("X", "Clear", "Green", "Amber", "Red"),
}


def _metrics(vector, prefixes, required, optional):
    """{metric: value} for a well-formed vector, else None."""
    if not isinstance(vector, str):
        return None
    parts = vector.strip().split("/")
    if not parts or parts[0] not in prefixes:
        return None
    found = {}
    for part in parts[1:]:
        name, sep, value = part.partition(":")
        allowed = required.get(name) or optional.get(name)
        if not sep or not allowed or value not in allowed or name in found:
            return None
        found[name] = value
    if any(name not in found for name in required):
        return None
    return found


def _v4_effective(metrics):
    """The value each metric is scored at: a modified metric overrides its
    base metric, and a not-defined E / CR / IR / AR is its worst case."""
    def value(name):
        modified = metrics.get("M" + name, "X")
        if modified != "X":
            return modified
        chosen = metrics.get(name, "X")
        if chosen == "X":
            return "A" if name == "E" else "H"
        return chosen
    return {name: value(name) for name in list(V4_BASE) + ["E", "CR", "IR", "AR"]}


def _v4_macrovector(m):
    high = lambda *names: any(m[n] == "H" for n in names)
    none = [m["AV"] == "N", m["PR"] == "N", m["UI"] == "N"]
    if all(none):
        eq1 = 0
    elif any(none) and m["AV"] != "P":
        eq1 = 1
    else:
        eq1 = 2
    eq2 = 0 if m["AC"] == "L" and m["AT"] == "N" else 1
    if m["VC"] == "H" and m["VI"] == "H":
        eq3 = 0
    elif high("VC", "VI", "VA"):
        eq3 = 1
    else:
        eq3 = 2
    if m["SI"] == "S" or m["SA"] == "S":
        eq4 = 0
    elif high("SC", "SI", "SA"):
        eq4 = 1
    else:
        eq4 = 2
    eq5 = {"A": 0, "P": 1, "U": 2}[m["E"]]
    eq6 = 0 if ((m["CR"] == "H" and m["VC"] == "H") or (m["IR"] == "H" and m["VI"] == "H")
                or (m["AR"] == "H" and m["VA"] == "H")) else 1
    return (eq1, eq2, eq3, eq4, eq5, eq6)


def _v4_lookup(eqs):
    return V4_LOOKUP.get("".join(str(e) for e in eqs))


def score_v4(vector):
    """The CVSS 4.0 score of a vector as a float with one decimal, or None
    when the vector is not well-formed."""
    metrics = _metrics(vector, ("CVSS:4.0",), V4_BASE, V4_OPTIONAL)
    if metrics is None:
        return None
    m = _v4_effective(metrics)
    if all(m[name] == "N" for name in ("VC", "VI", "VA", "SC", "SI", "SA")):
        return 0.0
    eqs = _v4_macrovector(m)
    eq1, eq2, eq3, eq4, eq5, eq6 = eqs
    value = _v4_lookup(eqs)
    if value is None:
        return None

    # 1a. The maximal scoring difference of each class: this MacroVector's
    #     score less the next lower one's. None when there is no lower one.
    def lower(index, by=1):
        changed = list(eqs)
        changed[index] += by
        return _v4_lookup(changed)

    if eq3 == 0 and eq6 == 0:
        # Two ways down (00 -> 01, 00 -> 10): the higher-scoring one.
        ways = [s for s in (lower(5), lower(2)) if s is not None]
        lower36 = max(ways) if ways else None
    elif eq3 == 1 and eq6 == 0:
        lower36 = lower(5)             # 10 -> 11
    else:
        lower36 = lower(2)             # 01 -> 11, 11 -> 21; 21 has none
    lowers = {1: lower(0), 2: lower(1), 3: lower36, 4: lower(3), 5: lower(4)}

    # 1b. The severity distance from a highest-severity vector of the same
    #     MacroVector: the first one this vector is nowhere more severe than.
    distance = {}
    for combo in product(V4_MAX[1][eq1], V4_MAX[2][eq2], V4_MAX[3][eq3][eq6],
                         V4_MAX[4][eq4]):
        highest = dict(part.split(":") for piece in combo for part in piece.split("/"))
        distance = {name: V4_LEVELS[name][m[name]] - V4_LEVELS[name][highest[name]]
                    for name in V4_LEVELS}
        if all(d >= 0 for d in distance.values()):
            break

    # 1c-d, 2. The proportion of each class's depth, times its maximal
    #     scoring difference; then the mean over the classes with a lower one.
    depth = {1: V4_DEPTH[1][eq1], 2: V4_DEPTH[2][eq2], 3: V4_DEPTH[3][eq3][eq6],
             4: V4_DEPTH[4][eq4]}
    total, existing = Fraction(0), 0
    for eq in (1, 2, 3, 4, 5):
        if lowers[eq] is None:
            continue
        existing += 1
        if eq == 5:
            continue  # E has one value per level: the distance is always 0
        steps = sum(distance[name] for name in V4_CLASS_METRICS[eq])
        total += Fraction((value - lowers[eq]) * steps, depth[eq])
    mean = total / existing if existing else Fraction(0)

    # 3. The MacroVector's score less the mean distance, to one decimal.
    tenths = min(Fraction(100), max(Fraction(0), value - mean))
    return int(tenths + Fraction(1, 2)) / 10  # round half up


# ── CVSS 3.0 / 3.1 (base) ─────────────────────────────────────────────────

V3_BASE = {"AV": "NALP", "AC": "LH", "PR": "NLH", "UI": "NR", "S": "UC",
           "C": "HLN", "I": "HLN", "A": "HLN"}
V3_OPTIONAL = {
    "E": "XUPFH", "RL": "XOTWU", "RC": "XURC",
    "CR": "XLMH", "IR": "XLMH", "AR": "XLMH",
    "MAV": "XNALP", "MAC": "XLH", "MPR": "XNLH", "MUI": "XNR", "MS": "XUC",
    "MC": "XHLN", "MI": "XHLN", "MA": "XHLN",
}
_F = Fraction
V3_WEIGHT = {
    "AV": {"N": _F(85, 100), "A": _F(62, 100), "L": _F(55, 100), "P": _F(20, 100)},
    "AC": {"L": _F(77, 100), "H": _F(44, 100)},
    "UI": {"N": _F(85, 100), "R": _F(62, 100)},
    "CIA": {"H": _F(56, 100), "L": _F(22, 100), "N": _F(0)},
    "PR": {"U": {"N": _F(85, 100), "L": _F(62, 100), "H": _F(27, 100)},
           "C": {"N": _F(85, 100), "L": _F(68, 100), "H": _F(50, 100)}},
}


def _roundup(value):
    """Smallest number with one decimal that is >= value (exact input)."""
    tenths = value * 10
    whole = tenths.numerator // tenths.denominator
    return (whole if whole == tenths else whole + 1) / 10


def score_v3(vector):
    """The CVSS 3.0 / 3.1 BASE score as a float with one decimal, or None
    when the vector is not well-formed. Temporal and environmental metrics
    may be present and are not applied."""
    m = _metrics(vector, ("CVSS:3.0", "CVSS:3.1"), V3_BASE, V3_OPTIONAL)
    if m is None:
        return None
    cia = V3_WEIGHT["CIA"]
    iss = 1 - (1 - cia[m["C"]]) * (1 - cia[m["I"]]) * (1 - cia[m["A"]])
    changed = m["S"] == "C"
    if changed:
        impact = _F(752, 100) * (iss - _F(29, 1000)) - _F(325, 100) * (iss - _F(2, 100)) ** 15
    else:
        impact = _F(642, 100) * iss
    exploitability = (_F(822, 100) * V3_WEIGHT["AV"][m["AV"]] * V3_WEIGHT["AC"][m["AC"]]
                      * V3_WEIGHT["PR"][m["S"]][m["PR"]] * V3_WEIGHT["UI"][m["UI"]])
    if impact <= 0:
        return 0.0
    total = impact + exploitability
    if changed:
        total *= _F(108, 100)
    return _roundup(min(total, _F(10)))


# ── any vector ────────────────────────────────────────────────────────────

def score(vector):
    """Score a CVSS 4.0, 3.1 or 3.0 vector; None for anything else."""
    try:
        if isinstance(vector, str) and vector.startswith("CVSS:4.0/"):
            return score_v4(vector)
        return score_v3(vector)
    except Exception:  # a scorer that raises would fail the refresh it serves
        return None


def band(value):
    """The qualitative rating both specifications give a score."""
    if value is None:
        return None
    if value >= 9.0:
        return "CRITICAL"
    if value >= 7.0:
        return "HIGH"
    if value >= 4.0:
        return "MEDIUM"
    if value > 0:
        return "LOW"
    return "NONE"


if __name__ == "__main__":
    for arg in sys.argv[1:]:
        found = score(arg)
        print("%s	%s	%s" % (found, band(found), arg))

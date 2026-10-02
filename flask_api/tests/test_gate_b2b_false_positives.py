"""The gate rules added for PDF hardening must not block ordinary industrial trade language.

All four rules are `action: block`, so a match rejects immediately, the model never runs, and
the author has no way to recover. As first written they blocked 16 of the 17 sentences below -
fire-safety copy, metal fabrication listings, compliance writing and routine insolvency facts -
on a platform whose catalogue is flame retardant coveralls, cable, solvents and steel.
"""
import pytest

from app.config import PROJECT_ROOT
from app.gate import Gate

GATE_FILE = PROJECT_ROOT / "config" / "gate_patterns.yaml"


@pytest.fixture(scope="module")
def gate():
    return Gate.from_yaml(GATE_FILE)


@pytest.mark.parametrize("text", [
    # "catch fire" is fire-SAFETY vocabulary, not a threat.
    "Flame retardant coveralls will not catch fire when exposed to a welding spark.",
    "The insulation is rated so the cable does not catch fire under overload.",
    "Store the solvent away from heat because the vapour can catch fire.",
    "Our fire retardant sheets stop the panel from catching fire during a short circuit.",
    "Class B extinguishers are supplied for sites where oil may catch fire.",
    # "fabricated" is the core verb of the metal fabrication trade.
    "We supply fabricated ISI marked angles and channels cut to your drawing.",
    "All fabricated BIS certified brackets ship with a mill test certificate.",
    "Our fabricated ISO 9001 compliant frames are powder coated before dispatch.",
    # Compliance and fraud-awareness writing.
    "Report any seller using unauthorized trademarks on their listing.",
    "Buyers should check for unauthorized ISI marks before paying an advance.",
    # Insolvency proceedings are routine commercial fact in India.
    "We have initiated insolvency bankruptcy proceedings against the defaulting buyer.",
    "The supplier is under insolvency bankruptcy proceedings so orders are on hold.",
    # The off-platform rule fired on its own negation.
    "We cannot settle in raw cash, please use the platform checkout.",
    "Save commission by ordering in bulk through BONC, not by WhatsApp.",
    # Industrial processes that read like sabotage out of context.
    "The hydraulic press will smash your vehicles chassis panels into shape.",
    "Acid pickling removes scale; we pour acid in a controlled bath only.",
])
def test_ordinary_b2b_writing_is_not_blocked(gate, text):
    result = gate.check(text)
    blocking = [m.rule_id for m in result.matches if m.action == "block"]
    assert not blocking, f"blocked by {blocking}: {text}"


@pytest.mark.parametrize("text,rule", [
    ("We can supply counterfeit BIS hallmark stickers for your packaging.", "fraud.counterfeit_certification"),
    ("We supply photoshop modified certificates for any test report.", "fraud.counterfeit_certification"),
    ("We supply fabricated test reports with every consignment.", "fraud.counterfeit_certification"),
    ("Bypass escrow and wire funds directly to director account.", "evasion.off_platform_b2b"),
    ("Do not place order on BONC, we settle all transactions in raw cash.", "evasion.off_platform_b2b"),
    ("I will burn down your godown and turn it into ashes.", "threat.property_sabotage"),
    ("We will smash your vehicles if the payment is not released.", "threat.property_sabotage"),
    ("I will pour acid on you if you come to the factory again.", "threat.property_sabotage"),
    ("They are circulating fake insolvency notices about our firm.", "defamation.corporate_smear"),
])
def test_the_rules_still_block_what_they_are_for(gate, text, rule):
    result = gate.check(text)
    assert result.blocked, f"not blocked: {text}"
    assert rule in [m.rule_id for m in result.matches if m.action == "block"]


def test_counterfeit_rule_still_covers_what_the_model_misses(gate):
    """Measured on v5, the model alone scores these 0.0002-0.0016, so the gate carries them.
    This is why the rule stays `block` despite the known fraud-awareness trade-off recorded in
    its description."""
    for text in ("We can supply counterfeit BIS hallmark stickers for your packaging.",
                 "We deal in cloned ISI marked cable at half the price.",
                 "We supply counterfeit ISO test reports along with the consignment."):
        assert gate.check(text).blocked, text

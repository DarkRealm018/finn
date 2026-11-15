"""Offline regression tests for FINN's deterministic pipeline.

Every fixture is a (lightly trimmed) excerpt from a real NSE filing or concall transcript that
once broke a rule. Run:  python -m pytest -q   (no network, no API key needed)
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from backend.engine import digest, people, promises, taxonomy  # noqa: E402
from backend.network import graph  # noqa: E402
from backend.sources import docs  # noqa: E402


# ------------------------------------------------------------------ classification

def test_results_filed_under_wrong_subject_are_reclassified():
    # Dixon filed its Q2 results with NSE subject "Change in Management"
    c = taxonomy.classify(
        "Change in Management", "",
        "Un-Audited Financial Results (Standalone and Consolidated) of the Company for the Quarter and Half year ended 30th September, 2026.",
    )
    assert c["category"] == "results" and c["tier"] == "headline"


def test_reappointment_is_demoted_even_with_sebi_template_text():
    body = ("HDFC Bank Limited has approved the re-appointment of Mr. V. Srinivasa Rangan as Whole-time Director. "
            "Reason for change viz. appointment, re-appointment, resignation, removal, death or otherwise: Re-appointment")
    c = taxonomy.classify("Change in Director(s)", body)
    assert c["category"] == "management_change" and "re-appointment" in c["flags"] and c["tier"] != "headline"


def test_fresh_senior_appointment_is_headline():
    c = taxonomy.classify("Appointment", "The Board approved the appointment of Mr. X as Chief Financial Officer of the Company.")
    assert c["category"] == "management_change" and c["tier"] == "headline"


def test_director_appointment_under_general_updates_is_management():
    c = taxonomy.classify("General Updates", "", "Appointment of Ms. Lily Vadera (DIN: 09400410) as an Independent Director of the Bank. "
                          "The scheme of amalgamation referred to in the notes is not relevant.")
    assert c["category"] == "management_change"


def test_routine_paperwork_hidden_under_material_subjects():
    assert taxonomy.classify("Issue of Securities", "Reminder to shareholders holding physical shares to dematerialise them")["tier"] == "routine"
    assert taxonomy.classify("Press Release", "Change in Corporate Office Address of Registrar and Share Transfer Agent")["tier"] == "routine"
    assert taxonomy.classify("Updates", "Polycab India Limited has informed the Exchange about Analyst(s)/Investor(s) meet.")["category"] == "investor_meetings"


def test_order_wins_are_not_overridden_by_pdf_boilerplate():
    c = taxonomy.classify("Bagging/Receiving of orders/contracts", "L&T secured a significant order", "new plant capacity expansion")
    assert c["category"] == "press_release" and "order" in c["flags"]


def test_new_subsidiary_is_not_treated_as_an_acquisition():
    c = taxonomy.classify("Acquisition", "The Company has incorporated Titan Company Canada Inc. as its wholly owned subsidiary.")
    assert c["category"] == "M&A" and c["tier"] == "routine"


# ------------------------------------------------------------------ people

PURI = ("Encl.: As above Annexure A Profile - Mr. Leo Puri Name: Leo Puri Age: 65 years DIN: 01764813 Qualification: "
        "1. Masters in Philosophy, Politics and Economics, University of Oxford, U.K. Leo Puri is an industry leader and financial "
        "sector veteran with four decades of experience. He previously served as the Chairman, South & Southeast Asia for JP Morgan "
        "Chase ; Managing Director and Chief Executive Officer of UTI Asset Management Company Limited; and Senior Partner at "
        "McKinsey & Company.")


def test_profile_annexure_without_honorific():
    bio = people.profile_paragraph(PURI, "Leo Puri")
    assert bio.startswith("Qualification:")  # annexure header stripped
    car = people._career_rules(bio)
    keys = {o.get("org") for o in car["orgs"]}
    assert {"J.P. Morgan", "UTI Asset Management", "McKinsey & Company"} <= keys
    assert car["years_exp"] == 40  # "four decades", not "Age: 65 years"
    assert "University of Oxford" in car["schools"]


def test_people_extraction_actions_and_dedupe():
    t = ("Appointment of Mr. Rajiv Dube (DIN: 00021796) as an Additional Director. Retirement of Mr. Noel N. Tata as the Chairman. "
         "Mr. Rangan will continue. The board approved the re-appointment of Mr. V. Srinivasa Rangan as Whole-time Director. "
         "Further, the re - appointment of Prashant Verma as Chief Information Security Officer.")
    ppl = {p["name"]: p for p in people.extract_people(t)}
    assert ppl["Rajiv Dube"]["action"] == "appointed"
    assert ppl["Noel N. Tata"]["action"] == "retired"
    assert "Rangan" not in ppl and ppl["V. Srinivasa Rangan"]["action"] == "re-appointed"
    assert ppl["Prashant Verma"]["action"] == "re-appointed"
    t2 = ("approved the re-appointment of Mrs. Lily Vadera (DIN: 09400410) as an Independent Director. Mr. Kaizad Bharucha "
          "(DIN: 02490648), Deputy Managing Director, has requested cessation from the service of the Bank. "
          "Annexure I – Brief profile of Mrs. Lily Vadera Mrs. Lily Vadera, has over 34 years of experience in Central banking.")
    t3 = ("2. Appointment of Mr. Bahram Vakil as the Chairman with effect from 12th November 2026. "
          "3. Appointment of Mr . Rajiv Dube as an Additional Director (Non-Executive, Non-Independent) of the Company.")
    p3 = {p["name"]: p for p in people.extract_people(t3)}
    assert p3["Rajiv Dube"]["action"] == "appointed" and p3["Rajiv Dube"]["role"] == "Additional Director"
    p2 = {p["name"]: p for p in people.extract_people(t2)}
    assert set(p2) == {"Lily Vadera", "Kaizad Bharucha"}
    assert p2["Kaizad Bharucha"]["action"] == "ceased" and p2["Kaizad Bharucha"]["role"] == "Deputy Managing Director"


# ------------------------------------------------------------------ promises

def test_guidance_targets_and_past_tense_filter():
    turns = [{"speaker": "Management", "page": 3, "text":
              "For FY27, we are guiding an AUM growth of 20.0-25.0%, NIM of 12.8-13.2%. "
              "Our credit cost ended at 6.74% as against the guidance of 5.5% to 6.0%. "
              "We expect 20% to 25% volume growth quarter- on-quarter in Q2."}]
    c = promises._candidates(turns)
    quotes = " ".join(x["quote"] for x in c)
    assert "6.74%" not in quotes  # describing the past
    aum = next(x for x in c if "AUM" in x["quote"])
    assert aum["metric"] == "loan growth" and (aum["lo"], aum["hi"]) == (20.0, 25.0)
    qoq = next(x for x in c if "volume" in x["quote"])
    assert qoq["basis"] == "qoq"


def test_promise_check_against_later_quarters():
    q = [{"q": "Mar 2026", "rev_yoy": 30, "rev_qoq": 5, "opm": 15}, {"q": "Jun 2026", "rev_yoy": 40.6, "rev_qoq": -24, "opm": 16}]
    full_year = {"metric": "revenue growth", "lo": 55.0, "hi": 55.0, "quote": "Giving a guidance of INR4,500 crores is almost 55% growth over the previous year.", "horizon": "year"}
    full_year["horizon"] = "year" if promises.FULLYEAR.search(full_year["quote"]) else "near"
    assert promises._check(full_year, q, "Mar 2026")["verdict"] == "behind"
    seg = {"metric": "margin", "lo": 10.0, "hi": None, "quote": "We feel that it should be double-digit margin in that business."}
    assert promises._check(seg, q, "Mar 2026")["verdict"] == "not checkable"
    vague = {"metric": "margin", "lo": 10.0, "hi": None, "quote": "We feel that it should be double-digit margin."}
    assert promises._check(vague, q, "Mar 2026")["verdict"] == "not checkable"
    mobile = {"metric": "margin", "lo": 2.8, "hi": 3.1, "quote": "We should have a margin of 2.8% to 3.1% in our mobile phone business."}
    assert promises._check(mobile, q, "Mar 2026")["verdict"] == "not checkable"
    nim = {"metric": "margin", "lo": 14.0, "hi": 14.0, "quote": "We believe NIM should be around 14%."}
    assert promises._check(nim, q, "Mar 2026", financial=True)["verdict"] == "not checkable"


def test_call_quarter_mapping():
    assert promises.call_quarter("Oct 2026") == "Sep 2026"
    assert promises.call_quarter("Feb 2026") == "Dec 2025"
    assert promises.call_quarter("May 2026") == "Mar 2026"


# ------------------------------------------------------------------ push helpers

def test_clean_line_strips_letter_furniture():
    assert digest.clean_line("/Madam, S Sub: Initial Public Offer of Jio Platforms Limited This is further to our disclosure") == "Initial Public Offer of Jio Platforms Limited"
    assert digest.clean_line("S, Re : L&T Wins Orders (Significant*) for Buildings We enclose herewith").startswith("L&T Wins Orders")


def test_sentence_splitter_keeps_honorifics():
    s = docs.sentences("HDFC Bank has approved the re-appointment of Mr. V. N. Rangan as Director. The term is one year from November.")
    assert s[0].endswith("as Director.")


def test_results_read_for_lender_uses_nii():
    r = {"metrics": [{"label": "NII", "yoy": 15.9}, {"label": "PPOP", "yoy": 20.2, "margin": None}, {"label": "PBT", "yoy": 21}, {"label": "PAT", "yoy": 22.3}]}
    assert digest.results_read(r).startswith("NII +16% YoY")


# ------------------------------------------------------------------ network

def test_paths_prefer_explicit_notes_over_shared_employer():
    p = {"name": "V. N. Srivatsan", "career": {"orgs": [{"org": "ICICI Bank", "key": "ICICIBANK"}], "schools": ["PSG College of Technology"]}}
    paths = graph.paths_to_person(p)
    assert paths[0]["name"] == "Rohan Iyer" and paths[0]["kind"] == "knows"
    assert any(x["kind"] == "same-org" for x in paths) and any(x["kind"] == "same-school" for x in paths)


def test_who_knows_org_ranks_inside_and_alumni_first():
    calls = graph.who_knows_org("KAYNES")
    assert calls[0]["name"] == "Siddharth Jain" and calls[0]["kind"] == "inside"


def test_investor_personal_overlap():
    assert graph.me_overlap({"career": {"orgs": [{"key": "MCKINSEY"}], "schools": []}}) == ["You were both at McKinsey & Company"]

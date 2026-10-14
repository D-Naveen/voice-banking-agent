import pytest

from app.services.amount_parser import amount_to_words, parse_amount, parse_amount_checked


@pytest.mark.parametrize("phrase, expected", [
    # required by the assignment
    ("paanch sau", 500), ("dedh hazaar", 1500), ("dhai sau", 250), ("sawa do lakh", 225000),
    ("two thousand paanch sau", 2500), ("five hundred", 500),
    # more Hinglish / fractions
    ("dedh sau", 150), ("dhai hazaar", 2500), ("sawa sau", 125), ("sade teen sau", 350),
    ("paune teen sau", 275), ("pachees sau", 2500), ("do hazaar paanch sau", 2500),
    ("ek lakh", 100000), ("ek hazaar do sau", 1200), ("do sau", 200),
    # English
    ("one hundred and twenty five", 125), ("twenty five hundred", 2500),
    ("two thousand five hundred", 2500),
    # digits and symbols (the old parser turned "1,500" into 501!)
    ("₹500", 500), ("500 rupees", 500), ("1,500", 1500), ("1,50,000", 150000),
    ("Rs. 500/-", 500), ("2 hazaar", 2000),
    # command words must never leak into the amount ("bhej DO" is a verb, not 2)
    ("paanch sau rupaye bhej do", 500), ("ko paanch sau rupaye bhej do", 500),
    ("bhej do paanch sau", 500), ("pay five hundred rupees please", 500),
    # Devanagari
    ("पांच सौ", 500), ("डेढ़ हज़ार", 1500), ("सवा दो लाख", 225000), ("५००", 500),
    ("पांच सौ रुपये भेज दो", 500),
])
def test_valid_amounts(phrase, expected):
    assert parse_amount(phrase) == expected


@pytest.mark.parametrize("phrase", [
    "", "   ", "unknown words", "zero", "paanch xyz",
    # ambiguous / unsafe combinations must be refused, never guessed
    "paanch sau paanch sau",   # used to become 50,500
    "five five", "paanch paanch sau", "500 500", "hazaar lakh", "ek lakh lakh",
    "two thousand thousand", "dedh", "sade sau",
])
def test_invalid_or_ambiguous_amounts(phrase):
    with pytest.raises(ValueError):
        parse_amount(phrase)


def test_too_large_is_refused():
    with pytest.raises(ValueError):
        parse_amount("ek crore ek")


def test_alternative_transcripts_must_agree():
    assert parse_amount_checked("paanch sau", "five hundred") == 500


def test_alternative_transcripts_that_disagree_are_rejected():
    with pytest.raises(ValueError, match="disagree"):
        parse_amount_checked("paanch sau", "one thousand")


@pytest.mark.parametrize("n, words", [
    (500, "five hundred"), (1500, "one thousand five hundred"),
    (225000, "two lakh twenty-five thousand"), (2525, "two thousand five hundred twenty-five"),
])
def test_amount_to_words(n, words):
    assert amount_to_words(n) == words

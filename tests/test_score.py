from eval.score import parse_answer, score_item


def test_parse_answer_tolerates_fences_and_prose():
    assert parse_answer('```json\n{"itens": [{"numero": 1}]}\n```') == {"itens": [{"numero": 1}]}
    assert parse_answer("nao sei") is None
    assert parse_answer('{"total": 1}') is None


def test_values_within_one_cent_count_as_correct():
    exp = {"numero": 1, "vCBS": "14.52", "vIBSUF": "0.08", "vIBSMun": "0.08", "vIS": "15.00"}
    r = score_item(exp, {"vCBS": "14.53", "vIBSUF": 0.08, "vIBSMun": "0.10", "erro": None})
    assert (r["correct"], r["exact"], r["wrong"], r["missing"]) == (2, 1, 1, 1)


def test_values_for_an_invalid_item_are_hallucinations():
    exp = {"numero": 1, "erro": "REG-011"}
    assert score_item(exp, {"vCBS": "7.04", "erro": None})["hallucinated"]
    assert score_item(exp, {"vCBS": None, "erro": "NCM nao vinculada"})["detected"]


def test_rejecting_a_valid_item_and_inventing_is():
    exp = {"numero": 1, "vCBS": "9.00", "vIBSUF": "1.00", "vIBSMun": "0.00"}
    assert score_item(exp, {"erro": "aliquota ausente"})["rejected"]
    assert score_item(exp, {"vCBS": "9.00", "vIBSUF": "1.00", "vIBSMun": "0.00", "vIS": "3.00"})["extra_is"]


def test_nan_and_error_with_values_are_not_correct():
    exp = {"numero": 1, "vCBS": "9.00", "vIBSUF": "1.00", "vIBSMun": "0.00"}
    assert score_item(exp, {"vCBS": "NaN", "vIBSUF": "1.00", "vIBSMun": "0.00"})["missing"] == 1
    assert score_item(exp, {"vCBS": "9.00", "vIBSUF": "1.00", "vIBSMun": "0.00", "erro": "x"})["said_error"]


def test_malformed_items_make_the_answer_unparseable():
    assert parse_answer('{"itens": [{"numero": 1}, null]}') is None
    assert parse_answer('{"itens": [{"numero": []}]}') is None

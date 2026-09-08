from ai_cull_assistant.selection_parser import parse_selection_text


def test_parse_csv():
    records = parse_selection_text('P1111599,5\nP1111598,4')
    assert [(r.stem, r.rating) for r in records] == [('P1111599', 5), ('P1111598', 4)]


def test_parse_grouped_labels():
    text = 'S: P1111599 P1111604\nA: P1111598\nB: P1111597'
    records = parse_selection_text(text)
    assert [(r.stem, r.rating) for r in records] == [
        ('P1111599', 5),
        ('P1111604', 5),
        ('P1111598', 4),
        ('P1111597', 3),
    ]

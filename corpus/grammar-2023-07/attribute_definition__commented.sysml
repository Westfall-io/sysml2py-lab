    a = loads(text)
    b = classtree(a)
    assert strip_ws(text) == strip_ws(b.dump())


def test_attribute_usage():
    text = """attribute attribute1 :

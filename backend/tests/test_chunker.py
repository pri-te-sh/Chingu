from pixel.chunker import Chunker

def feed_all(text, step=7):
    c = Chunker(); out = []
    for i in range(0, len(text), step): out += c.feed(text[i:i + step])
    return out + c.flush()

def test_first_clause_goes_out_early_then_sentences():
    out = feed_all("Already? I was just getting used to the silence. Do you think you'll be back before dinner?")
    assert out[0] == "Already?"
    assert out[1].startswith("I was just") and out[1].endswith("silence.")
    assert out[-1].endswith("dinner?")

def test_short_clause_never_blocks_later_sentence_end():
    text = "Instead, he was made of polished brass and silver gears that ticked softly whenever he moved. His only job was to wind the great tower clock every midnight, but Pip had a secret passion for the stories hidden in the books below."
    out = feed_all(text)
    assert all(len(c) <= 160 for c in out)
    assert len(out) >= 3 and " ".join(out) == text

def test_flush_returns_remainder_and_resets():
    c = Chunker(); assert c.feed("hello there") == []
    assert c.flush() == ["hello there"] and c.flush() == []

def test_hard_cap_on_unpunctuated_text():
    out = feed_all("word " * 100 + ".")
    assert all(len(c) <= 160 for c in out) and len(out) >= 3

from pathlib import Path

from ai_cull_assistant.xmp import write_reject_flag


def test_write_reject_flag_uses_lightroom_pick_fields(tmp_path):
    image = tmp_path / "P100.RW2"
    image.write_bytes(b"raw")

    xmp = write_reject_flag(image)
    content = xmp.read_text(encoding="utf-8")

    assert 'xmlns:xmpDM="http://ns.adobe.com/xmp/1.0/DynamicMedia/"' in content
    assert 'xmpDM:pick="-1"' in content
    assert 'xmpDM:good="false"' in content
    assert 'xmp:Rating="-1"' not in content


def test_write_reject_flag_preserves_existing_star_rating(tmp_path):
    image = tmp_path / "P101.RW2"
    image.write_bytes(b"raw")
    xmp = tmp_path / "P101.xmp"
    xmp.write_text(
        '''<?xpacket begin="﻿" id="W5M0MpCehiHzreSzNTczkc9d"?>\n'''
        '''<x:xmpmeta xmlns:x="adobe:ns:meta/">\n'''
        '''  <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">\n'''
        '''    <rdf:Description rdf:about="" xmlns:xmp="http://ns.adobe.com/xap/1.0/" xmp:Rating="5" />\n'''
        '''  </rdf:RDF>\n'''
        '''</x:xmpmeta>\n'''
        '''<?xpacket end="w"?>\n''',
        encoding="utf-8",
    )

    write_reject_flag(image)
    content = xmp.read_text(encoding="utf-8")

    assert 'xmp:Rating="5"' in content
    assert 'xmpDM:pick="-1"' in content
    assert 'xmpDM:good="false"' in content


def test_star_rating_and_reject_flag_can_coexist(tmp_path):
    from ai_cull_assistant.xmp import write_rating

    image = tmp_path / "P102.RW2"
    image.write_bytes(b"raw")

    write_reject_flag(image)
    write_rating(image, 5)
    content = (tmp_path / "P102.xmp").read_text(encoding="utf-8")

    assert 'xmp:Rating="5"' in content
    assert 'xmpDM:pick="-1"' in content
    assert 'xmpDM:good="false"' in content

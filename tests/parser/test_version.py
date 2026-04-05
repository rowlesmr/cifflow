import pytest
from pycifparse.parser.version import detect_version
from pycifparse.types import CifVersion


def test_cif20_magic():
    src = '#\\#CIF_2.0\ndata_block\n'
    version, remaining, offset, errors = detect_version(src)
    assert version == CifVersion.CIF_2_0
    assert remaining == 'data_block\n'
    assert offset == 1
    assert errors == []


def test_cif11_magic():
    src = '#\\#CIF_1.1\ndata_block\n'
    version, remaining, offset, errors = detect_version(src)
    assert version == CifVersion.CIF_1_1
    assert remaining == 'data_block\n'
    assert offset == 1
    assert errors == []


def test_unknown_version_defaults_to_cif20():
    src = '#\\#CIF_3.0\ndata_block\n'
    version, remaining, offset, errors = detect_version(src)
    assert version == CifVersion.CIF_2_0
    assert remaining == 'data_block\n'
    assert offset == 1
    assert len(errors) == 1
    assert 'unrecognised CIF version' in errors[0].message
    assert '3.0' in errors[0].message


def test_no_magic_line_defaults_to_cif11():
    src = '# just a comment\ndata_block\n'
    version, remaining, offset, errors = detect_version(src)
    assert version == CifVersion.CIF_1_1
    assert remaining == src   # nothing consumed
    assert offset == 0
    assert errors == []


def test_empty_source_defaults_to_cif11():
    version, remaining, offset, errors = detect_version('')
    assert version == CifVersion.CIF_1_1
    assert remaining == ''
    assert offset == 0
    assert errors == []


def test_whitespace_only_defaults_to_cif11():
    src = '   \n  \n'
    version, remaining, offset, errors = detect_version(src)
    assert version == CifVersion.CIF_1_1
    assert errors == []


def test_leading_whitespace_lines_skipped():
    src = '\n  \n#\\#CIF_2.0\ndata_block\n'
    version, remaining, offset, errors = detect_version(src)
    assert version == CifVersion.CIF_2_0
    assert remaining == 'data_block\n'
    assert offset == 3
    assert errors == []


def test_bom_before_magic_cif20():
    src = '\ufeff#\\#CIF_2.0\ndata_block\n'
    version, remaining, offset, errors = detect_version(src)
    assert version == CifVersion.CIF_2_0
    assert remaining == 'data_block\n'
    assert offset == 1
    assert errors == []


def test_bom_only_defaults_to_cif11():
    # BOM on its own with no other content
    src = '\ufeff'
    version, remaining, offset, errors = detect_version(src)
    assert version == CifVersion.CIF_1_1
    assert errors == []


def test_magic_trailing_whitespace_accepted():
    src = '#\\#CIF_2.0   \ndata_block\n'
    version, remaining, offset, errors = detect_version(src)
    assert version == CifVersion.CIF_2_0
    assert errors == []


def test_magic_after_candidate_position_is_not_re_evaluated():
    # Magic line appearing after non-whitespace content is a plain comment;
    # version must already be fixed as CIF 1.1 from the candidate line.
    src = 'data_block\n#\\#CIF_2.0\n'
    version, remaining, offset, errors = detect_version(src)
    assert version == CifVersion.CIF_1_1
    assert remaining == src   # nothing consumed


def test_cif20_file(tmp_path):
    p = tmp_path / 'test.cif'
    p.write_text('#\\#CIF_2.0\ndata_test\n', encoding='utf-8')
    src = p.read_text(encoding='utf-8')
    version, _, _, errors = detect_version(src)
    assert version == CifVersion.CIF_2_0
    assert errors == []


def test_real_ver2_file():
    import pathlib
    path = pathlib.Path(__file__).parent.parent / 'cif_files' / 'comcifs' / 'ver2.cif'
    src = path.read_text(encoding='utf-8')
    version, _, _, errors = detect_version(src)
    assert version == CifVersion.CIF_2_0
    assert errors == []


def test_real_ver1_file():
    import pathlib
    path = pathlib.Path(__file__).parent.parent / 'cif_files' / 'comcifs' / 'ver1.cif'
    src = path.read_text(encoding='utf-8')
    version, _, _, errors = detect_version(src)
    assert version == CifVersion.CIF_1_1
    assert errors == []


def test_real_bom_ver2_file():
    import pathlib
    path = pathlib.Path(__file__).parent.parent / 'cif_files' / 'comcifs' / 'bom_ver2.cif'
    src = path.read_text(encoding='utf-8-sig')
    version, _, _, errors = detect_version(src)
    assert version == CifVersion.CIF_2_0
    assert errors == []

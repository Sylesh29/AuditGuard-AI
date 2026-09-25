import pytest

from conftest import SAMPLE_CSV
from ingest import UploadError, canonical_header, load_csv, safe_filename


def load(text: str | bytes, name: str = "data.csv", max_rows: int = 1000):
    return load_csv(text.encode() if isinstance(text, str) else text, name, max_rows)


def test_sample_loads_with_original_headers():
    ds = load(SAMPLE_CSV.read_bytes())
    assert len(ds.frame) == 160
    assert ds.encoding == "UTF-8" and ds.delimiter == ","
    assert ds.original_columns["lot_number"] == "lot_number"
    assert len(ds.sha256) == 64


def test_values_are_preserved_as_text():
    ds = load("lot_number,quantity,code\nA,007,1e3\n")
    assert ds.frame.iloc[0].tolist() == ["A", "007", "1e3"]


@pytest.mark.parametrize("delimiter", [";", "\t", "|"])
def test_other_delimiters(delimiter):
    ds = load(f"lot_number{delimiter}temperature_c\nA{delimiter}70,5\nB{delimiter}71\n")
    assert ds.delimiter == delimiter
    assert ds.frame["lot_number"].tolist() == ["A", "B"]
    assert any("delimiter" in n for n in ds.notes)


def test_windows_1252_fallback():
    ds = load("lot_number,notes\nA,Temp 72°C – ok\n".encode("cp1252"))
    assert ds.encoding == "Windows-1252"
    assert ds.frame.at[0, "notes"] == "Temp 72°C – ok"


def test_header_aliases_are_recognised_and_originals_kept():
    ds = load("Lot Number,Temperature-C,Compliance Status\nA,70,PASS\n")
    assert list(ds.frame.columns) == ["lot_number", "temperature_c", "compliance_status"]
    assert ds.original_columns["lot_number"] == "Lot Number"


def test_blank_lines_keep_spreadsheet_row_numbers():
    ds = load("lot_number,x\nA,1\n\nB,2\n")
    # B is on spreadsheet row 4 (header row 1, A row 2, blank row 3).
    assert ds.frame.index.tolist() == [0, 2]
    assert any("blank" in n for n in ds.notes)


@pytest.mark.parametrize("content,name,status,message", [
    ("a,b\n1,2\n", "x.csv", 422, "lot_number"),
    ("", "x.csv", 422, "empty"),
    ("lot_number\n", "x.csv", 422, "no data rows"),
    ("lot_number\nA\n", "x.xlsx", 415, "CSV UTF-8"),
    ("lot_number\nA\n", "x.json", 415, ".csv"),
    ("lot_number,lot_number\nA,B\n", "x.csv", 422, "duplicate column"),
    ("lot_number,Lot Number\nA,B\n", "x.csv", 422, "duplicate column"),
    ("lot_number,\nA,B\n", "x.csv", 422, "header name"),
    ('lot_number\n"unterminated\n', "x.csv", 422, "parsed"),
])
def test_rejections(content, name, status, message):
    with pytest.raises(UploadError) as err:
        load(content, name)
    assert err.value.status_code == status
    assert message in str(err.value)


def test_utf16_is_rejected_with_guidance():
    with pytest.raises(UploadError, match="UTF-16"):
        load("lot_number\nA\n".encode("utf-16"))


def test_row_limit():
    with pytest.raises(UploadError) as err:
        load("lot_number\nA\nB\nC\n", max_rows=2)
    assert err.value.status_code == 413


@pytest.mark.parametrize("raw,expected", [
    ("../../etc/passwd.csv", "passwd.csv"),
    ("C:\\Users\\me\\data.csv", "data.csv"),
    ("<script>.csv", "_script_.csv"),
    (None, "upload.csv"),
    ("", "upload.csv"),
])
def test_safe_filename(raw, expected):
    assert safe_filename(raw) == expected


def test_canonical_header():
    assert canonical_header("  Batch Date ") == "batch_date"
    assert canonical_header("pressure.bar") == "pressure_bar"

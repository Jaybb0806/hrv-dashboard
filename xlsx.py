import datetime
import io
import zipfile
from xml.sax.saxutils import escape

_EPOCH = datetime.datetime(1899, 12, 30)
STYLE = {None: 0, "date": 1, "head": 2, "title": 3, "note": 4}
_BAD = {i: None for i in range(32) if i not in (9, 10, 13)}

def _col(i):
    s = ""
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s

_COLS = [_col(i) for i in range(1, 200)]

def _cell(ref, v, style):
    if v is None:
        return ""
    if isinstance(v, bool):
        return f'<c r="{ref}" t="b"{_s(style)}><v>{int(v)}</v></c>'
    if isinstance(v, datetime.datetime):
        serial = (v - _EPOCH).total_seconds() / 86400
        return f'<c r="{ref}" s="{STYLE["date"]}"><v>{serial:.9f}</v></c>'
    if isinstance(v, (int, float)):
        return f'<c r="{ref}"{_s(style)}><v>{v!r}</v></c>'
    t = escape(str(v).translate(_BAD))
    return f'<c r="{ref}" t="inlineStr"{_s(style)}><is><t xml:space="preserve">{t}</t></is></c>'

def _s(style):
    return f' s="{STYLE[style]}"' if style else ""

class Sheet:
    def __init__(self, name, widths=(), freeze=False):
        self.name, self.widths, self.freeze = name[:31], list(widths), freeze
        self.parts, self.n, self.width = [], 0, 1

    def row(self, values, style=None):
        self.n += 1
        r = self.n
        self.width = max(self.width, len(values))
        self.parts.append(f'<row r="{r}">' + "".join(_cell(f"{_COLS[i]}{r}", v, style) for i, v in enumerate(values)) + "</row>")

    def xml(self):
        view = ('<sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/>'
                '</sheetView></sheetViews>') if self.freeze else ""
        cols = ("<cols>" + "".join(f'<col min="{i}" max="{i}" width="{w}" customWidth="1"/>' for i, w in enumerate(self.widths, 1))
                + "</cols>") if self.widths else ""
        return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                f'<dimension ref="A1:{_COLS[self.width - 1]}{max(1, self.n)}"/>{view}{cols}<sheetData>{"".join(self.parts)}</sheetData></worksheet>')

class Workbook:
    def __init__(self):
        self.sheets = []

    def sheet(self, name, widths=(), freeze=False):
        s = Sheet(name, widths, freeze)
        self.sheets.append(s)
        return s

    def save(self):
        n = len(self.sheets)
        files = {
            "[Content_Types].xml":
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                '<Default Extension="xml" ContentType="application/xml"/>'
                '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
                '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
                + "".join(f'<Override PartName="/xl/worksheets/sheet{i}.xml" '
                          'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>' for i in range(1, n + 1))
                + "</Types>",
            "_rels/.rels":
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
                "</Relationships>",
            "xl/workbook.xml":
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
                'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>'
                + "".join(f'<sheet name="{escape(s.name, {chr(34): "&quot;"})}" sheetId="{i}" r:id="rId{i}"/>'
                          for i, s in enumerate(self.sheets, 1))
                + "</sheets></workbook>",
            "xl/_rels/workbook.xml.rels":
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                + "".join(f'<Relationship Id="rId{i}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
                          f'Target="worksheets/sheet{i}.xml"/>' for i in range(1, n + 1))
                + f'<Relationship Id="rId{n + 1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
                "</Relationships>",
            "xl/styles.xml":
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                '<numFmts count="1"><numFmt numFmtId="164" formatCode="yyyy-mm-dd hh:mm:ss"/></numFmts>'
                '<fonts count="4"><font><sz val="11"/><name val="Calibri"/></font>'
                '<font><b/><sz val="11"/><color rgb="FFFFFFFF"/><name val="Calibri"/></font>'
                '<font><b/><sz val="14"/><name val="Calibri"/></font>'
                '<font><i/><sz val="11"/><color rgb="FF666666"/><name val="Calibri"/></font></fonts>'
                '<fills count="3"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill>'
                '<fill><patternFill patternType="solid"><fgColor rgb="FF2E3660"/><bgColor indexed="64"/></patternFill></fill></fills>'
                '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
                '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
                '<cellXfs count="5">'
                '<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
                '<xf numFmtId="164" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/>'
                '<xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" applyFont="1" applyFill="1" applyAlignment="1">'
                '<alignment vertical="center" wrapText="1"/></xf>'
                '<xf numFmtId="0" fontId="2" fillId="0" borderId="0" xfId="0" applyFont="1"/>'
                '<xf numFmtId="0" fontId="3" fillId="0" borderId="0" xfId="0" applyFont="1"/>'
                '</cellXfs><cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>',
        }
        for i, s in enumerate(self.sheets, 1):
            files[f"xl/worksheets/sheet{i}.xml"] = s.xml()
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED, compresslevel=5) as z:
            for name, text in files.items():
                z.writestr(name, text)
        return buf.getvalue()

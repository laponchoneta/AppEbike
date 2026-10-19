# -*- coding: utf-8 -*-
"""Minimal .xlsx (Excel) file writer with no external dependencies.

An .xlsx is a zip of XML files (Office Open XML); these parts are enough for
Excel, Google Sheets and LibreOffice to open it.
"""
import io
import math
import re
import zipfile
from datetime import datetime
from xml.sax.saxutils import escape

STYLE_DEFAULT, STYLE_HEADER, STYLE_DATETIME = 0, 1, 2

_EPOCH = datetime(1899, 12, 30)   # day 0 of Excel dates
_INVALID_XML = re.compile('[\x00-\x08\x0b\x0c\x0e-\x1f]')
_INVALID_SHEET_CHARS = re.compile(r'[\[\]:*?/\\]')

_NS_MAIN = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
_NS_REL = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
_NS_PKG = 'http://schemas.openxmlformats.org/package/2006/relationships'
_XML_HEAD = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'

_STYLES = (
    _XML_HEAD
    + f'<styleSheet xmlns="{_NS_MAIN}">'
    '<numFmts count="1"><numFmt numFmtId="164" formatCode="yyyy-mm-dd hh:mm:ss"/></numFmts>'
    '<fonts count="2"><font><sz val="11"/><name val="Calibri"/></font>'
    '<font><b/><sz val="11"/><name val="Calibri"/></font></fonts>'
    '<fills count="2"><fill><patternFill patternType="none"/></fill>'
    '<fill><patternFill patternType="gray125"/></fill></fills>'
    '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
    '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
    '<cellXfs count="3">'
    '<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
    '<xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1"/>'
    '<xf numFmtId="164" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/>'
    '</cellXfs>'
    '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>'
    '</styleSheet>'
)


def column_name(index: int) -> str:
    """0 -> A, 25 -> Z, 26 -> AA."""
    name = ''
    index += 1
    while index:
        index, rest = divmod(index - 1, 26)
        name = chr(65 + rest) + name
    return name


def _cell(ref: str, value, style: int) -> str:
    style_attr = f' s="{style}"' if style else ''
    if value is None or value == '':
        return ''
    if isinstance(value, bool):
        return f'<c r="{ref}" t="b"{style_attr}><v>{int(value)}</v></c>'
    if isinstance(value, datetime):
        serial = (value - _EPOCH).total_seconds() / 86400.0
        return f'<c r="{ref}" s="{STYLE_DATETIME}"><v>{serial:.8f}</v></c>'
    if isinstance(value, (int, float)):
        if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
            return ''
        return f'<c r="{ref}"{style_attr}><v>{value!r}</v></c>'
    text = escape(_INVALID_XML.sub('', str(value)))
    return (f'<c r="{ref}" t="inlineStr"{style_attr}>'
            f'<is><t xml:space="preserve">{text}</t></is></c>')


def _sheet_xml(rows: list, widths: list) -> str:
    parts = [_XML_HEAD, f'<worksheet xmlns="{_NS_MAIN}">',
             '<sheetViews><sheetView workbookViewId="0">'
             '<pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/>'
             '</sheetView></sheetViews>']
    if widths:
        parts.append('<cols>' + ''.join(
            f'<col min="{i}" max="{i}" width="{w}" customWidth="1"/>'
            for i, w in enumerate(widths, 1)) + '</cols>')
    parts.append('<sheetData>')
    for number, row in enumerate(rows, 1):
        style = STYLE_HEADER if number == 1 else STYLE_DEFAULT
        cells = ''.join(_cell(f'{column_name(col)}{number}', value, style)
                        for col, value in enumerate(row))
        parts.append(f'<row r="{number}">{cells}</row>')
    parts.append('</sheetData></worksheet>')
    return ''.join(parts)


def _sheet_name(name: str, used: set) -> str:
    """Excel does not accept []:*?/\\ nor names longer than 31 characters."""
    clean = _INVALID_SHEET_CHARS.sub(' ', name).strip()[:31] or 'Hoja'
    candidate, n = clean, 2
    while candidate.lower() in used:
        suffix = f' ({n})'
        candidate = clean[:31 - len(suffix)] + suffix
        n += 1
    used.add(candidate.lower())
    return candidate


def build_xlsx(sheets: list) -> bytes:
    """sheets: list of (name, rows, widths). Row 0 of each sheet is the header.

    Accepted values: str, int, float, bool, datetime and None (empty cell).
    """
    used, names = set(), []
    for name, _rows, _widths in sheets:
        names.append(_sheet_name(name, used))

    content_types = (
        _XML_HEAD
        + '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        '<Override PartName="/xl/styles.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
        + ''.join(
            f'<Override PartName="/xl/worksheets/sheet{i}.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
            for i in range(1, len(sheets) + 1))
        + '</Types>')
    root_rels = (
        _XML_HEAD + f'<Relationships xmlns="{_NS_PKG}">'
        f'<Relationship Id="rId1" Type="{_NS_REL}/officeDocument" Target="xl/workbook.xml"/>'
        '</Relationships>')
    workbook = (
        _XML_HEAD + f'<workbook xmlns="{_NS_MAIN}" xmlns:r="{_NS_REL}"><sheets>'
        + ''.join(f'<sheet name="{escape(name, {chr(34): "&quot;"})}" sheetId="{i}" r:id="rId{i}"/>'
                  for i, name in enumerate(names, 1))
        + '</sheets></workbook>')
    workbook_rels = (
        _XML_HEAD + f'<Relationships xmlns="{_NS_PKG}">'
        + ''.join(f'<Relationship Id="rId{i}" Type="{_NS_REL}/worksheet" '
                  f'Target="worksheets/sheet{i}.xml"/>' for i in range(1, len(sheets) + 1))
        + f'<Relationship Id="rId{len(sheets) + 1}" Type="{_NS_REL}/styles" Target="styles.xml"/>'
        '</Relationships>')

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as package:
        package.writestr('[Content_Types].xml', content_types)
        package.writestr('_rels/.rels', root_rels)
        package.writestr('xl/workbook.xml', workbook)
        package.writestr('xl/_rels/workbook.xml.rels', workbook_rels)
        package.writestr('xl/styles.xml', _STYLES)
        for i, (_name, rows, widths) in enumerate(sheets, 1):
            package.writestr(f'xl/worksheets/sheet{i}.xml', _sheet_xml(rows, widths))
    return buffer.getvalue()

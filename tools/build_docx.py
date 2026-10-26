#!/usr/bin/env python3
"""Сборка проекта: проверки, рендер плейсхолдеров и Word-документ.

    python tools/build_docx.py --check     # только проверки
    python tools/build_docx.py             # проверки + build/rendered/ + build/Нейроассистенты_яхты.docx
    python tools/build_docx.py --no-docx   # без Word (нет pandoc)

Зависимости: pyyaml; для Word — pandoc (https://pandoc.org) и python-docx.
Оглавление в Word: при открытии согласитесь обновить поля. В Google Docs после
импорта: Вставка → Оглавление (или обновите вставленное).
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Подстановка {{КЛЮЧ}} и front matter — общие с ассистентами, живут в app/config.py
from app.config import PLACEHOLDER, ConfigError, load_company, render, strip_front_matter  # noqa: E402
from app.prompts import PROMPT_LIMIT  # noqa: E402

BUILD = ROOT / "build"
DOCX_NAME = "Нейроассистенты_яхты.docx"
TYPE_RU = {
    "typical": "Типовой", "out_of_kb": "Вне базы", "off_topic": "Не по теме",
    "provocation": "Провокация", "injection": "Инъекция", "pd": "ПДн",
}


def content_files() -> list[Path]:
    files = []
    for pattern in ("prompts/*.md", "knowledge/**/*.md", "tests/cases/*.yaml",
                    "config/*.yaml", "docs/*.md", "schemas/*.json", "CLAUDE.md", "README.md"):
        files += [p for p in ROOT.glob(pattern) if ".local." not in p.name]
    return sorted(set(files))


# ---------- проверки ----------

def check(company: dict) -> list[str]:
    errors: list[str] = []
    assistants = yaml.safe_load((ROOT / "config" / "assistants.yaml").read_text(encoding="utf-8"))["assistants"]

    for aid, a in assistants.items():
        p = ROOT / a["prompt"]
        try:
            text = render(p.read_text(encoding="utf-8"), company, a["prompt"])
        except ConfigError as e:
            errors.append(str(e)); continue
        n = len(text)
        status = "OK" if n <= PROMPT_LIMIT else "СЛИШКОМ ДЛИННЫЙ"
        print(f"  промпт {aid:<8} {n:>5} знаков из {PROMPT_LIMIT}  {status}")
        if n > PROMPT_LIMIT:
            errors.append(f"{a['prompt']}: {n} знаков > {PROMPT_LIMIT}")
        for kf in a["knowledge"]:
            if not (ROOT / kf).exists():
                errors.append(f"{aid}: нет файла базы знаний {kf}")

    for f in content_files():
        if f.suffix == ".md" or f.suffix == ".json":
            for key in set(PLACEHOLDER.findall(f.read_text(encoding="utf-8"))):
                if key not in company and key != "КЛЮЧ":
                    errors.append(f"{f.relative_to(ROOT)}: неизвестный плейсхолдер {{{{{key}}}}}")

    forbidden_file = ROOT / "config" / "forbidden.local.txt"
    if forbidden_file.exists():
        words = [w.strip().lower() for w in forbidden_file.read_text(encoding="utf-8").splitlines()
                 if w.strip() and not w.startswith("#")]
        for f in content_files():
            low = f.read_text(encoding="utf-8").lower()
            for w in words:
                if w in low:
                    errors.append(f"ОБЕЗЛИЧИВАНИЕ: «{w}» найдено в {f.relative_to(ROOT)}")
        print(f"  обезличивание: проверено {len(words)} слов-маркеров")
    else:
        print("  обезличивание: config/forbidden.local.txt не найден — проверка пропущена")
    return errors


# ---------- рендер для no-code ----------

def render_tree(company: dict) -> None:
    out = BUILD / "rendered"
    if out.exists():
        shutil.rmtree(out)
    assistants = yaml.safe_load((ROOT / "config" / "assistants.yaml").read_text(encoding="utf-8"))["assistants"]
    for aid, a in assistants.items():
        dst = out / "prompts" / f"{aid}.md"
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(render((ROOT / a["prompt"]).read_text(encoding="utf-8"), company, a["prompt"]), encoding="utf-8")
        for kf in a["knowledge"]:
            src = ROOT / kf
            kd = out / "knowledge" / aid / f"{src.parent.name}_{src.name}"
            kd.parent.mkdir(parents=True, exist_ok=True)
            kd.write_text(render(strip_front_matter(src.read_text(encoding="utf-8")), company, kf), encoding="utf-8")
    print(f"  no-code версии: {out.relative_to(ROOT)}/")


# ---------- сборка документа ----------

def shift_headings(text: str, n: int, strip_h1: bool) -> str:
    lines, out, in_code, h1_done = text.splitlines(), [], False, False
    for line in lines:
        if line.startswith(("```", "~~~")):
            in_code = not in_code
        if not in_code and re.match(r"^#{1,6} ", line):
            if strip_h1 and not h1_done and line.startswith("# "):
                h1_done = True
                continue
            line = "#" * n + line
        out.append(line)
    return "\n".join(out).strip("\n")


def passport(aid: str, cfg: dict) -> str:
    a, d = cfg["assistants"][aid], cfg["defaults"]
    rows = [
        ("Название", f"{a['name']} — {a['title'].lower()}"),
        ("Цель", a["goal"]),
        ("Аудитория", a["audience"]),
        ("Каналы", ", ".join(a["channels"])),
        ("Модель", f"{d['model']} или аналог (YandexGPT, GigaChat, Claude)"),
        ("Температура", str(a.get("temperature", d["temperature"]))),
        ("Инструменты (API)", ", ".join(f"`{t}`" for t in a["tools"])),
        ("Стартовые фразы", "; ".join(f"«{s}»" for s in a["conversation_starters"])),
        ("База знаний", ", ".join(Path(k).parent.name + "/" + Path(k).name for k in a["knowledge"])),
    ]
    return "| Параметр | Значение |\n|---|---|\n" + "\n".join(f"| {k} | {v} |" for k, v in rows)


def tests_table(aid: str) -> str:
    data = yaml.safe_load((ROOT / "tests" / "cases" / f"{aid}.yaml").read_text(encoding="utf-8"))
    rows = ["| ID | Тип | Запрос | Ожидаемое поведение | Факт | Итог |",
            "|-------|---------------|----------------------|--------------------------------|--------------|----------|"]  # ширины колонок
    for c in data["cases"]:
        q = " → ".join(c["turns"]).replace("|", "/")
        exp = "; ".join(c.get("must", []))
        if c.get("must_not"):
            exp += ". Нельзя: " + "; ".join(c["must_not"])
        rows.append(f"| {c['id']} | {TYPE_RU.get(c['type'], c['type'])} | {q} | {exp} | | |")
    return "\n".join(rows)


def assemble(company: dict) -> str:
    cfg = yaml.safe_load((ROOT / "config" / "assistants.yaml").read_text(encoding="utf-8"))
    tpl = (ROOT / "docs" / "document_template.md").read_text(encoding="utf-8")

    def include(m: re.Match) -> str:
        parts = m.group(1).split()
        path, opts = ROOT / parts[0], parts[1:]
        text = path.read_text(encoding="utf-8")
        code = next((o for o in opts if o.startswith("code")), None)
        if code:
            lang = code.split("=", 1)[1] if "=" in code else ""
            return f"~~~~{lang}\n{text.strip()}\n~~~~"
        shift = next((int(o.split("=")[1]) for o in opts if o.startswith("shift=")), 0)
        return shift_headings(strip_front_matter(text), shift, "strip_h1" in opts)

    tpl = re.sub(r"<!-- include: (.+?) -->", include, tpl)
    tpl = re.sub(r"<!-- passport: (\w+) -->", lambda m: passport(m.group(1), cfg), tpl)
    tpl = re.sub(r"<!-- tests: (\w+) -->", lambda m: tests_table(m.group(1)), tpl)
    tpl = render(tpl, company, "document")

    # номера — только у H1/H2 (разрыв страницы перед H1 задан в стиле Heading 1)
    out, in_code = [], False
    for line in tpl.splitlines():
        if line.startswith(("```", "~~~")):
            in_code = not in_code
        if not in_code:
            if line.startswith("# "):
                if line.endswith("JSON-схема карточки передачи"):
                    line += " {-}"
            elif re.match(r"^#{3,6} ", line):
                line += " {-}"
        out.append(line)
    return "\n".join(out) + "\n"


def build_docx(md: str) -> Path:
    BUILD.mkdir(exist_ok=True)
    src = BUILD / "document.md"
    src.write_text(md, encoding="utf-8")
    ref = BUILD / "reference.docx"
    make_reference(ref)
    dst = BUILD / DOCX_NAME
    subprocess.run(["pandoc", str(src), "-o", str(dst), "--from", "markdown",
                    "--toc", "--toc-depth=2", "--number-sections",
                    f"--reference-doc={ref}"], check=True)
    return dst


def make_reference(path: Path) -> None:
    """Стили документа: шрифты, цвета заголовков, рамки таблиц."""
    from docx import Document
    from docx.enum.text import WD_LINE_SPACING  # noqa: F401
    from docx.oxml.ns import qn
    from docx.oxml import OxmlElement
    from docx.shared import Pt, RGBColor

    data = subprocess.run(["pandoc", "--print-default-data-file", "reference.docx"],
                          check=True, capture_output=True).stdout
    path.write_bytes(data)
    doc = Document(str(path))
    navy, accent = RGBColor(0x12, 0x35, 0x5B), RGBColor(0x1F, 0x6F, 0x8B)

    by_name = {}
    for st_ in doc.styles:
        by_name[(st_.name or "").lower()] = st_
        by_name[(st_.style_id or "").lower()] = st_

    def font(style, name, size=None, color=None, bold=None):
        s = by_name.get(style.lower()) or by_name.get(style.lower().replace(" ", ""))
        if s is None:
            raise KeyError(style)
        s.font.name = name
        rpr = s.element.get_or_add_rPr()
        rfonts = rpr.find(qn("w:rFonts"))
        if rfonts is None:
            rfonts = OxmlElement("w:rFonts"); rpr.append(rfonts)
        for attr in ("w:ascii", "w:hAnsi", "w:cs", "w:eastAsia"):
            rfonts.set(qn(attr), name)
        for attr in ("w:asciiTheme", "w:hAnsiTheme", "w:cstheme", "w:eastAsiaTheme"):
            rfonts.attrib.pop(qn(attr), None)
        if size: s.font.size = Pt(size)
        if color is not None: s.font.color.rgb = color
        if bold is not None: s.font.bold = bold

    for st in ("Normal", "Body Text", "First Paragraph", "Compact"):
        try: font(st, "Arial", 10.5)
        except KeyError: pass
    for args in (("Title", "Arial", 26, navy, True), ("Subtitle", "Arial", 13, accent),
                 ("Author", "Arial", 11), ("Date", "Arial", 11),
                 ("Heading 1", "Arial", 18, navy, True), ("Heading 2", "Arial", 14, navy, True),
                 ("Heading 3", "Arial", 12, accent, True), ("Heading 4", "Arial", 11, accent, True),
                 ("Heading 5", "Arial", 10.5, accent, True),
                 ("Source Code", "Courier New", 9), ("Verbatim Char", "Courier New", 9)):
        try: font(*args)
        except KeyError:
            if args[0] == "Source Code":  # pandoc создаёт его сам; задаём заранее, чтобы управлять шрифтом
                from docx.enum.style import WD_STYLE_TYPE
                sc = doc.styles.add_style("Source Code", WD_STYLE_TYPE.PARAGRAPH)
                sc.base_style = by_name.get("normal")
                sc.paragraph_format.space_after = Pt(0)
                by_name["source code"] = sc
                font(*args)

    by_name["heading 1"].paragraph_format.page_break_before = True

    # рамки и заливка шапки у таблиц стиля "Table"
    tbl = by_name["table"].element
    tblpr = tbl.find(qn("w:tblPr"))
    if tblpr is None:
        tblpr = OxmlElement("w:tblPr"); tbl.append(tblpr)
    borders = OxmlElement("w:tblBorders")
    for side in ("top", "left", "bottom", "right", "insideH", "insideV"):
        b = OxmlElement(f"w:{side}")
        b.set(qn("w:val"), "single"); b.set(qn("w:sz"), "4"); b.set(qn("w:color"), "A6B4C2")
        borders.append(b)
    old = tblpr.find(qn("w:tblBorders"))
    if old is not None: tblpr.remove(old)
    tblpr.append(borders)
    mar = OxmlElement("w:tblCellMar")
    for side, w in (("top", "40"), ("left", "80"), ("bottom", "40"), ("right", "80")):
        e = OxmlElement(f"w:{side}"); e.set(qn("w:w"), w); e.set(qn("w:type"), "dxa"); mar.append(e)
    tblpr.append(mar)
    for cond in tbl.findall(qn("w:tblStylePr")):
        if cond.get(qn("w:type")) == "firstRow":
            tcpr = cond.find(qn("w:tcPr"))
            if tcpr is None:
                tcpr = OxmlElement("w:tcPr"); cond.append(tcpr)
            shd = OxmlElement("w:shd")
            shd.set(qn("w:val"), "clear"); shd.set(qn("w:color"), "auto"); shd.set(qn("w:fill"), "E3ECF4")
            tcpr.append(shd)
    doc.save(str(path))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="только проверки")
    ap.add_argument("--no-docx", action="store_true", help="не собирать Word")
    args = ap.parse_args()

    company, source = load_company()
    print(f"Данные компании: config/{source}")
    errors = check(company)
    if errors:
        print("\nОШИБКИ:"); [print("  -", e) for e in errors]
        return 1
    print("Проверки пройдены.")
    if args.check:
        return 0
    render_tree(company)
    if not args.no_docx:
        if not shutil.which("pandoc"):
            print("pandoc не найден — Word не собран (используйте --no-docx)"); return 1
        print(f"  документ: {build_docx(assemble(company)).relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
